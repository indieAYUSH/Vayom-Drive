import json
import uuid
import os
import secrets
import aiofiles
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException, Query, Response, Request, status
from fastapi.responses import StreamingResponse, FileResponse
from ..config import settings
from ..db.connection import get_db
from ..storage.client import storage_client
from ..compression.splitter import split_file_if_needed, calculate_sha256
from ..index.metadata import detect_file_category, generate_webp_thumbnail, extract_image_exif
from ..streaming.engine import streaming_engine, parse_and_clamp_range, DEFAULT_MAX_206_BYTES

router = APIRouter(prefix="/api/v1")

# --- Pydantic Request Models ---
class CreateAlbumRequest(BaseModel):
    name: str
    description: Optional[str] = None

class AddFileToAlbumRequest(BaseModel):
    file_id: str

class CreateShareRequest(BaseModel):
    file_id: str
    hours: int = 72
    has_password: bool = False
    password: Optional[str] = None
    max_views: Optional[int] = None

class BatchFileRequest(BaseModel):
    file_ids: List[str]


# --- Disaster Recovery Logic ---
async def perform_rebuild_index(limit: int = 5000) -> int:
    """
    Scans Telegram storage channel messages backward from HEAD, parses
    VAYOM:v1: caption envelopes, and reconstructs the SQLite files and file_parts catalog.
    Returns the number of restored envelopes.
    """
    envelopes = await storage_client.scan_channel_envelopes(limit=limit)
    restored_count = 0

    async with get_db() as db:
        for env in envelopes:
            file_id = env.get("id")
            if not file_id:
                continue

            # Check if file already exists
            cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
            if not await cur.fetchone():
                await db.execute(
                    """
                    INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id, env.get("name", "recovered_file"),
                        env.get("mime", "application/octet-stream"),
                        env.get("cat", "other"), env.get("size", 0),
                        env.get("sha256", ""),
                        1 if env.get("total", 1) > 1 else 0,
                        env.get("total", 1)
                    )
                )

            # Insert part pointer
            part_id = str(uuid.uuid4())
            await db.execute(
                """
                INSERT OR IGNORE INTO file_parts (
                    id, file_id, part_index, byte_offset, part_size_bytes, 
                    part_sha256, telegram_message_id, telegram_channel_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part_id, file_id, env.get("part", 0), env.get("offset", 0),
                    env.get("size", 0), env.get("sha256", ""), env.get("telegram_message_id"),
                    str(settings.TELEGRAM_CHANNEL_ID)
                )
            )
            restored_count += 1

        await db.commit()

    return restored_count


# --- Health & Metrics ---
@router.get("/health")
async def health_check():
    """Heartbeat endpoint for Hugging Face Spaces and uptime monitors."""
    return {"status": "ok", "app": "Vayom Drive", "storage": "telegram"}

@router.get("/system/metrics")
async def get_metrics():
    """Returns storage metrics, channel stats, and online health."""
    async with get_db() as db:
        # Automatically rebuild index if local SQLite database is empty/fresh
        cur = await db.execute("SELECT COUNT(*) FROM files")
        if (await cur.fetchone())[0] == 0 and storage_client._is_connected:
            await perform_rebuild_index(limit=5000)

        cursor = await db.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM files WHERE is_deleted = 0")
        total_files, total_bytes = await cursor.fetchone()

        cursor = await db.execute(
            """
            SELECT
                COUNT(DISTINCT fp.telegram_message_id),
                COUNT(fp.id)
            FROM file_parts fp
            JOIN files f ON fp.file_id = f.id
            WHERE f.is_deleted = 0
            """
        )
        telegram_messages, total_parts = await cursor.fetchone()

    status_str = storage_client.online_status
    wait_secs = storage_client.flood_wait_seconds

    metrics_res = {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_parts": total_parts,
        "channel_id": settings.TELEGRAM_CHANNEL_ID or "NOT_CONFIGURED",
        "telegram_messages": telegram_messages,
        "online_status": status_str,
    }
    if wait_secs > 0:
        metrics_res["flood_wait_seconds"] = wait_secs
    return metrics_res


