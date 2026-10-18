import json
import uuid
import os
import shutil
import secrets
import mimetypes
import re
import urllib.parse
import asyncio
import logging
import aiofiles
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException, Query, Response, Request, status
from fastapi.responses import StreamingResponse, FileResponse
from ..config import settings
from ..db.connection import get_db
from ..storage.client import storage_client
from ..compression.splitter import split_file_if_needed, calculate_sha256
from ..index.metadata import (
    detect_file_category,
    generate_webp_thumbnail,
    generate_video_thumbnail,
    apply_mp4_faststart,
    extract_image_exif,
    extract_video_metadata
)
from ..streaming.engine import streaming_engine, parse_and_clamp_range, DEFAULT_MAX_206_BYTES
import time
from ..crypto.cipher import generate_file_nonce

logger = logging.getLogger("vayom.routes")
router = APIRouter(prefix="/api/v1")

# In-memory real-time upload progress tracking keyed by task_id
upload_progress_tracker: Dict[str, Dict[str, Any]] = {}

def generate_file_id() -> str:
    """Generates continuous alphanumeric ID without hyphens to prevent Telegram search word-breaking."""
    return f"v_{uuid.uuid4().hex}"

def generate_part_id() -> str:
    """Generates continuous alphanumeric part ID."""
    return f"vp_{uuid.uuid4().hex}"

def format_content_disposition(filename: str, disposition_type: str = "inline") -> str:
    """
    Builds an RFC 6266 / RFC 5987 safe `Content-Disposition` header value.

    - The ASCII fallback parameter strips control characters (CR/LF/TAB/etc.)
      and double quotes so a raw filename can never break the header or inject
      additional headers (CWE-113).
    - The `filename*=UTF-8''...` parameter carries the full percent-encoded
      filename so non-ASCII names survive intact in modern browsers.
    """
    # Strip control characters and double quotes for the fallback ASCII parameter
    clean_ascii = re.sub(r'[\x00-\x1f\x7f"]', '', filename or "").encode('ascii', 'ignore').decode('ascii').strip() or "file"
    # RFC 5987 UTF-8 percent-encoded filename parameter
    encoded_utf8 = urllib.parse.quote(filename or "", safe='')
    return f'{disposition_type}; filename="{clean_ascii}"; filename*=UTF-8\'\'{encoded_utf8}'

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

class BackupChannelRequest(BaseModel):
    backup_channel_id: str


# --- Master Catalog & Disaster Recovery Logic ---
_catalog_sync_lock = asyncio.Lock()
_catalog_sync_requested = 0
_catalog_sync_completed = 0

async def _do_sync_master_catalog():
    """Compacts the local SQLite file catalog and updates/pins index.json."""
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM files WHERE is_deleted = 0 ORDER BY created_at DESC")
        files = [dict(r) for r in await cur.fetchall()]
        catalog_files = []
        total_bytes = 0
        for f in files:
            fid = f["id"]
            total_bytes += f.get("size_bytes", 0)
            p_cur = await db.execute(
                "SELECT part_index, byte_offset AS offset, part_size_bytes AS size, part_sha256 AS sha256, telegram_message_id, telegram_channel_id FROM file_parts WHERE file_id = ? ORDER BY part_index ASC",
                (fid,)
            )
            parts = [dict(p) for p in await p_cur.fetchall()]
            f_item = {
                "id": fid,
                "filename": f["filename"],
                "mime_type": f["mime_type"],
                "file_category": f["file_category"],
                "size_bytes": f["size_bytes"],
                "sha256": f["sha256"],
                "is_split": bool(f.get("is_split")),
                "total_parts": f.get("total_parts", 1),
                "upload_status": f.get("upload_status", "completed"),
                "nonce": f.get("nonce"),
                "encryption": f.get("encryption", "none"),
                "created_at": f.get("created_at"),
                "parts": parts
            }
            catalog_files.append(f_item)

    catalog_data = {
        "version": "1.0",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "channel_id": str(settings.TELEGRAM_CHANNEL_ID or ""),
        "total_files": len(catalog_files),
        "total_bytes": total_bytes,
        "files": catalog_files
    }
    old_pinned_id = storage_client.last_pinned_catalog_msg_id
    new_pinned_id = await storage_client.save_and_pin_catalog(catalog_data, old_pinned_id=old_pinned_id)
    logger.info(f"Master catalog index.json synchronized and pinned to channel (msg_id={new_pinned_id}).")
    return new_pinned_id

async def sync_master_catalog():
    """
    Compacts the local SQLite file catalog and updates/pins index.json
    directly in the private Telegram storage channel WAL.
    Serializes execution and coalesces concurrent sync requests to prevent races and channel flooding.
    """
    global _catalog_sync_requested, _catalog_sync_completed
    _catalog_sync_requested += 1
    target_version = _catalog_sync_requested

    try:
        async with _catalog_sync_lock:
            if _catalog_sync_completed >= target_version:
                return
            current_version = _catalog_sync_requested
            await _do_sync_master_catalog()
            _catalog_sync_completed = current_version
    except Exception as e:
        logger.error(f"Error syncing master catalog: {e}")

