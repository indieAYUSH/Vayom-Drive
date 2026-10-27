import ipaddress
import logging
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query, Response, Request, status
from fastapi.responses import StreamingResponse, JSONResponse
from ..api.routes import format_content_disposition, is_upload_incomplete
from ..config import settings
from ..db.connection import get_db
from ..storage.client import storage_client
from ..transcoding.encoder import PROXY_PROFILE, PROXY_MAX_HEIGHT
from .engine import (
    streaming_engine,
    parse_and_clamp_range,
    RangeSpec,
    DEFAULT_MAX_206_BYTES,
    MP4MoovResolver
)

logger = logging.getLogger("vayom.streaming.router")
router = APIRouter(prefix="/api/v1")


def verify_loopback_security(request: Request, token: Optional[str] = None):
    """
    Enforces localhost loopback security invariant from update.md Section 0:
    'Bind to 127.0.0.1 only. Require a random token in the URL path and reject any Host header that isn’t the loopback origin.'
    Validates both the real socket client IP (request.client.host) and Host request header.
    """
    # 1. Validate real socket client IP (request.client.host)
    if request.client and getattr(request.client, "host", None):
        client_host = request.client.host.strip().lower()
        is_loopback_client = (
            client_host in ("127.0.0.1", "::1", "localhost", "testclient", "testserver")
            or client_host.startswith("127.")
        )
        if not is_loopback_client:
            try:
                ip = ipaddress.ip_address(client_host)
                is_loopback_client = ip.is_loopback
            except ValueError:
                is_loopback_client = False
        if not is_loopback_client:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: non-loopback client IP origin"
            )

    # 2. Validate Host request header (bracket-aware port strip: "[::1]:8000" -> "::1", B16)
    host_header = request.headers.get("host", "").strip().lower()
    host = host_header.split("]")[0].lstrip("[") if host_header.startswith("[") else host_header.split(":")[0]
    is_loopback_host = host in ("localhost", "testserver", "testclient", "::1")
    if not is_loopback_host:
        try:
            ip = ipaddress.ip_address(host)
            is_loopback_ip = ip.is_loopback
        except ValueError:
            # Prefix match would accept DNS-rebinding names like "127.evil.com"
            is_loopback_ip = False
        is_loopback_host = is_loopback_ip
    if not is_loopback_host:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: non-loopback host origin"
        )

    # 3. In strict desktop mode or when a loopback token is configured, validate it
    loopback_token = getattr(settings, "STREAM_LOOPBACK_TOKEN", "")
    if loopback_token and token != loopback_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized loopback access token"
        )