# --- Files Endpoints ---
@router.get("/files")
async def list_files(
    category: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    album_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    is_deleted: bool = Query(False),
    limit: int = Query(200),
    offset: int = Query(0)
):
    """Returns filtered, paginated file list with parts, thumbnails, albums, and metadata."""
    async with get_db() as db:
        # Automatically rebuild index if local SQLite database is empty/fresh
        cur = await db.execute("SELECT COUNT(*) FROM files")
        if (await cur.fetchone())[0] == 0 and storage_client._is_connected:
            await perform_rebuild_index(limit=5000)

        query = "SELECT f.* FROM files f"
        joins = []
        where_clauses = ["f.is_deleted = ?"]
        params: list = [1 if is_deleted else 0]

        if album_id:
            joins.append("JOIN file_albums fa ON f.id = fa.file_id")
            where_clauses.append("fa.album_id = ?")
            params.append(album_id)

        if tag:
            joins.append("JOIN file_tags ft ON f.id = ft.file_id JOIN tags t ON ft.tag_id = t.id")
            where_clauses.append("t.name = ?")
            params.append(tag)

        if category and category != "all":
            where_clauses.append("f.file_category = ?")
            params.append(category)

        if q:
            where_clauses.append("(f.filename LIKE ? OR f.id LIKE ?)")
            params.append(f"%{q}%")
            params.append(f"%{q}%")

        full_query = f"{query} {' '.join(joins)} WHERE {' AND '.join(where_clauses)} ORDER BY f.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await db.execute(full_query, params)
        rows = await cursor.fetchall()

        results = []
        for row in rows:
            f_dict = dict(row)
            file_id = f_dict["id"]

            # Fetch parts for this file
            p_cursor = await db.execute(
                "SELECT part_index, byte_offset AS offset, part_size_bytes AS size, part_sha256 AS sha256, telegram_message_id, telegram_channel_id, telegram_file_id "
                "FROM file_parts WHERE file_id = ? ORDER BY part_index ASC",
                (file_id,)
            )
            parts = [dict(p) for p in await p_cursor.fetchall()]
            f_dict["parts"] = parts

            # Fetch tags
            t_cursor = await db.execute(
                "SELECT t.name FROM tags t JOIN file_tags ft ON t.id = ft.tag_id WHERE ft.file_id = ?",
                (file_id,)
            )
            f_dict["tags"] = [r[0] for r in await t_cursor.fetchall()]

            # Fetch albums
            a_cursor = await db.execute(
                "SELECT album_id FROM file_albums WHERE file_id = ?",
                (file_id,)
            )
            f_dict["albums"] = [r[0] for r in await a_cursor.fetchall()]

            # Format booleans and URLs
            f_dict["is_split"] = bool(f_dict.get("is_split"))
            f_dict["is_compressed"] = bool(f_dict.get("is_compressed"))
            f_dict["is_lossy_video"] = bool(f_dict.get("is_lossy_video"))
            f_dict["is_deleted"] = bool(f_dict.get("is_deleted"))

            if f_dict.get("has_thumbnail") and not f_dict.get("is_deleted"):
                f_dict["thumbnail_url"] = f"/api/v1/files/{file_id}/thumbnail"
            else:
                f_dict["thumbnail_url"] = None

            if not f_dict.get("is_deleted"):
                f_dict["preview_url"] = f"/api/v1/files/{file_id}/download"
            else:
                f_dict["preview_url"] = None

            if f_dict.get("exif_json"):
                try:
                    f_dict["exif"] = json.loads(f_dict["exif_json"])
                except Exception:
                    f_dict["exif"] = None

            if f_dict.get("latitude") is not None and f_dict.get("longitude") is not None:
                f_dict["gps"] = {
                    "latitude": f_dict["latitude"],
                    "longitude": f_dict["longitude"]
                }

            results.append(f_dict)

        return results


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...)
):
    """
    Receives file, calculates SHA-256, generates WebP thumbnail,
    slices into parts if > 1.9 GB, streams to Telegram, and commits to SQLite.
    """
    file_id = str(uuid.uuid4())
    temp_dir = settings.UPLOAD_TEMP_DIR / file_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(file.filename).name or f"{file_id}_upload"
    target_path = temp_dir / safe_filename

    # 1. Stream incoming upload to temporary disk
    async with aiofiles.open(target_path, "wb") as out_file:
        while chunk := await file.read(1024 * 1024):
            await out_file.write(chunk)

    total_size = os.path.getsize(target_path)
    sha256 = calculate_sha256(target_path)
    mime_type = file.content_type or "application/octet-stream"
    category = detect_file_category(mime_type, file.filename)

    # 2. Thumbnail & EXIF generation
    thumbnail_path = settings.THUMBNAIL_DIR / f"{file_id}.webp"
    has_thumb = False
    if category == "image":
        has_thumb = generate_webp_thumbnail(target_path, thumbnail_path)
    exif = extract_image_exif(target_path) if category == "image" else {}

    # 3. Size Policy & Pointer-Offset Slicing
    parts = split_file_if_needed(target_path, max_part_bytes=settings.MAX_PART_BYTES)

    # 4. Upload each part to Telegram with Caption Envelope
    uploaded_parts = []
    for part in parts:
        caption = (
            f"VAYOM:v1:{json.dumps({'id': file_id, 'name': file.filename, 'part': part['part_index'], 'total': len(parts), 'offset': part['byte_offset'], 'size': part['part_size_bytes'], 'sha256': part['part_sha256'], 'cat': category})}"
        )
        res = await storage_client.upload_part(part["file_path"], caption=caption)
        if isinstance(res, dict):
            uploaded_parts.append({
                **part,
                "telegram_message_id": res["message_id"],
                "telegram_channel_id": res.get("channel_id", str(settings.TELEGRAM_CHANNEL_ID)),
                "telegram_file_id": res.get("file_id"),
                "telegram_access_hash": res.get("access_hash")
            })
        else:
            uploaded_parts.append({
                **part,
                "telegram_message_id": res,
                "telegram_channel_id": str(settings.TELEGRAM_CHANNEL_ID),
                "telegram_file_id": None,
                "telegram_access_hash": None
            })

    # 5. Commit atomic transaction to SQLite
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (
                id, filename, mime_type, file_category, size_bytes, sha256,
                is_split, total_parts, has_thumbnail, thumbnail_path,
                exif_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id, file.filename, mime_type, category, total_size, sha256,
                1 if len(parts) > 1 else 0, len(parts), 1 if has_thumb else 0,
                str(thumbnail_path) if has_thumb else None,
                json.dumps(exif) if exif else None
            )
        )

        for p in uploaded_parts:
            part_id = str(uuid.uuid4())
            await db.execute(
                """
                INSERT INTO file_parts (
                    id, file_id, part_index, byte_offset, part_size_bytes,
                    part_sha256, telegram_message_id, telegram_channel_id,
                    telegram_file_id, telegram_access_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part_id, file_id, p["part_index"], p["byte_offset"],
                    p["part_size_bytes"], p["part_sha256"], p["telegram_message_id"],
                    p.get("telegram_channel_id", str(settings.TELEGRAM_CHANNEL_ID)),
                    p.get("telegram_file_id"), p.get("telegram_access_hash")
                )
            )

        await db.commit()

    # 6. Cleanup temporary upload files
    try:
        for p in parts:
            if os.path.exists(p["file_path"]):
                os.remove(p["file_path"])
        if target_path.exists():
            os.remove(target_path)
        temp_dir.rmdir()
    except Exception:
        pass

    return {
        "file_id": file_id,
        "filename": file.filename,
        "size_bytes": total_size,
        "sha256": sha256,
        "total_parts": len(parts),
        "status": "confirmed"
    }


@router.api_route("/files/{file_id}/thumbnail", methods=["GET", "HEAD"])
async def get_thumbnail(file_id: str):
    """Serves the ultra-compact WebP thumbnail preview."""
    async with get_db() as db:
        cursor = await db.execute("SELECT is_deleted FROM files WHERE id = ?", (file_id,))
        file_row = await cursor.fetchone()
        if not file_row or file_row["is_deleted"]:
            raise HTTPException(status_code=404, detail="Thumbnail not found")

    thumb = settings.THUMBNAIL_DIR / f"{file_id}.webp"
    if thumb.exists():
        return FileResponse(thumb, media_type="image/webp")
    raise HTTPException(status_code=404, detail="Thumbnail not found")


@router.api_route("/files/{file_id}/download", methods=["GET", "HEAD"])
async def download_file(
    file_id: str,
    request: Request,
    range_header: Optional[str] = Header(None, alias="Range")
):
    """
    Streams file bytes with full HTTP Range request support (206 Partial Content).
    Enables instant seeking in 4K videos without downloading the full file!
    """
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
    filename = file_item["filename"]

    # Parse and clamp range according to update.md invariants
    range_spec = parse_and_clamp_range(range_header, total_size, max_chunk_bytes=DEFAULT_MAX_206_BYTES)

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
        return Response(status_code=status_code, headers=headers, media_type=file_item["mime_type"])

    return StreamingResponse(
        streaming_engine.stream_range(
            parts=parts,
            total_size=total_size,
            range_spec=range_spec,
            storage_client=storage_client
        ),
        status_code=status_code,
        headers=headers,
        media_type=file_item["mime_type"]
    )


@router.delete("/files/{file_id}")
async def delete_file(file_id: str, purge: bool = False):
    """Soft deletes file to 30-day trash, or purges permanently from disk and Telegram."""
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="File not found")

        if purge:
            # 1. Fetch parts before cascade deletion deletes them from file_parts
            p_cur = await db.execute(
                "SELECT telegram_message_id, telegram_channel_id FROM file_parts WHERE file_id = ?",
                (file_id,)
            )
            parts = [dict(r) for r in await p_cur.fetchall()]

            # 2. Permanently delete parts from Telegram channel
            await storage_client.delete_parts(parts)

            # 3. Remove local thumbnail preview
            thumb = settings.THUMBNAIL_DIR / f"{file_id}.webp"
            if thumb.exists():
                try:
                    os.remove(thumb)
                except Exception:
                    pass

            # 4. Remove database record (cascades to file_parts, file_albums, file_tags, share_links)
            await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        else:
            await db.execute(
                "UPDATE files SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (file_id,)
            )
        await db.commit()
    return {"success": True, "purged": purge}


@router.post("/files/{file_id}/restore")
async def restore_file(file_id: str):
    """Restores soft-deleted file from trash."""
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="File not found")
        await db.execute(
            "UPDATE files SET is_deleted = 0, deleted_at = NULL WHERE id = ?",
            (file_id,)
        )
        await db.commit()
    return {"success": True}


# --- Batch Operations ---
@router.post("/files/batch-trash")
async def batch_trash(body: BatchFileRequest):
    """Batch moves files to trash."""
    if not body.file_ids:
        return {"count": 0}
    placeholders = ",".join(["?"] * len(body.file_ids))
    async with get_db() as db:
        await db.execute(
            f"UPDATE files SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
            body.file_ids
        )
        await db.commit()
    return {"count": len(body.file_ids)}


@router.post("/files/batch-restore")
async def batch_restore(body: BatchFileRequest):
    """Batch restores files from trash."""
    if not body.file_ids:
        return {"count": 0}
    placeholders = ",".join(["?"] * len(body.file_ids))
    async with get_db() as db:
        await db.execute(
            f"UPDATE files SET is_deleted = 0, deleted_at = NULL WHERE id IN ({placeholders})",
            body.file_ids
        )
        await db.commit()
    return {"count": len(body.file_ids)}


@router.post("/files/batch-purge")
async def batch_purge(body: BatchFileRequest):
    """Batch permanently purges files from database, local disk, and Telegram channel."""
    if not body.file_ids:
        return {"count": 0, "messages_purged": 0}

    placeholders = ",".join(["?"] * len(body.file_ids))
    async with get_db() as db:
        # 1. Fetch parts before cascading deletion
        p_cur = await db.execute(
            f"SELECT telegram_message_id, telegram_channel_id FROM file_parts WHERE file_id IN ({placeholders})",
            body.file_ids
        )
        parts = [dict(r) for r in await p_cur.fetchall()]

        # 2. Delete messages from Telegram channel
        messages_purged = await storage_client.delete_parts(parts)

        # 3. Delete database records
        await db.execute(
            f"DELETE FROM files WHERE id IN ({placeholders})",
            body.file_ids
        )
        await db.commit()

        # 4. Remove local thumbnails
        for fid in body.file_ids:
            thumb = settings.THUMBNAIL_DIR / f"{fid}.webp"
            if thumb.exists():
                try:
                    os.remove(thumb)
                except Exception:
                    pass

    return {"count": len(body.file_ids), "messages_purged": messages_purged}


@router.post("/files/empty-trash")
async def empty_trash():
    """
    Permanently purges ALL items currently in trash (is_deleted = 1)
    from SQLite, disk thumbnails, and the Telegram storage channel.
    """
    async with get_db() as db:
        # 1. Get all soft-deleted file IDs
        cur = await db.execute("SELECT id FROM files WHERE is_deleted = 1")
        rows = await cur.fetchall()
        deleted_ids = [r[0] for r in rows]

        if not deleted_ids:
            return {"count": 0, "messages_purged": 0}

        placeholders = ",".join(["?"] * len(deleted_ids))

        # 2. Get all message parts for these files
        p_cur = await db.execute(
            f"SELECT telegram_message_id, telegram_channel_id FROM file_parts WHERE file_id IN ({placeholders})",
            deleted_ids
        )
        parts = [dict(r) for r in await p_cur.fetchall()]

        # 3. Delete from Telegram channel
        messages_purged = await storage_client.delete_parts(parts)

        # 4. Delete files from DB
        await db.execute(
            f"DELETE FROM files WHERE id IN ({placeholders})",
            deleted_ids
        )
        await db.commit()

        # 5. Delete thumbnails
        for fid in deleted_ids:
            thumb = settings.THUMBNAIL_DIR / f"{fid}.webp"
            if thumb.exists():
                try:
                    os.remove(thumb)
                except Exception:
                    pass

    return {"count": len(deleted_ids), "messages_purged": messages_purged}


# --- Albums Endpoints ---
@router.get("/albums")
async def list_albums():
    """Lists all albums with file counts."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT a.id, a.name, a.description, a.cover_file_id, a.created_at,
                   COUNT(fa.file_id) AS file_count
            FROM albums a
            LEFT JOIN file_albums fa ON a.id = fa.album_id
            GROUP BY a.id
            ORDER BY a.created_at DESC
            """
        )
        rows = await cursor.fetchall()
        albums = []
        for r in rows:
            alb = dict(r)
            if alb.get("cover_file_id"):
                alb["cover_url"] = f"/api/v1/files/{alb['cover_file_id']}/thumbnail"
            else:
                alb["cover_url"] = None
            albums.append(alb)
        return albums


@router.post("/albums")
async def create_album(body: CreateAlbumRequest):
    """Creates a new album collection."""
    album_id = f"alb-{uuid.uuid4().hex[:12]}"
    async with get_db() as db:
        await db.execute(
            "INSERT INTO albums (id, name, description) VALUES (?, ?, ?)",
            (album_id, body.name, body.description)
        )
        await db.commit()
    return {"id": album_id, "name": body.name, "description": body.description, "file_count": 0}


@router.delete("/albums/{album_id}")
async def delete_album(album_id: str):
    """Deletes an album collection (does not delete underlying files)."""
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM albums WHERE id = ?", (album_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Album not found")
        await db.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        await db.commit()
    return {"success": True}


@router.post("/albums/{album_id}/files")
async def add_file_to_album(album_id: str, body: AddFileToAlbumRequest):
    """Adds a file to an album."""
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO file_albums (file_id, album_id) VALUES (?, ?)",
            (body.file_id, album_id)
        )
        # Update cover file id if not present
        await db.execute(
            "UPDATE albums SET cover_file_id = ? WHERE id = ? AND cover_file_id IS NULL",
            (body.file_id, album_id)
        )
        await db.commit()
    return {"success": True}


@router.delete("/albums/{album_id}/files/{file_id}")
async def remove_file_from_album(album_id: str, file_id: str):
    """Removes a file from an album."""
    async with get_db() as db:
        await db.execute(
            "DELETE FROM file_albums WHERE file_id = ? AND album_id = ?",
            (file_id, album_id)
        )
        await db.commit()
    return {"success": True}


# --- Share Links Endpoints ---
@router.get("/shares")
async def list_shares():
    """Lists all active share links."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT s.id, s.token, s.file_id, f.filename, f.size_bytes AS file_size,
                   s.expires_at, s.max_views, s.view_count,
                   CASE WHEN s.password_hash IS NOT NULL AND s.password_hash != '' THEN 1 ELSE 0 END AS has_password,
                   s.is_revoked, s.created_at
            FROM share_links s
            JOIN files f ON s.file_id = f.id
            ORDER BY s.created_at DESC
            """
        )
        rows = await cursor.fetchall()
        shares = []
        for r in rows:
            s_dict = dict(r)
            s_dict["has_password"] = bool(s_dict["has_password"])
            s_dict["is_revoked"] = bool(s_dict["is_revoked"])
            shares.append(s_dict)
        return shares


@router.post("/shares")
async def create_share_link(body: CreateShareRequest):
    """Creates a temporary public share link for a file."""
    token = secrets.token_hex(16)
    share_id = f"shr-{uuid.uuid4().hex[:12]}"
    expires = (datetime.now(timezone.utc) + timedelta(hours=body.hours)).strftime("%Y-%m-%d %H:%M:%S")

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO share_links (id, token, file_id, password_hash, expires_at, max_views)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (share_id, token, body.file_id, body.password if body.has_password else None, expires, body.max_views)
        )
        await db.commit()

    return {
        "id": share_id,
        "token": token,
        "file_id": body.file_id,
        "expires_at": expires
    }


@router.post("/shares/{share_id}/revoke")
async def revoke_share_link(share_id: str):
    """Revokes a share link immediately."""
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM share_links WHERE id = ?", (share_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Share link not found")
        await db.execute("UPDATE share_links SET is_revoked = 1 WHERE id = ?", (share_id,))
        await db.commit()
    return {"success": True}


@router.get("/s/{token}")
async def access_shared_file(
    token: str,
    request: Request,
    range_header: Optional[str] = Header(None, alias="Range")
):
    """Public streaming download endpoint for a valid share link."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT s.*, f.filename, f.mime_type, f.size_bytes
            FROM share_links s
            JOIN files f ON s.file_id = f.id
            WHERE s.token = ? AND s.is_revoked = 0 AND f.is_deleted = 0
            """,
            (token,)
        )
        share = await cursor.fetchone()
        if not share:
            raise HTTPException(status_code=404, detail="Shared link not found or revoked")

        s_dict = dict(share)
        if s_dict.get("max_views") and s_dict["view_count"] >= s_dict["max_views"]:
            raise HTTPException(status_code=410, detail="Share link view limit exceeded")

        # Increment view count
        await db.execute("UPDATE share_links SET view_count = view_count + 1 WHERE id = ?", (s_dict["id"],))
        await db.commit()

    return await download_file(file_id=s_dict["file_id"], request=request, range_header=range_header)


# --- Disaster Recovery ---
@router.post("/system/rebuild-index")
async def rebuild_index_from_channel():
    """
    Disaster Recovery: scans Telegram channel message history,
    parses VAYOM:v1: captions, and reconstructs the full SQLite catalog!
    """
    restored_count = await perform_rebuild_index(limit=5000)
    return {"status": "complete", "restored_envelopes": restored_count}