async def perform_rebuild_index(limit: int = 5000) -> int:
    """
    Sub-second library initialization:
    1. Attempts to fetch the pinned master catalog (index.json) for instant restoration.
    2. If absent/corrupted, falls back to scanning Telegram channel message captions.
    Reconciles multi-part files and flags incomplete uploads as 'interrupted'.
    """
    restored_count = 0

    # Fast path: Check pinned master catalog index.json
    pinned_catalog = await storage_client.get_pinned_catalog()
    if pinned_catalog and isinstance(pinned_catalog.get("files"), list) and len(pinned_catalog["files"]) > 0:
        logger.info(f"Sub-second boot: Restoring {len(pinned_catalog['files'])} files from pinned master catalog.")
        async with get_db() as db:
            for item in pinned_catalog["files"]:
                file_id = item.get("id")
                if not file_id:
                    continue

                cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
                if not await cur.fetchone():
                    await db.execute(
                        """
                        INSERT INTO files (
                            id, filename, mime_type, file_category, size_bytes, sha256,
                            is_split, total_parts, upload_status, nonce, encryption, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            file_id, item.get("filename", "recovered_file"),
                            item.get("mime_type", "application/octet-stream"),
                            item.get("file_category", "other"), item.get("size_bytes", 0),
                            item.get("sha256", ""), 1 if item.get("total_parts", 1) > 1 else 0,
                            item.get("total_parts", 1), item.get("upload_status", "completed"),
                            item.get("nonce"), item.get("encryption", "none"),
                            item.get("created_at", datetime.now(timezone.utc).isoformat())
                        )
                    )

                for p in item.get("parts", []):
                    part_id = generate_part_id()
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO file_parts (
                            id, file_id, part_index, byte_offset, part_size_bytes,
                            part_sha256, telegram_message_id, telegram_channel_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            part_id, file_id, p.get("part_index", 0), p.get("offset", 0),
                            p.get("size", 0), p.get("sha256", ""), p.get("telegram_message_id"),
                            str(p.get("telegram_channel_id") or settings.TELEGRAM_CHANNEL_ID)
                        )
                    )
                restored_count += 1
            await db.commit()
        return restored_count

    # Slow fallback: Scan channel envelopes backwards from HEAD
    logger.info("No pinned catalog found. Falling back to backward envelope history scan.")
    envelopes = await storage_client.scan_channel_envelopes(limit=limit)

    # Group parts by file_id for incomplete upload reconciliation
    file_envelopes: Dict[str, List[dict]] = {}
    for env in envelopes:
        fid = env.get("id")
        if fid:
            file_envelopes.setdefault(fid, []).append(env)

    async with get_db() as db:
        for file_id, parts in file_envelopes.items():
            first = parts[0]
            total_expected = first.get("total", 1)
            parts_found = len(parts)
            is_interrupted = parts_found < total_expected

            cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
            if not await cur.fetchone():
                await db.execute(
                    """
                    INSERT INTO files (
                        id, filename, mime_type, file_category, size_bytes, sha256,
                        is_split, total_parts, upload_status, nonce, encryption
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id, first.get("name", "recovered_file"),
                        first.get("mime", "application/octet-stream"),
                        first.get("cat", "other"), first.get("size", 0),
                        first.get("sha256", ""),
                        1 if total_expected > 1 else 0,
                        total_expected,
                        "interrupted" if is_interrupted else "completed",
                        first.get("nonce"),
                        first.get("enc", "none")
                    )
                )

            for env in parts:
                part_id = generate_part_id()
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

    if restored_count > 0:
        asyncio.create_task(sync_master_catalog())

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

            # Format booleans, URLs, and reconciliation status
            total_parts = f_dict.get("total_parts", 1)
            uploaded_count = len(parts)
            is_interrupted = (f_dict.get("upload_status") == "interrupted") or (uploaded_count < total_parts and f_dict.get("upload_status") != "completed")
            f_dict["is_interrupted"] = is_interrupted
            f_dict["uploaded_parts"] = uploaded_count
            f_dict["is_split"] = bool(f_dict.get("is_split"))
            f_dict["is_compressed"] = bool(f_dict.get("is_compressed"))
            f_dict["is_lossy_video"] = bool(f_dict.get("is_lossy_video"))
            f_dict["is_deleted"] = bool(f_dict.get("is_deleted"))

            if (f_dict.get("has_thumbnail") or f_dict.get("file_category") in ("image", "video")) and not f_dict.get("is_deleted"):
                f_dict["thumbnail_url"] = f"/api/v1/files/{file_id}/thumbnail"
            else:
                f_dict["thumbnail_url"] = None

            if not f_dict.get("is_deleted"):
                f_dict["preview_url"] = f"/api/v1/virtual-stream/{file_id}" if f_dict.get("file_category") == "video" else f"/api/v1/files/{file_id}/download"
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
    request: Request,
    file: UploadFile = File(...),
    task_id: Optional[str] = Query(None)
):
    """
    Receives file, calculates SHA-256, generates WebP thumbnail,
    slices into parts if > 1.9 GB, streams to Telegram with alphanumeric v_ ID and AES-CTR nonce,
    and commits to SQLite before updating pinned master catalog.
    Tracks live Telegram MTProto upload progress in upload_progress_tracker.
    """
    effective_task_id = task_id or request.headers.get("X-Task-ID") or request.query_params.get("task_id")

    if storage_client.flood_wait_seconds > 60:
        if effective_task_id and effective_task_id in upload_progress_tracker:
            upload_progress_tracker[effective_task_id].update({
                "status": "failed",
                "stage": f"Telegram flood wait active ({storage_client.flood_wait_seconds}s)",
                "error": f"FLOOD_WAIT_{storage_client.flood_wait_seconds}"
            })
            # B-6: expire this failed entry like the main failure path (no leaked tracker rows)
            async def _expire_failed_tracker_early():
                await asyncio.sleep(60)
                upload_progress_tracker.pop(effective_task_id, None)
            asyncio.create_task(_expire_failed_tracker_early())
        raise HTTPException(
            status_code=429,
            detail=f"FLOOD_WAIT_{storage_client.flood_wait_seconds}",
            headers={"Retry-After": str(storage_client.flood_wait_seconds)}
        )

    file_id = generate_file_id()
    nonce = None
    temp_dir = settings.UPLOAD_TEMP_DIR / file_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(file.filename).name or f"{file_id}_upload"
    target_path = temp_dir / safe_filename
    thumbnail_path = settings.THUMBNAIL_DIR / f"{file_id}.webp"
    has_thumb = False
    parts = []
    uploaded_parts = []
    total_size = 0

    try:
        if effective_task_id:
            upload_progress_tracker[effective_task_id] = {
                "task_id": effective_task_id,
                "file_id": file_id,
                "status": "buffering",
                "stage": "Buffering file to node gateway...",
                "uploaded_bytes": 0,
                "total_bytes": getattr(file, "size", 0) or 0,
                "speed_bps": 0,
                "eta_seconds": 0,
                "progress_percent": 5,
                "current_part": 1,
                "total_parts": 1,
                "last_updated": time.time()
            }

        # 1. Stream incoming upload to temporary disk
        async with aiofiles.open(target_path, "wb") as out_file:
            while chunk := await file.read(1024 * 1024):
                await out_file.write(chunk)

        total_size = os.path.getsize(target_path)
        mime_type = file.content_type or "application/octet-stream"
        category = detect_file_category(mime_type, file.filename)

        # 2. Thumbnail & Stream Header Optimization
        if effective_task_id:
            upload_progress_tracker[effective_task_id].update({
                "status": "analyzing",
                "stage": "Generating media preview & streaming headers...",
                "progress_percent": 10,
                "last_updated": time.time()
            })

        if category == "image":
            has_thumb = generate_webp_thumbnail(target_path, thumbnail_path)
        elif category == "video":
            has_thumb = await generate_video_thumbnail(target_path, thumbnail_path)
            # Apply FastStart for MP4/MOV so browsers can play immediately from byte 0
            ext = target_path.suffix.lower()
            if ext in (".mp4", ".mov", ".m4v"):
                faststart_path = target_path.with_name(f"faststart_{target_path.name}")
                if await apply_mp4_faststart(target_path, faststart_path):
                    try:
                        target_path.unlink()
                        faststart_path.rename(target_path)
                        total_size = target_path.stat().st_size
                    except Exception as e:
                        logger.warning(f"Failed swapping faststart video: {e}")

        # 3. Non-blocking single-pass SHA-256 Integrity Hash
        if effective_task_id:
            upload_progress_tracker[effective_task_id].update({
                "status": "hashing",
                "stage": "Computing SHA-256 cryptographic checksum...",
                "total_bytes": total_size,
                "progress_percent": 12,
                "last_updated": time.time()
            })

        sha256 = await asyncio.to_thread(calculate_sha256, target_path)

        exif = {}
        if category == "image":
            exif = extract_image_exif(target_path)
        elif category == "video":
            exif = await extract_video_metadata(target_path)

        # 4. Size Policy & Pointer-Offset Slicing (with precomputed SHA)
        parts = split_file_if_needed(target_path, max_part_bytes=settings.MAX_PART_BYTES, precomputed_sha=sha256)
        total_parts_count = len(parts)

        if effective_task_id:
            upload_progress_tracker[effective_task_id].update({
                "status": "splitting" if total_parts_count > 1 else "uploading",
                "stage": f"Starting MTProto upload to Telegram (0/{total_parts_count} parts)" if total_parts_count == 1 else f"Splitting into {total_parts_count} parts...",
                "total_bytes": total_size,
                "total_parts": total_parts_count,
                "current_part": 1,
                "progress_percent": 15,
                "last_updated": time.time()
            })

        # 4. Upload each part to Telegram with Caption Envelope (Continuous v_ ID & AES-CTR nonce)
        last_callback_time = time.time()
        last_callback_bytes = 0
        smoothed_speed = 0.0

        for part_idx, part in enumerate(parts):
            current_part_num = part_idx + 1
            part_offset = part.get("byte_offset", 0)
            part_size = part.get("part_size_bytes", 0)

            def make_progress_cb(p_num: int, p_offset: int, p_size_bytes: int):
                nonlocal last_callback_time, last_callback_bytes, smoothed_speed
                def progress_cb(current: int, total: int):
                    nonlocal last_callback_time, last_callback_bytes, smoothed_speed
                    if not effective_task_id:
                        return
                    now = time.time()
                    dt = now - last_callback_time
                    total_uploaded = p_offset + current
                    pct = min(98, max(1, int((total_uploaded / total_size) * 100))) if total_size > 0 else 0

                    if dt >= 0.2:
                        delta_bytes = current - last_callback_bytes
                        if delta_bytes > 0:
                            inst_speed = delta_bytes / dt
                            smoothed_speed = (smoothed_speed * 0.7 + inst_speed * 0.3) if smoothed_speed > 0 else inst_speed
                        last_callback_time = now
                        last_callback_bytes = current

                    rem_bytes = max(0, total_size - total_uploaded)
                    eta = int(rem_bytes / smoothed_speed) if smoothed_speed > 0 else 0

                    stage_msg = (
                        f"Finalizing Telegram message (Part {p_num}/{total_parts_count})..."
                        if current >= p_size_bytes
                        else (f"Uploading Part {p_num}/{total_parts_count} to Telegram MTProto" if total_parts_count > 1 else "Uploading to Telegram MTProto")
                    )

                    upload_progress_tracker[effective_task_id] = {
                        "task_id": effective_task_id,
                        "status": "uploading",
                        "stage": stage_msg,
                        "uploaded_bytes": total_uploaded,
                        "total_bytes": total_size,
                        "speed_bps": int(smoothed_speed),
                        "eta_seconds": eta,
                        "progress_percent": pct,
                        "current_part": p_num,
                        "total_parts": total_parts_count,
                        "last_updated": now
                    }
                return progress_cb

            last_callback_bytes = 0
            last_callback_time = time.time()
            cb = make_progress_cb(current_part_num, part_offset, part_size) if effective_task_id else None

            caption = (
                f"VAYOM:v1:{json.dumps({'id': file_id, 'name': file.filename, 'part': part['part_index'], 'total': len(parts), 'offset': part['byte_offset'], 'size': part['part_size_bytes'], 'sha256': part['part_sha256'], 'cat': category, 'nonce': None, 'enc': 'none'})}"
            )
            res = await storage_client.upload_part(part["file_path"], caption=caption, progress_callback=cb)
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
        if effective_task_id:
            upload_progress_tracker[effective_task_id].update({
                "status": "verifying",
                "stage": "Committing file metadata to SQLite & WAL catalog...",
                "uploaded_bytes": total_size,
                "progress_percent": 99,
                "speed_bps": 0,
                "eta_seconds": 0,
                "last_updated": time.time()
            })

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO files (
                    id, filename, mime_type, file_category, size_bytes, sha256,
                    is_split, total_parts, upload_status, nonce, encryption,
                    has_thumbnail, thumbnail_path, exif_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id, file.filename, mime_type, category, total_size, sha256,
                    1 if len(parts) > 1 else 0, len(parts), "completed", None, "none",
                    1 if has_thumb else 0,
                    str(thumbnail_path) if has_thumb else None,
                    json.dumps(exif) if exif else None
                )
            )

            for p in uploaded_parts:
                part_id = generate_part_id()
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

            # Auto-associate extracted metadata tags
            if exif and isinstance(exif, dict) and exif.get("tags"):
                for t_name in exif["tags"]:
                    t_clean = str(t_name).strip().lower()
                    if not t_clean:
                        continue
                    tag_id = f"tag_{uuid.uuid4().hex[:12]}"
                    await db.execute("INSERT OR IGNORE INTO tags (id, name) VALUES (?, ?)", (tag_id, t_clean))
                    cur = await db.execute("SELECT id FROM tags WHERE name = ?", (t_clean,))
                    row = await cur.fetchone()
                    if row:
                        real_tag_id = row[0]
                        await db.execute("INSERT OR IGNORE INTO file_tags (file_id, tag_id) VALUES (?, ?)", (file_id, real_tag_id))

            await db.commit()

        # Asynchronously sync updated master catalog to channel WAL
        asyncio.create_task(sync_master_catalog())

        if effective_task_id:
            upload_progress_tracker[effective_task_id].update({
                "status": "done",
                "stage": "Upload completed successfully",
                "uploaded_bytes": total_size,
                "progress_percent": 100,
                "speed_bps": 0,
                "eta_seconds": 0,
                "last_updated": time.time()
            })
            async def cleanup_tracker():
                await asyncio.sleep(60)
                upload_progress_tracker.pop(effective_task_id, None)
            asyncio.create_task(cleanup_tracker())

        return {
            "file_id": file_id,
            "filename": file.filename,
            "size_bytes": total_size,
            "sha256": sha256,
            "total_parts": len(parts),
            "status": "confirmed",
            "nonce": None,
            "encryption": "none"
        }
    except BaseException as exc:
        logger.error(f"Upload failed for file_id {file_id}: {exc}")

        # Failure cleanup 1: Purge any orphaned messages from Telegram
        if uploaded_parts:
            try:
                logger.warning(
                    f"Upload failed mid-process; purging {len(uploaded_parts)} orphaned Telegram message(s) for file {file_id}..."
                )
                del_coro = storage_client.delete_parts(list(uploaded_parts))
                delete_task = asyncio.create_task(del_coro)
                try:
                    await asyncio.shield(delete_task)
                except asyncio.CancelledError:
                    pass
            except Exception as del_err:
                logger.error(f"Failed deleting orphaned parts for {file_id}: {del_err}")

        # Failure cleanup 2: Remove orphaned thumbnail if generated
        if has_thumb and thumbnail_path.exists():
            try:
                thumbnail_path.unlink(missing_ok=True)
            except Exception as thumb_err:
                logger.warning(f"Failed removing orphaned thumbnail {thumbnail_path}: {thumb_err}")

        # Failure cleanup 3: Update progress tracker to failed state and schedule TTL cleanup
        if effective_task_id:
            err_msg = str(exc) if str(exc) else type(exc).__name__
            upload_progress_tracker[effective_task_id] = {
                "task_id": effective_task_id,
                "status": "failed",
                "stage": f"Upload failed: {err_msg}",
                "error": err_msg,
                "uploaded_bytes": 0,
                "total_bytes": total_size,
                "speed_bps": 0,
                "eta_seconds": 0,
                "progress_percent": 0,
                "last_updated": time.time()
            }
            async def cleanup_failed_tracker():
                await asyncio.sleep(60)
                upload_progress_tracker.pop(effective_task_id, None)
            asyncio.create_task(cleanup_failed_tracker())

        raise
    finally:
        # Cleanup temporary upload directory and all sliced part files
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as cleanup_err:
            logger.warning(f"Failed removing temp_dir {temp_dir}: {cleanup_err}")