@router.api_route("/virtual-stream/{file_id}", methods=["GET", "HEAD"])
async def virtual_stream_file(
    file_id: str,
    request: Request,
    range_header: Optional[str] = Header(None, alias="Range"),
    token: Optional[str] = Query(None),
    proxy: Optional[bool] = Query(False)
):
    """
    Virtual streaming engine endpoint adhering to update.md Section 3:
    - Provides HTTP 206 Partial Content byte-range negotiation for Service Worker & HTML5 <video>
    - Clamps ranges, handles suffix ranges (bytes=-n) and open-ended ranges (bytes=a-)
    - Returns HTTP 416 with Content-Range: bytes */total if start is past EOF
    - Enforces 16 MiB 206 capping to keep playback buffer sliding (<100MB RAM)
    - Prevents truncated payloads
    """
    # Verify loopback origin and token security
    verify_loopback_security(request, token)

    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM files WHERE id = ? AND is_deleted = 0", (file_id,))
        file_row = await cursor.fetchone()
        if not file_row:
            raise HTTPException(status_code=404, detail="File not found")

        file_item = dict(file_row)

        # If proxy stream requested and ready, resolve the CURRENT proxy —
        # prefer the active profile/height so a legacy large proxy is never
        # mistaken for the current one (B7; mirrors list_files selection).
        if proxy and file_item.get("kind") == "original":
            p_cur = await db.execute(
                """
                SELECT * FROM files
                WHERE proxy_of = ? AND is_deleted = 0
                  AND upload_status = 'completed' AND kind = 'proxy'
                ORDER BY CASE WHEN proxy_profile = ? AND (height IS NULL OR height <= ?)
                              THEN 0 ELSE 1 END,
                         created_at DESC
                LIMIT 1
                """,
                (file_id, PROXY_PROFILE, PROXY_MAX_HEIGHT)
            )
            p_row = await p_cur.fetchone()
            if p_row:
                file_item = dict(p_row)
                file_id = file_item["id"]

        p_cursor = await db.execute(
            "SELECT * FROM file_parts WHERE file_id = ? ORDER BY part_index ASC",
            (file_id,)
        )
        parts = [dict(p) for p in await p_cursor.fetchall()]
        if not parts:
            raise HTTPException(status_code=404, detail="File content parts not found")

        # C-4: a short part set must never be streamed as if the file were whole
        # (the response would advertise a full Content-Length and then abort
        # mid-body). Same condition the file-list endpoint exposes.
        if is_upload_incomplete(file_item, parts):
            raise HTTPException(
                status_code=409,
                detail="File upload was interrupted. Resume the upload before streaming."
            )

    total_size = file_item["size_bytes"]
    filename = file_item["filename"]
    mime_type = file_item["mime_type"] or "application/octet-stream"

    # Invariant: Parse & clamp range according to update.md Section 0 & 3
    range_spec = parse_and_clamp_range(range_header, total_size, max_chunk_bytes=DEFAULT_MAX_206_BYTES)

    # 416 Range Not Satisfiable check
    if not range_spec.is_satisfiable:
        headers = {
            "Content-Range": range_spec.content_range_header,
            "Accept-Ranges": "bytes"
        }
        return Response(status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE, headers=headers)

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(range_spec.content_length),
        "Content-Disposition": format_content_disposition(filename)
    }

    if range_spec.is_range:
        headers["Content-Range"] = range_spec.content_range_header
        status_code = status.HTTP_206_PARTIAL_CONTENT
    else:
        status_code = status.HTTP_200_OK

    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers, media_type=mime_type)

    encryption = file_item.get("encryption", "none")
    nonce = file_item.get("nonce")
    key_hex = request.headers.get("x-decryption-key") or request.query_params.get("key")
    key = None
    if key_hex:
        clean_key = key_hex.strip()
        if len(clean_key) == 64:
            try:
                key = bytes.fromhex(clean_key)
            except ValueError:
                pass
    if key is None:
        from ..crypto import vault_manager
        key = vault_manager.get_key()

    if encryption == "ctr" and key is None:
        raise HTTPException(
            status_code=423,
            detail="Vault is locked. Decryption key or passphrase required to decrypt this file."
        )

    return StreamingResponse(
        streaming_engine.stream_range(
            parts=parts,
            total_size=total_size,
            range_spec=range_spec,
            storage_client=storage_client,
            encryption=encryption,
            nonce=nonce,
            key=key
        ),
        status_code=status_code,
        headers=headers,
        media_type=mime_type
    )


def _resolve_decryption_key(request: Request) -> Optional[bytes]:
    """B9: same key resolution as /virtual-stream (x-decryption-key header,
    ?key query, then the unlocked vault key)."""
    key_hex = request.headers.get("x-decryption-key") or request.query_params.get("key")
    if key_hex:
        clean_key = key_hex.strip()
        if len(clean_key) == 64:
            try:
                return bytes.fromhex(clean_key)
            except ValueError:
                pass
    from ..crypto import vault_manager
    return vault_manager.get_key()


