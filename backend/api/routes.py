import json
import uuid
import os
import aiofiles
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse, FileResponse
from ..config import settings
from ..db.connection import get_db
from ..storage.client import storage_client
from ..compression.splitter import split_file_if_needed, calculate_sha256
from ..index.metadata import detect_file_category, generate_webp_thumbnail, extract_image_exif

router = APIRouter(prefix="/api/v1")

@router.get("/health")
async def health_check():
    """Heartbeat endpoint for Hugging Face Spaces and uptime monitors."""
    return {"status": "ok", "app": "Vayom Drive", "storage": "telegram"}

@router.get("/system/metrics")
async def get_metrics():
    """Returns storage metrics, channel stats, and online health."""
    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM files WHERE is_deleted = 0")
        total_files, total_bytes = await cursor.fetchone()

        cursor = await db.execute("SELECT COUNT(*) FROM file_parts")
        total_parts = (await cursor.fetchone())[0]

    return {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "channel_id": settings.TELEGRAM_CHANNEL_ID or "NOT_CONFIGURED",
        "telegram_messages": total_parts,
        "online_status": "online" if storage_client._is_connected else "offline_or_mock"
    }

@router.get("/files")
async def list_files(
    category: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    album_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    is_deleted: bool = Query(False),
    limit: int = Query(100),
    offset: int = Query(0)
):
    """Returns filtered, paginated file list with parts and metadata."""
    async with get_db() as db:
        query = "SELECT * FROM files WHERE is_deleted = ?"
        params: list = [1 if is_deleted else 0]

        if category and category != "all":
            query += " AND file_category = ?"
            params.append(category)

        if q:
            query += " AND filename LIKE ?"
            params.append(f"%{q}%")

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        results = []
        for row in rows:
            f_dict = dict(row)
            # Fetch parts for this file
            p_cursor = await db.execute(
                "SELECT part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id "
                "FROM file_parts WHERE file_id = ? ORDER BY part_index ASC",
                (f_dict["id"],)
            )
            parts = [dict(p) for p in await p_cursor.fetchall()]
            f_dict["parts"] = parts
            f_dict["tags"] = []
            f_dict["albums"] = []
            if f_dict.get("exif_json"):
                try:
                    f_dict["exif"] = json.loads(f_dict["exif_json"])
                except Exception:
                    pass
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
    target_path = temp_dir / file.filename

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
            f"VAYOM:v1:{json.dumps({"id": file_id, "name": file.filename, "part": part['part_index'], "total": len(parts), "offset": part['byte_offset'], "size": part['part_size_bytes'], "sha256": part['part_sha256'], "cat": category})}"
        )
        msg_id = await storage_client.upload_part(part["file_path"], caption=caption)
        uploaded_parts.append({
            **part,
            "telegram_message_id": msg_id
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
                    part_sha256, telegram_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part_id, file_id, p["part_index"], p["byte_offset"],
                    p["part_size_bytes"], p["part_sha256"], p["telegram_message_id"]
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

@router.get("/files/{file_id}/thumbnail")
async def get_thumbnail(file_id: str):
    """Serves the ultra-compact WebP thumbnail preview."""
    thumb = settings.THUMBNAIL_DIR / f"{file_id}.webp"
    if thumb.exists():
        return FileResponse(thumb, media_type="image/webp")
    raise HTTPException(status_code=404, detail="Thumbnail not found")

@router.get("/files/{file_id}/download")
async def download_file(
    file_id: str,
    range_header: Optional[str] = Header(None, alias="Range")
):
    """
    Streams file bytes with full HTTP Range request support (206 Partial Content).
    Enables instant seeking in 4K videos without downloading the full file!
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
    filename = file_item["filename"]

    # Parse Range Header if present
    start_byte = 0
    end_byte = total_size - 1

    if range_header and range_header.startswith("bytes="):
        range_val = range_header[len("bytes="):].strip()
        parts_range = range_val.split("-")
        if parts_range[0]:
            start_byte = int(parts_range[0])
        if len(parts_range) > 1 and parts_range[1]:
            end_byte = min(total_size - 1, int(parts_range[1]))

    content_length = end_byte - start_byte + 1

    async def multi_part_stream():
        """Iterates over all parts intersecting [start_byte, end_byte] and streams contiguous bytes."""
        for p in parts:
            p_start = p["byte_offset"]
            p_end = p_start + p["part_size_bytes"] - 1
            if p_end < start_byte or p_start > end_byte:
                continue

            slice_start = max(0, start_byte - p_start)
            slice_end = min(p["part_size_bytes"] - 1, end_byte - p_start)
            slice_len = slice_end - slice_start + 1

            async for chunk in storage_client.stream_part_bytes(
                message_id=p["telegram_message_id"],
                offset=slice_start,
                limit=slice_len
            ):
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Disposition": f'inline; filename="{filename}"'
    }
    if range_header:
        headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{total_size}"

    status_code = status.HTTP_206_PARTIAL_CONTENT if range_header else status.HTTP_200_OK
    return StreamingResponse(
        multi_part_stream(),
        status_code=status_code,
        headers=headers,
        media_type=file_item["mime_type"]
    )

@router.delete("/files/{file_id}")
async def delete_file(file_id: str, purge: bool = False):
    """Soft deletes file to 30-day trash, or purges permanently."""
    async with get_db() as db:
        if purge:
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
        await db.execute(
            "UPDATE files SET is_deleted = 0, deleted_at = NULL WHERE id = ?",
            (file_id,)
        )
        await db.commit()
    return {"success": True}

@router.post("/system/rebuild-index")
async def rebuild_index_from_channel():
    """
    Disaster Recovery: scans Telegram channel message history,
    parses VAYOM:v1: captions, and reconstructs the full SQLite catalog!
    """
    envelopes = await storage_client.scan_channel_envelopes(limit=5000)
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
                INSERT OR IGNORE INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part_id, file_id, env.get("part", 0), env.get("offset", 0),
                    env.get("size", 0), env.get("sha256", ""), env.get("telegram_message_id")
                )
            )
            restored_count += 1

        await db.commit()

    return {"status": "complete", "restored_envelopes": restored_count}