@router.get("/upload/{task_id}/progress")
async def get_upload_progress(task_id: str):
    """Returns real-time upload progress from Telegram MTProto worker with rate-limit status."""
    wait_secs = storage_client.flood_wait_seconds
    if task_id in upload_progress_tracker:
        data = dict(upload_progress_tracker[task_id])
        if wait_secs > 0 and data.get("status") not in ("failed", "done"):
            data["status"] = "rate_limited"
            data["flood_wait_seconds"] = wait_secs
            data["stage"] = f"Telegram rate limit active. Paused for {int(wait_secs)}s to safeguard your account from bans."
        return data
    return {
        "task_id": task_id,
        "status": "rate_limited" if wait_secs > 0 else "unknown",
        "flood_wait_seconds": wait_secs,
        "stage": f"Telegram rate limit active ({int(wait_secs)}s)" if wait_secs > 0 else "Unknown",
        "uploaded_bytes": 0,
        "total_bytes": 0,
        "speed_bps": 0,
        "eta_seconds": 0,
        "progress_percent": 0
    }


# --- Vault Redundancy & Disaster Recovery Endpoints ---
@router.get("/vault/channel-health")
async def get_channel_health():
    """Returns access, permission, and redundancy health of primary & backup channels."""
    return await storage_client.check_channel_health()