@router.get("/files/{file_id}/moov")
async def get_mp4_moov_info(
    file_id: str,
    request: Request,
    token: Optional[str] = Query(None)
):
    """
    MP4 'moov' atom resolver endpoint adhering to update.md Section 3:
    Detects if MP4 has FastStart ('moov' at front) or is trailing ('moov' at tail).
    Extracts atom locations so video players can seek immediately without downloading
    gigabytes of intermediate video frames.
    """
    verify_loopback_security(request, token)

    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM files WHERE id = ? AND is_deleted = 0", (file_id,))
        file_row = await cursor.fetchone()
        if not file_row:
            raise HTTPException(status_code=404, detail="File not found")

        file_item = dict(file_row)
        p_cursor = await db.execute(
            "SELECT * FROM file_parts WHERE file_id = ? ORDER BY part_index ASC",
            (file_id,)
        )
        parts = [dict(p) for p in await p_cursor.fetchall()]

    total_size = file_item["size_bytes"]
    # B9: decrypt probes for CTR-encrypted files; 423 when locked (mirrors /virtual-stream)
    encryption = file_item.get("encryption", "none")
    nonce = file_item.get("nonce")
    key = _resolve_decryption_key(request)
    if encryption == "ctr" and key is None:
        raise HTTPException(
            status_code=423,
            detail="Vault is locked. Decryption key or passphrase required to decrypt this file."
        )
    info = await MP4MoovResolver.inspect_mp4(
        parts, total_size, storage_client, encryption=encryption, nonce=nonce, key=key
    )
    info["file_id"] = file_id
    info["total_size"] = total_size
    info["filename"] = file_item["filename"]
    return JSONResponse(info)


@router.get("/files/{file_id}/moov/bytes")
async def get_mp4_moov_bytes(
    file_id: str,
    request: Request,
    token: Optional[str] = Query(None)
):
    """
    Directly streams the raw MP4 'moov' atom box bytes.
    Allows video players and demuxers to obtain codec and index tables instantly
    without downloading gigabytes of intermediate video frames.
    """
    verify_loopback_security(request, token)

    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM files WHERE id = ? AND is_deleted = 0", (file_id,))
        file_row = await cursor.fetchone()
        if not file_row:
            raise HTTPException(status_code=404, detail="File not found")

        file_item = dict(file_row)
        p_cursor = await db.execute(
            "SELECT * FROM file_parts WHERE file_id = ? ORDER BY part_index ASC",
            (file_id,)
        )
        parts = [dict(p) for p in await p_cursor.fetchall()]

    total_size = file_item["size_bytes"]
    # B9: decrypt probes and the streamed moov box itself for CTR files
    encryption = file_item.get("encryption", "none")
    nonce = file_item.get("nonce")
    key = _resolve_decryption_key(request)
    if encryption == "ctr" and key is None:
        raise HTTPException(
            status_code=423,
            detail="Vault is locked. Decryption key or passphrase required to decrypt this file."
        )
    info = await MP4MoovResolver.inspect_mp4(
        parts, total_size, storage_client, encryption=encryption, nonce=nonce, key=key
    )
    if not info.get("has_moov") or info.get("moov_offset") is None or not info.get("moov_size"):
        raise HTTPException(status_code=404, detail="MP4 moov atom not found in file")

    moov_offset = info["moov_offset"]
    moov_size = info["moov_size"]

    range_spec = RangeSpec(
        start=moov_offset,
        end=moov_offset + moov_size - 1,
        total_size=total_size,
        content_length=moov_size,
        is_range=True,
        is_satisfiable=True,
        is_capped=False
    )

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(moov_size),
        "Content-Range": f"bytes {moov_offset}-{moov_offset + moov_size - 1}/{total_size}",
        "Content-Disposition": format_content_disposition(f"{file_item['filename']}.moov")
    }

    return StreamingResponse(
        streaming_engine.stream_range(
            parts=parts,
            total_size=total_size,
            range_spec=range_spec,
            storage_client=storage_client,
            encryption=encryption,
            nonce=nonce,
            key=key
        ),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers=headers,
        media_type="application/octet-stream"
    )


# Root-level router for direct browser / virtual-stream paths
# F-4: Excluded from OpenAPI schema and marked deprecated to avoid publishing duplicate route surfaces;
# canonical route is /api/v1/virtual-stream/{file_id}.
root_router = APIRouter()
root_router.add_api_route(
    "/virtual-stream/{file_id}",
    virtual_stream_file,
    methods=["GET", "HEAD"],
    include_in_schema=False,
    deprecated=True
)
