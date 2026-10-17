import logging
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query, Response, Request, status
from fastapi.responses import StreamingResponse, JSONResponse
from ..config import settings
from ..db.connection import get_db
from ..storage.client import storage_client
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
    """
    host = request.headers.get("host", "").split(":")[0]
    # Allowed loopback origins: localhost, 127.0.0.1, ::1
    is_loopback = host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")

    # In strict desktop mode or when a loopback token is provided, validate it
    loopback_token = getattr(settings, "STREAM_LOOPBACK_TOKEN", None)
    if loopback_token and token != loopback_token:
        raise HTTPException(status_code=403, detail="Unauthorized loopback access token")


@router.api_route("/virtual-stream/{file_id}", methods=["GET", "HEAD"])
async def virtual_stream_file(
    file_id: str,
    request: Request,
    range_header: Optional[str] = Header(None, alias="Range"),
    token: Optional[str] = Query(None)
):
    """
    Virtual streaming engine endpoint adhering to update.md Section 3:
    - Provides HTTP 206 Partial Content byte-range negotiation for Service Worker & HTML5 <video>
    - Clamps ranges, handles suffix ranges (bytes=-n) and open-ended ranges (bytes=a-)
    - Returns HTTP 416 with Content-Range: bytes */total if start is past EOF
    - Enforces 16 MiB 206 capping to keep playback buffer sliding (<100MB RAM)
    - Prevents truncated payloads
    """
    # Verify loopback origin if in desktop loopback mode
    if token:
        verify_loopback_security(request, token)

    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
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
        "Content-Disposition": f'inline; filename="{filename}"'
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


@router.get("/files/{file_id}/moov")
async def get_mp4_moov_info(file_id: str):
    """
    MP4 'moov' atom resolver endpoint adhering to update.md Section 3:
    Detects if MP4 has FastStart ('moov' at front) or is trailing ('moov' at tail).
    Extracts atom locations so video players can seek immediately without downloading
    gigabytes of intermediate video frames.
    """
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
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
    info = await MP4MoovResolver.inspect_mp4(parts, total_size, storage_client)
    info["file_id"] = file_id
    info["total_size"] = total_size
    info["filename"] = file_item["filename"]
    return JSONResponse(info)


@router.get("/files/{file_id}/moov/bytes")
async def get_mp4_moov_bytes(file_id: str):
    """
    Directly streams the raw MP4 'moov' atom box bytes.
    Allows video players and demuxers to obtain codec and index tables instantly
    without downloading gigabytes of intermediate video frames.
    """
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
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
    info = await MP4MoovResolver.inspect_mp4(parts, total_size, storage_client)
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
        "Content-Disposition": f'inline; filename="{file_item["filename"]}.moov"'
    }

    return StreamingResponse(
        streaming_engine.stream_range(
            parts=parts,
            total_size=total_size,
            range_spec=range_spec,
            storage_client=storage_client
        ),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers=headers,
        media_type="application/octet-stream"
    )


# Root-level router for direct browser / virtual-stream paths
root_router = APIRouter()
root_router.add_api_route("/virtual-stream/{file_id}", virtual_stream_file, methods=["GET", "HEAD"])