@router.post("/vault/backup-channel")
async def set_backup_channel(req: BackupChannelRequest):
    """Configures secondary mirror channel for zero-data-loss redundancy."""
    settings.TELEGRAM_BACKUP_CHANNEL_ID = req.backup_channel_id.strip()
    config_data = {}
    if settings.auth_config_file.exists():
        try:
            config_data = json.loads(settings.auth_config_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    config_data["backup_channel_id"] = settings.TELEGRAM_BACKUP_CHANNEL_ID
    settings.save_auth_config(config_data)

    # Immediately mirror current master catalog to backup channel
    asyncio.create_task(sync_master_catalog())
    return {
        "status": "configured",
        "backup_channel_id": settings.TELEGRAM_BACKUP_CHANNEL_ID
    }


@router.get("/vault/catalog/export")
async def export_vault_catalog():
    """Exports compacted master catalog JSON for local offline disaster recovery."""
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM files WHERE is_deleted = 0 ORDER BY created_at DESC")
        files = [dict(r) for r in await cur.fetchall()]
        catalog_files = []
        total_bytes = 0
        for f in files:
            fid = f["id"]
            total_bytes += f.get("size_bytes", 0)
            p_cur = await db.execute(
                "SELECT part_index, byte_offset AS offset, part_size_bytes AS size, part_sha256 AS sha256, telegram_message_id, telegram_channel_id FROM file_parts WHERE file_id = ? ORDER BY part_index ASC",
                (fid,)
            )
            parts = [dict(p) for p in await p_cur.fetchall()]
            f_item = {
                "id": fid,
                "filename": f["filename"],
                "mime_type": f["mime_type"],
                "file_category": f["file_category"],
                "size_bytes": f["size_bytes"],
                "sha256": f["sha256"],
                "is_split": bool(f.get("is_split")),
                "total_parts": f.get("total_parts", 1),
                "upload_status": f.get("upload_status", "completed"),
                "nonce": f.get("nonce"),
                "encryption": f.get("encryption", "none"),
                "created_at": f.get("created_at"),
                "parts": parts
            }
            catalog_files.append(f_item)

    catalog_data = {
        "version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "channel_id": str(settings.TELEGRAM_CHANNEL_ID or ""),
        "total_files": len(catalog_files),
        "total_bytes": total_bytes,
        "files": catalog_files
    }
    content = json.dumps(catalog_data, indent=2)
    filename = f"vayom_vault_catalog_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": format_content_disposition(filename, disposition_type="attachment")}
    )


@router.post("/vault/catalog/import")
async def import_vault_catalog(catalog_file: UploadFile = File(...)):
    """Restores files, parts, and metadata from an exported vault catalog JSON backup."""
    raw = await catalog_file.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON format: {e}")

    files_list = data.get("files")
    if not isinstance(files_list, list):
        raise HTTPException(status_code=400, detail="Missing or invalid 'files' array in catalog")

    restored_count = 0
    async with get_db() as db:
        for item in files_list:
            file_id = item.get("id")
            if not file_id:
                continue

            cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
            if not await cur.fetchone():
                await db.execute(
                    """
                    INSERT INTO files (
                        id, filename, mime_type, file_category, size_bytes, sha256,
                        is_split, total_parts, upload_status, nonce, encryption, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id, item.get("filename", "restored_file"),
                        item.get("mime_type", "application/octet-stream"),
                        item.get("file_category", "other"), item.get("size_bytes", 0),
                        item.get("sha256", ""), 1 if item.get("total_parts", 1) > 1 else 0,
                        item.get("total_parts", 1), item.get("upload_status", "completed"),
                        item.get("nonce"), item.get("encryption", "none"),
                        item.get("created_at", datetime.now(timezone.utc).isoformat())
                    )
                )

            for p in item.get("parts", []):
                part_id = generate_part_id()
                await db.execute(
                    """
                    INSERT OR IGNORE INTO file_parts (
                        id, file_id, part_index, byte_offset, part_size_bytes,
                        part_sha256, telegram_message_id, telegram_channel_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        part_id, file_id, p.get("part_index", 0), p.get("offset", 0),
                        p.get("size", 0), p.get("sha256", ""), p.get("telegram_message_id"),
                        str(p.get("telegram_channel_id") or settings.TELEGRAM_CHANNEL_ID)
                    )
                )
            restored_count += 1
        await db.commit()

    asyncio.create_task(sync_master_catalog())
    return {
        "status": "success",
        "restored_files": restored_count,
        "total_catalog_files": len(files_list)
    }


@router.get("/files/{file_id}/upload-status")
async def get_upload_status(file_id: str):
    """Returns upload status and missing parts list for one-click resumption."""
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        f_row = await cur.fetchone()
        if not f_row:
            raise HTTPException(status_code=404, detail="File not found")
        f_dict = dict(f_row)

        p_cur = await db.execute("SELECT part_index FROM file_parts WHERE file_id = ? ORDER BY part_index ASC", (file_id,))
        part_indices = [r["part_index"] for r in await p_cur.fetchall()]

        total_parts = f_dict.get("total_parts", 1)
        is_interrupted = len(part_indices) < total_parts or f_dict.get("upload_status") == "interrupted"
        missing_parts = [i for i in range(total_parts) if i not in part_indices]
        next_part = missing_parts[0] if missing_parts else None

        return {
            "file_id": file_id,
            "filename": f_dict["filename"],
            "total_parts": total_parts,
            "uploaded_parts": part_indices,
            "uploaded_count": len(part_indices),
            "missing_parts": missing_parts,
            "next_part": next_part,
            "is_interrupted": is_interrupted,
            "upload_status": "interrupted" if is_interrupted else f_dict.get("upload_status", "completed"),
            "nonce": f_dict.get("nonce")
        }


@router.post("/upload/init")
async def init_upload(
    filename: str = Form(...),
    total_size: int = Form(...),
    mime_type: Optional[str] = Form("application/octet-stream"),
    total_parts: int = Form(1),
    nonce: Optional[str] = Form(None),
    encryption: str = Form("none")
):
    """Initializes a resumable chunked upload and returns continuous v_ file ID."""
    file_id = generate_file_id()
    file_nonce = nonce
    guessed_mime = mimetypes.guess_type(filename)[0] or (mime_type if mime_type and mime_type != "application/octet-stream" else None) or "application/octet-stream"
    category = detect_file_category(guessed_mime, filename)

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (
                id, filename, mime_type, file_category, size_bytes, sha256,
                is_split, total_parts, upload_status, nonce, encryption
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id, filename, guessed_mime, category, total_size, "",
                1 if total_parts > 1 else 0, total_parts, "uploading", file_nonce, encryption
            )
        )
        await db.commit()

    return {
        "file_id": file_id,
        "filename": filename,
        "nonce": file_nonce,
        "encryption": encryption,
        "total_parts": total_parts,
        "upload_status": "uploading"
    }


@router.post("/upload/chunk")
async def upload_chunk(
    file_id: str = Form(...),
    part_index: int = Form(...),
    total_parts: int = Form(...),
    byte_offset: int = Form(...),
    part_sha256: str = Form(...),
    filename: str = Form(...),
    total_size: int = Form(...),
    nonce: Optional[str] = Form(None),
    encryption: str = Form("none"),
    chunk: UploadFile = File(...)
):
    """
    Receives an individual 512 KiB / chunk slice, streams it directly to Telegram WAL,
    commits the extent pointer, and reconciles file completion status.
    """
    if storage_client.flood_wait_seconds > 60:
        raise HTTPException(
            status_code=429,
            detail=f"FLOOD_WAIT_{storage_client.flood_wait_seconds}",
            headers={"Retry-After": str(storage_client.flood_wait_seconds)}
        )

    temp_dir = settings.UPLOAD_TEMP_DIR / file_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = temp_dir / f"part_{part_index}.bin"

    async with aiofiles.open(chunk_path, "wb") as out_file:
        while data := await chunk.read(1024 * 1024):
            await out_file.write(data)

    chunk_size = os.path.getsize(chunk_path)
    computed_sha = calculate_sha256(chunk_path)
    guessed_mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    category = detect_file_category(guessed_mime, filename)

    # For Part 0: extract thumbnail and video metadata immediately while the chunk is on local disk
    thumb_path = settings.THUMBNAIL_DIR / f"{file_id}.webp"
    video_meta = {}
    if part_index == 0:
        try:
            if category == "video":
                await generate_video_thumbnail(chunk_path, thumb_path)
                video_meta = await extract_video_metadata(chunk_path)
            elif category == "image":
                generate_webp_thumbnail(chunk_path, thumb_path)
                video_meta = extract_image_exif(chunk_path)
        except Exception as meta_err:
            logger.warning(f"Failed extracting preview/metadata for {file_id}: {meta_err}")

    caption = (
        f"VAYOM:v1:{json.dumps({'id': file_id, 'name': filename, 'part': part_index, 'total': total_parts, 'offset': byte_offset, 'size': chunk_size, 'sha256': computed_sha, 'cat': category, 'nonce': nonce, 'enc': encryption})}"
    )

    res = await storage_client.upload_part(str(chunk_path), caption=caption)
    part_id = generate_part_id()

    msg_id = res["message_id"] if isinstance(res, dict) else res
    chan_id = res.get("channel_id", str(settings.TELEGRAM_CHANNEL_ID)) if isinstance(res, dict) else str(settings.TELEGRAM_CHANNEL_ID)
    tg_file_id = res.get("file_id") if isinstance(res, dict) else None
    tg_access_hash = res.get("access_hash") if isinstance(res, dict) else None

    async with get_db() as db:
        # Check/ensure file record exists
        cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
        if not await cur.fetchone():
            await db.execute(
                """
                INSERT INTO files (
                    id, filename, mime_type, file_category, size_bytes, sha256,
                    is_split, total_parts, upload_status, nonce, encryption,
                    has_thumbnail, thumbnail_path, exif_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id, filename, guessed_mime, category, total_size, "",
                    1 if total_parts > 1 else 0, total_parts, "uploading", nonce, encryption,
                    1 if thumb_path.exists() else 0,
                    str(thumb_path) if thumb_path.exists() else None,
                    json.dumps(video_meta) if video_meta else None
                )
            )

        # Commit part pointer
        await db.execute(
            """
            INSERT OR REPLACE INTO file_parts (
                id, file_id, part_index, byte_offset, part_size_bytes,
                part_sha256, telegram_message_id, telegram_channel_id,
                telegram_file_id, telegram_access_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                part_id, file_id, part_index, byte_offset,
                chunk_size, computed_sha, msg_id, chan_id,
                tg_file_id, tg_access_hash
            )
        )

        # Check if all parts have been received
        p_cur = await db.execute("SELECT COUNT(*) FROM file_parts WHERE file_id = ?", (file_id,))
        uploaded_count = (await p_cur.fetchone())[0]

        is_done = uploaded_count >= total_parts
        if is_done:
            has_thumb = 1 if thumb_path.exists() else 0
            await db.execute(
                """
                UPDATE files SET 
                    upload_status = 'completed',
                    mime_type = ?,
                    has_thumbnail = CASE WHEN has_thumbnail = 1 THEN 1 ELSE ? END,
                    thumbnail_path = COALESCE(thumbnail_path, ?),
                    exif_json = COALESCE(exif_json, ?)
                WHERE id = ?
                """,
                (
                    guessed_mime,
                    has_thumb,
                    str(thumb_path) if has_thumb else None,
                    json.dumps(video_meta) if video_meta else None,
                    file_id
                )
            )

            # Auto-associate extracted metadata tags
            if video_meta and isinstance(video_meta, dict) and video_meta.get("tags"):
                for t_name in video_meta["tags"]:
                    t_clean = str(t_name).strip().lower()
                    if not t_clean:
                        continue
                    tag_id = f"tag_{uuid.uuid4().hex[:12]}"
                    await db.execute("INSERT OR IGNORE INTO tags (id, name) VALUES (?, ?)", (tag_id, t_clean))
                    cur_t = await db.execute("SELECT id FROM tags WHERE name = ?", (t_clean,))
                    row_t = await cur_t.fetchone()
                    if row_t:
                        await db.execute("INSERT OR IGNORE INTO file_tags (file_id, tag_id) VALUES (?, ?)", (file_id, row_t[0]))

        await db.commit()

    try:
        chunk_path.unlink()
        if not any(temp_dir.iterdir()):
            temp_dir.rmdir()
    except Exception:
        pass

    if is_done:
        asyncio.create_task(sync_master_catalog())

    return {
        "file_id": file_id,
        "part_index": part_index,
        "uploaded_parts": uploaded_count,
        "total_parts": total_parts,
        "is_completed": is_done,
        "status": "completed" if is_done else "uploading"
    }


@router.delete("/files/{file_id}/incomplete")
async def cleanup_incomplete_upload(file_id: str):
    """
    Purges orphan uploaded parts of an interrupted upload from Telegram WAL and database.
    Performs atomic batch deletion via channels.deleteMessages.
    """
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="File not found")

        p_cur = await db.execute(
            "SELECT telegram_message_id, telegram_channel_id FROM file_parts WHERE file_id = ?",
            (file_id,)
        )
        parts = [dict(r) for r in await p_cur.fetchall()]

        # Delete all orphan parts from Telegram WAL
        purged_messages = await storage_client.delete_parts(parts)

        # Delete database records
        await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await db.commit()

    asyncio.create_task(sync_master_catalog())
    return {"success": True, "purged_parts": purged_messages}


@router.post("/catalog/sync")
async def trigger_catalog_sync():
    """Forces an immediate compaction and re-pinning of index.json to Telegram channel."""
    await sync_master_catalog()
    return {"status": "ok", "synced": True}


@router.api_route("/files/{file_id}/thumbnail", methods=["GET", "HEAD"])
async def get_thumbnail(file_id: str):
    """Serves the ultra-compact WebP thumbnail preview with on-demand fallback."""
    async with get_db() as db:
        cursor = await db.execute("SELECT id, is_deleted, has_thumbnail, file_category, filename FROM files WHERE id = ?", (file_id,))
        file_row = await cursor.fetchone()
        if not file_row or file_row["is_deleted"]:
            raise HTTPException(status_code=404, detail="Thumbnail not found")

        file_item = dict(file_row)

    thumb = settings.THUMBNAIL_DIR / f"{file_id}.webp"
    if thumb.exists():
        return FileResponse(thumb, media_type="image/webp")

    # On-demand generation: if file exists in DB but thumbnail was not generated or lost
    category = file_item["file_category"]
    has_thumb = False
    try:
        if category == "video":
            token_param = f"?token={settings.STREAM_LOOPBACK_TOKEN}" if settings.STREAM_LOOPBACK_TOKEN else ""
            stream_url = f"http://127.0.0.1:{settings.PORT}/api/v1/virtual-stream/{file_id}{token_param}"
            has_thumb = await generate_video_thumbnail(stream_url, thumb)
        elif category == "image":
            # Fetch image bytes and generate thumbnail
            async with get_db() as db:
                p_cur = await db.execute(
                    "SELECT telegram_message_id, telegram_channel_id FROM file_parts WHERE file_id = ? ORDER BY part_index ASC LIMIT 1",
                    (file_id,)
                )
                p_row = await p_cur.fetchone()
            if p_row:
                tmp_img = settings.DATA_DIR / f"tmp_thumb_{file_id}"
                with open(tmp_img, "wb") as f:
                    async for chunk in storage_client.stream_part_bytes(p_row[0], channel_id=p_row[1], offset=0, limit=2 * 1024 * 1024):
                        f.write(chunk)
                has_thumb = generate_webp_thumbnail(tmp_img, thumb)
                try:
                    tmp_img.unlink()
                except Exception:
                    pass

        if has_thumb and thumb.exists():
            async with get_db() as db:
                await db.execute("UPDATE files SET has_thumbnail = 1, thumbnail_path = ? WHERE id = ?", (str(thumb), file_id))
                await db.commit()
            return FileResponse(thumb, media_type="image/webp")
    except Exception as e:
        logger.warning(f"On-demand thumbnail generation failed for {file_id}: {e}")

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
        "Content-Disposition": format_content_disposition(filename)
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
    asyncio.create_task(sync_master_catalog())
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
    asyncio.create_task(sync_master_catalog())
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
    asyncio.create_task(sync_master_catalog())
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
    asyncio.create_task(sync_master_catalog())
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

    asyncio.create_task(sync_master_catalog())
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

    asyncio.create_task(sync_master_catalog())
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
