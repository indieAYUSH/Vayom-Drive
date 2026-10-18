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
from ..storage.client import storage_client, format_part_caption
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
from ..transcoding.manager import transcoding_manager

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

class SetupVaultRequest(BaseModel):
    passphrase: str
    remember_device: bool = True

class UnlockVaultRequest(BaseModel):
    passphrase: Optional[str] = None
    key: Optional[str] = None
    remember_device: bool = True


# --- Master Catalog & Disaster Recovery Logic ---
_catalog_sync_lock = asyncio.Lock()
_catalog_sync_requested = 0
_catalog_sync_completed = 0
_catalog_debounce_task: Optional[asyncio.Task] = None
_catalog_sync_state: Dict[str, Any] = {
    "status": "idle",  # "idle" | "syncing" | "synced" | "failed"
    "last_synced_at": None,
    "last_error": None,
    "pending": False,
    "retry_count": 0,
    "pinned_msg_id": None,
}

def get_catalog_sync_state() -> Dict[str, Any]:
    """E-3: returns current catalog sync and pinned WAL status for health and metrics."""
    state = dict(_catalog_sync_state)
    state["pending"] = _catalog_sync_completed < _catalog_sync_requested
    return state

async def _do_sync_master_catalog():
    """Compacts the local SQLite file catalog and updates/pins index.json with nested proxies."""
    async with get_db() as db:
        # Only select top-level original files (proxies are nested inside parent originals)
        cur = await db.execute("SELECT * FROM files WHERE is_deleted = 0 AND kind = 'original' ORDER BY created_at DESC")
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

            # Fetch active child proxy to nest inside parent entry
            proxy_cur = await db.execute(
                "SELECT * FROM files WHERE proxy_of = ? AND kind = 'proxy' AND is_deleted = 0 LIMIT 1",
                (fid,)
            )
            proxy_row = await proxy_cur.fetchone()
            proxy_item = None
            if proxy_row:
                p_dict = dict(proxy_row)
                p_parts_cur = await db.execute(
                    "SELECT part_index, byte_offset AS offset, part_size_bytes AS size, part_sha256 AS sha256, telegram_message_id, telegram_channel_id FROM file_parts WHERE file_id = ? ORDER BY part_index ASC",
                    (p_dict["id"],)
                )
                p_parts = [dict(p) for p in await p_parts_cur.fetchall()]
                proxy_item = {
                    "id": p_dict["id"],
                    "size_bytes": p_dict["size_bytes"],
                    "sha256": p_dict["sha256"],
                    "profile": p_dict.get("proxy_profile", "stream_v1"),
                    "status": p_dict.get("upload_status", "completed"),
                    "parts": p_parts
                }

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
                "proxy_status": f.get("proxy_status", "none"),
                "proxy": proxy_item,
                "nonce": f.get("nonce"),
                "encryption": f.get("encryption", "none"),
                "created_at": f.get("created_at"),
                "parts": parts
            }
            catalog_files.append(f_item)

    max_msg_id = 0
    for f in catalog_files:
        for p in f.get("parts", []):
            mid = p.get("telegram_message_id")
            if mid and isinstance(mid, int) and mid > max_msg_id:
                max_msg_id = mid
        proxy = f.get("proxy")
        if isinstance(proxy, dict):
            for pp in proxy.get("parts", []):
                mid = pp.get("telegram_message_id")
                if mid and isinstance(mid, int) and mid > max_msg_id:
                    max_msg_id = mid

    catalog_data = {
        "version": "1.0",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "channel_id": str(settings.TELEGRAM_CHANNEL_ID or ""),
        "total_files": len(catalog_files),
        "total_bytes": total_bytes,
        "max_message_id": max_msg_id,
        "files": catalog_files
    }
    old_pinned_id = storage_client.last_pinned_catalog_msg_id
    new_pinned_id = await storage_client.save_and_pin_catalog(catalog_data, old_pinned_id=old_pinned_id)
    logger.info(f"Master catalog index.json synchronized and pinned to channel (msg_id={new_pinned_id}, max_msg_id={max_msg_id}).")
    return new_pinned_id

async def _execute_sync_with_retry(max_retries: int = 3) -> Optional[int]:
    """
    E-3: Executes catalog sync with bounded retries and exponential backoff.
    Updates sync state and prevents silent divergence between local DB and channel.
    """
    global _catalog_sync_completed, _catalog_sync_state
    current_version = _catalog_sync_requested
    if _catalog_sync_completed >= current_version:
        return _catalog_sync_state.get("pinned_msg_id")

    _catalog_sync_state["status"] = "syncing"
    _catalog_sync_state["pending"] = True

    for attempt in range(1, max_retries + 1):
        try:
            new_msg_id = await _do_sync_master_catalog()
            _catalog_sync_completed = current_version
            _catalog_sync_state.update({
                "status": "synced",
                "last_synced_at": datetime.now(timezone.utc).isoformat(),
                "last_error": None,
                "pending": False,
                "retry_count": 0,
                "pinned_msg_id": new_msg_id,
            })
            return new_msg_id
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _catalog_sync_state["retry_count"] = attempt
            _catalog_sync_state["last_error"] = str(e)
            logger.warning(f"Catalog sync attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                backoff = 2.0 * (2 ** (attempt - 1))
                flood_secs = getattr(e, "seconds", None) or getattr(storage_client, "flood_wait_seconds", 0)
                wait_time = max(backoff, flood_secs) if flood_secs > 0 else backoff
                await asyncio.sleep(min(wait_time, 30.0))
            else:
                _catalog_sync_state["status"] = "failed"
                _catalog_sync_state["pending"] = False
                logger.error(f"Catalog sync permanently failed after {max_retries} attempts: {e}")
                raise

async def _debounced_sync_worker(delay_seconds: float):
    global _catalog_debounce_task
    try:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        async with _catalog_sync_lock:
            await _execute_sync_with_retry(max_retries=3)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error in debounced catalog sync worker: {e}")
    finally:
        if _catalog_debounce_task is asyncio.current_task():
            _catalog_debounce_task = None

async def sync_master_catalog(delay_seconds: float = 0.0) -> Optional[int]:
    """
    Compacts the local SQLite file catalog and updates/pins index.json
    directly in the private Telegram storage channel WAL.
    Supports debounced execution (delay_seconds > 0) to coalesce bulk watch folder / upload events.
    """
    global _catalog_sync_requested, _catalog_debounce_task
    _catalog_sync_requested += 1
    _catalog_sync_state["pending"] = True

    if delay_seconds > 0:
        if _catalog_debounce_task and not _catalog_debounce_task.done():
            _catalog_debounce_task.cancel()
        _catalog_debounce_task = asyncio.create_task(_debounced_sync_worker(delay_seconds))
        return None
    else:
        if _catalog_debounce_task and not _catalog_debounce_task.done():
            _catalog_debounce_task.cancel()
            _catalog_debounce_task = None
        async with _catalog_sync_lock:
            return await _execute_sync_with_retry(max_retries=3)

async def perform_rebuild_index(limit: int = 5000) -> int:
    """
    Sub-second library initialization:
    1. Attempts to fetch the pinned master catalog (index.json) for instant restoration.
    2. If absent/corrupted, falls back to two-pass scanning of Telegram channel message captions.
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
                            is_split, total_parts, upload_status, kind, proxy_status, nonce, encryption, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'original', ?, ?, ?, ?)
                        """,
                        (
                            file_id, item.get("filename", "recovered_file"),
                            item.get("mime_type", "application/octet-stream"),
                            item.get("file_category", "other"), item.get("size_bytes", 0),
                            item.get("sha256", ""), 1 if item.get("total_parts", 1) > 1 else 0,
                            item.get("total_parts", 1), item.get("upload_status", "completed"),
                            item.get("proxy_status", "none"),
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

                # Restore nested child proxy if present
                proxy_data = item.get("proxy")
                if isinstance(proxy_data, dict) and proxy_data.get("id"):
                    proxy_id = proxy_data["id"]
                    cur_p = await db.execute("SELECT id FROM files WHERE id = ?", (proxy_id,))
                    if not await cur_p.fetchone():
                        await db.execute(
                            """
                            INSERT INTO files (
                                id, filename, mime_type, file_category, size_bytes, sha256,
                                is_split, total_parts, upload_status, kind, proxy_of,
                                proxy_profile, proxy_status, created_at
                            ) VALUES (?, ?, 'video/mp4', 'video', ?, ?, ?, ?, ?, 'proxy', ?, ?, 'ready', ?)
                            """,
                            (
                                proxy_id, f"{item.get('filename', 'video')}.proxy.mp4",
                                proxy_data.get("size_bytes", 0),
                                proxy_data.get("sha256", ""),
                                1 if len(proxy_data.get("parts", [])) > 1 else 0,
                                len(proxy_data.get("parts", [])) or 1,
                                proxy_data.get("status", "completed"),
                                file_id,
                                proxy_data.get("profile", "stream_v1"),
                                datetime.now(timezone.utc).isoformat()
                            )
                        )

                    for pp in proxy_data.get("parts", []):
                        pp_id = generate_part_id()
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO file_parts (
                                id, file_id, part_index, byte_offset, part_size_bytes,
                                part_sha256, telegram_message_id, telegram_channel_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                pp_id, proxy_id, pp.get("part_index", 0), pp.get("offset", 0),
                                pp.get("size", 0), pp.get("sha256", ""), pp.get("telegram_message_id"),
                                str(pp.get("telegram_channel_id") or settings.TELEGRAM_CHANNEL_ID)
                            )
                        )
                    await db.execute("UPDATE files SET proxy_status = 'ready' WHERE id = ?", (file_id,))

            await db.commit()

        # Delta Tail Sync: fetch any newer channel messages (min_id > max_message_id)
        # committed after the last index.json snapshot was pinned
        max_catalog_msg_id = pinned_catalog.get("max_message_id")
        if max_catalog_msg_id and isinstance(max_catalog_msg_id, int) and max_catalog_msg_id > 0:
            logger.info(f"Running Delta Tail Sync from Telegram WAL (min_id > {max_catalog_msg_id})...")
            try:
                tail_envelopes = await storage_client.scan_channel_envelopes(limit=1000, min_id=max_catalog_msg_id)
                if tail_envelopes:
                    logger.info(f"Delta Tail Sync: Found {len(tail_envelopes)} newer envelopes. Reconciling...")
                    tail_file_envelopes: Dict[str, List[dict]] = {}
                    for env in tail_envelopes:
                        fid = env.get("id")
                        if fid:
                            tail_file_envelopes.setdefault(fid, []).append(env)

                    tail_originals = {fid: p for fid, p in tail_file_envelopes.items() if p[0].get("k") != "p"}
                    tail_proxies = {fid: p for fid, p in tail_file_envelopes.items() if p[0].get("k") == "p"}

                    async with get_db() as db:
                        for file_id, parts in tail_originals.items():
                            first = parts[0]
                            cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
                            if not await cur.fetchone():
                                await db.execute(
                                    """
                                    INSERT INTO files (
                                        id, filename, mime_type, file_category, size_bytes, sha256,
                                        is_split, total_parts, upload_status, kind, proxy_status, nonce, encryption, created_at
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'original', 'none', ?, ?, ?)
                                    """,
                                    (
                                        file_id, first.get("name", "recovered_tail_file"),
                                        first.get("mime", "application/octet-stream"),
                                        first.get("cat", "other"), first.get("size", 0),
                                        first.get("sha256", ""), 1 if first.get("total", 1) > 1 else 0,
                                        first.get("total", 1), "completed",
                                        first.get("nonce"), first.get("enc", "none"),
                                        datetime.now(timezone.utc).isoformat()
                                    )
                                )
                                restored_count += 1

                            for p in parts:
                                part_id = generate_part_id()
                                await db.execute(
                                    """
                                    INSERT OR IGNORE INTO file_parts (
                                        id, file_id, part_index, byte_offset, part_size_bytes,
                                        part_sha256, telegram_message_id, telegram_channel_id
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        part_id, file_id, p.get("part", 0), p.get("offset", 0),
                                        p.get("size", 0), p.get("sha256", ""), p.get("telegram_message_id"),
                                        str(settings.TELEGRAM_CHANNEL_ID)
                                    )
                                )

                        for proxy_id, parts in tail_proxies.items():
                            first = parts[0]
                            parent_id = first.get("of") or first.get("orig")
                            if not parent_id:
                                logger.warning(f"Delta Tail Sync: Skipping proxy {proxy_id} without parent ID.")
                                continue
                            cur_orig = await db.execute("SELECT id FROM files WHERE id = ? AND kind = 'original'", (parent_id,))
                            if not await cur_orig.fetchone():
                                logger.warning(f"Delta Tail Sync: Skipping orphaned proxy {proxy_id}; parent original {parent_id} not found.")
                                continue
                            cur_p = await db.execute("SELECT id FROM files WHERE id = ?", (proxy_id,))
                            if not await cur_p.fetchone():
                                await db.execute(
                                    """
                                    INSERT INTO files (
                                        id, filename, mime_type, file_category, size_bytes, sha256,
                                        is_split, total_parts, upload_status, kind, proxy_of,
                                        proxy_profile, proxy_status, created_at
                                    ) VALUES (?, ?, 'video/mp4', 'video', ?, ?, ?, ?, ?, 'proxy', ?, ?, 'ready', ?)
                                    """,
                                    (
                                        proxy_id, f"{first.get('name', 'video')}.proxy.mp4",
                                        first.get("size", 0), first.get("sha256", ""),
                                        1 if len(parts) > 1 else 0, len(parts),
                                        "completed", parent_id, first.get("prof", "stream_v1"),
                                        datetime.now(timezone.utc).isoformat()
                                    )
                                )
                            for p in parts:
                                pp_id = generate_part_id()
                                await db.execute(
                                    """
                                    INSERT OR IGNORE INTO file_parts (
                                        id, file_id, part_index, byte_offset, part_size_bytes,
                                        part_sha256, telegram_message_id, telegram_channel_id
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        pp_id, proxy_id, p.get("part", 0), p.get("offset", 0),
                                        p.get("size", 0), p.get("sha256", ""), p.get("telegram_message_id"),
                                        str(settings.TELEGRAM_CHANNEL_ID)
                                    )
                                )
                            await db.execute("UPDATE files SET proxy_status = 'ready' WHERE id = ?", (parent_id,))

                        await db.commit()
            except Exception as e:
                logger.warning(f"Delta Tail Sync encountered error: {e}")

        return restored_count

    # Slow fallback: Scan channel envelopes backwards from HEAD
    logger.info("No pinned catalog found. Falling back to two-pass backward envelope history scan.")
    envelopes = await storage_client.scan_channel_envelopes(limit=limit)

    # Group parts by file_id for incomplete upload reconciliation
    file_envelopes: Dict[str, List[dict]] = {}
    for env in envelopes:
        fid = env.get("id")
        if fid:
            file_envelopes.setdefault(fid, []).append(env)

    # Constraint 1: Partition into originals and proxies for Two-Pass Reverse Scan
    original_envelopes = {fid: parts for fid, parts in file_envelopes.items() if parts[0].get("k") != "p"}
    proxy_envelopes = {fid: parts for fid, parts in file_envelopes.items() if parts[0].get("k") == "p"}

    async with get_db() as db:
        # Pass 1: Insert all parent originals first (satisfies foreign key constraints)
        for file_id, parts in original_envelopes.items():
            first = parts[0]
            total_expected = first.get("total", 1)
            parts_found = len(parts)
            is_interrupted = parts_found < total_expected

            async with db.execute("SELECT id FROM files WHERE id = ?", (file_id,)) as cur:
                existing_file = await cur.fetchone()
            if not existing_file:
                await db.execute(
                    """
                    INSERT INTO files (
                        id, filename, mime_type, file_category, size_bytes, sha256,
                        is_split, total_parts, upload_status, kind, proxy_status, nonce, encryption
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'original', 'none', ?, ?)
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

        # Pass 2: Insert/link proxies whose parent originals exist; ignore and log orphaned proxies
        for proxy_id, parts in proxy_envelopes.items():
            first = parts[0]
            orig_id = first.get("of")
            if not orig_id:
                logger.warning(f"Rebuild index: Skipping proxy {proxy_id} without 'of' parent pointer.")
                continue

            async with db.execute("SELECT id FROM files WHERE id = ? AND kind = 'original'", (orig_id,)) as cur_orig:
                parent_exists = await cur_orig.fetchone()
            if not parent_exists:
                logger.warning(f"Rebuild index: Skipping orphaned proxy {proxy_id}; parent original {orig_id} not found in channel.")
                continue

            async with db.execute("SELECT id FROM files WHERE id = ?", (proxy_id,)) as cur_p:
                proxy_exists = await cur_p.fetchone()
            if not proxy_exists:
                total_expected = first.get("total", 1)
                await db.execute(
                    """
                    INSERT INTO files (
                        id, filename, mime_type, file_category, size_bytes, sha256,
                        is_split, total_parts, upload_status, kind, proxy_of, proxy_profile, proxy_status
                    ) VALUES (?, ?, 'video/mp4', 'video', ?, ?, ?, ?, 'completed', 'proxy', ?, ?, 'ready')
                    """,
                    (
                        proxy_id, f"proxy_{proxy_id}.mp4",
                        first.get("size", 0),
                        first.get("sha256", ""),
                        1 if total_expected > 1 else 0,
                        total_expected,
                        orig_id,
                        first.get("profile", "stream_v1")
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
                        part_id, proxy_id, env.get("part", 0), env.get("offset", 0),
                        env.get("size", 0), env.get("sha256", ""), env.get("telegram_message_id"),
                        str(settings.TELEGRAM_CHANNEL_ID)
                    )
                )
                restored_count += 1

            # Update parent original's proxy_status to ready
            await db.execute("UPDATE files SET proxy_status = 'ready' WHERE id = ?", (orig_id,))

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
        needs_rebuild = (await cur.fetchone())[0] == 0

    # B-4: rebuild OUTSIDE the request's open connection — perform_rebuild_index()
    # opens its own get_db() context, which would contend for the same SQLite lock.
    if needs_rebuild and storage_client._is_connected:
        await perform_rebuild_index(limit=5000)

    async with get_db() as db:
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

        # Aggregate metrics across storage backends
        cur = await db.execute(
            """
            SELECT storage_backend, COUNT(*), COALESCE(SUM(size_bytes), 0)
            FROM files WHERE is_deleted = 0
            GROUP BY storage_backend
            """
        )
        backend_stats = {r[0]: {"count": r[1], "bytes": r[2]} for r in await cur.fetchall()}

    status_str = storage_client.online_status
    wait_secs = storage_client.flood_wait_seconds

    metrics_res = {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_parts": total_parts,
        "channel_id": settings.TELEGRAM_CHANNEL_ID or "NOT_CONFIGURED",
        "telegram_messages": telegram_messages,
        "online_status": status_str,
        "active_storage_backend": getattr(settings, "DEFAULT_STORAGE_BACKEND", "telegram"),
        "backend_stats": backend_stats,
        "catalog_sync": get_catalog_sync_state(),
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
        needs_rebuild = (await cur.fetchone())[0] == 0

    # B-4: rebuild OUTSIDE the request's open connection — perform_rebuild_index()
    # opens its own get_db() context, which would contend for the same SQLite lock.
    if needs_rebuild and storage_client._is_connected:
        await perform_rebuild_index(limit=5000)

    async with get_db() as db:
        query = """
            SELECT 
                f.*,
                p.id AS child_proxy_id,
                p.size_bytes AS child_proxy_size_bytes,
                p.upload_status AS child_proxy_upload_status,
                p.proxy_profile AS child_proxy_profile
            FROM files f
            LEFT JOIN files p ON p.proxy_of = f.id AND p.kind = 'proxy' AND p.is_deleted = 0
        """
        joins = []
        where_clauses = ["f.is_deleted = ?", "f.kind = 'original'"]
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

            # Proxy metadata resolution
            f_dict["proxy_id"] = f_dict.get("child_proxy_id")
            f_dict["proxy_profile"] = f_dict.get("child_proxy_profile")
            if f_dict.get("child_proxy_id") and f_dict.get("child_proxy_upload_status") == "completed":
                f_dict["proxy_status"] = "ready"
                f_dict["proxy_url"] = f"/api/v1/virtual-stream/{f_dict['child_proxy_id']}"
                f_dict["proxy_size_bytes"] = f_dict.get("child_proxy_size_bytes")
            else:
                f_dict["proxy_status"] = f_dict.get("proxy_status") or "none"
                f_dict["proxy_url"] = None
                f_dict["proxy_size_bytes"] = None

            f_dict["storage_backend"] = f_dict.get("storage_backend") or "telegram"
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
    target_backend = (
        request.headers.get("X-Storage-Backend")
        or request.query_params.get("storage_backend")
        or getattr(settings, "DEFAULT_STORAGE_BACKEND", "telegram")
        or "telegram"
    ).strip().lower()

    # Paywall Guard for Multi-Storage Backends (Pro feature)
    if target_backend != "telegram":
        from ..license.manager import license_manager
        if not await license_manager.is_backend_allowed(target_backend):
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "PRO_FEATURE_REQUIRED",
                    "feature": target_backend,
                    "message": f"{target_backend.upper()} storage adapter is a Vayom Drive Pro feature. Please activate your Pro license."
                }
            )

    if target_backend == "telegram" and storage_client.flood_wait_seconds > 60:
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
            has_thumb = await asyncio.to_thread(generate_webp_thumbnail, target_path, thumbnail_path)
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
            exif = await asyncio.to_thread(extract_image_exif, target_path)
        elif category == "video":
            exif = await extract_video_metadata(target_path)

        from ..crypto import vault_manager, generate_file_nonce, EncryptedFileSlice, compute_slice_sha256
        vault_key = vault_manager.get_key()
        # Invariant: Targeted Client Encryption (never encrypt streamable video)
        if category != "video" and (vault_key is not None or getattr(settings, "ENCRYPTION_ENABLED", False)):
            file_nonce = generate_file_nonce()
            encryption_mode = "ctr"
        else:
            file_nonce = None
            encryption_mode = "none"

        if target_backend == "telegram":
            # 4. Size Policy & Pointer-Offset Slicing (with precomputed SHA)
            parts = await asyncio.to_thread(split_file_if_needed, target_path, max_part_bytes=settings.MAX_PART_BYTES, precomputed_sha=sha256)
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

                    upload_progress_tracker[effective_task_id].update({
                        "task_id": effective_task_id,
                        "status": "uploading",
                        "stage": f"Uploading to Telegram (part {p_num}/{total_parts_count})...",
                        "uploaded_bytes": total_uploaded,
                        "total_bytes": total_size,
                        "progress_percent": pct,
                        "speed_bps": smoothed_speed,
                        "eta_seconds": eta,
                        "current_part": p_num,
                        "total_parts": total_parts_count,
                        "last_updated": now
                    })
                return progress_cb

            for part_idx, part in enumerate(parts):
                current_part_num = part_idx + 1
                part_offset = part.get("byte_offset", 0)
                part_size = part.get("part_size_bytes", 0)

                if encryption_mode == "ctr" and vault_key:
                    enc_slice = EncryptedFileSlice(
                        source=part.get("slice") or part["file_path"],
                        key=vault_key,
                        nonce_hex=file_nonce,
                        byte_offset=part_offset,
                        slice_size=part_size
                    )
                    upload_target = enc_slice
                    upload_sha = compute_slice_sha256(enc_slice)
                    part_record = {**part, "part_sha256": upload_sha}
                else:
                    upload_target = part.get("slice") or part["file_path"]
                    upload_sha = part["part_sha256"]
                    part_record = part

                cb = make_progress_cb(current_part_num, part_offset, part_size) if effective_task_id else None

                caption = format_part_caption(
                    file_id=file_id,
                    part_index=part["part_index"],
                    total_parts=len(parts),
                    byte_offset=part["byte_offset"],
                    part_size=part["part_size_bytes"],
                    part_sha256=upload_sha,
                    filename=file.filename,
                    category=category,
                    nonce=file_nonce,
                    encryption=encryption_mode,
                    kind="original"
                )
                res = await storage_client.upload_part(upload_target, caption=caption, progress_callback=cb)
                if isinstance(res, dict):
                    uploaded_parts.append({
                        **part_record,
                        "telegram_message_id": res["message_id"],
                        "telegram_channel_id": res.get("channel_id", str(settings.TELEGRAM_CHANNEL_ID)),
                        "telegram_file_id": res.get("file_id"),
                        "telegram_access_hash": res.get("access_hash"),
                        "storage_backend": "telegram",
                        "storage_uri": f"tg://{res.get('channel_id', str(settings.TELEGRAM_CHANNEL_ID))}/{res['message_id']}"
                    })
                else:
                    uploaded_parts.append({
                        **part_record,
                        "telegram_message_id": res,
                        "telegram_channel_id": str(settings.TELEGRAM_CHANNEL_ID),
                        "telegram_file_id": None,
                        "telegram_access_hash": None,
                        "storage_backend": "telegram",
                        "storage_uri": f"tg://{str(settings.TELEGRAM_CHANNEL_ID)}/{res}"
                    })
        else:
            # Native Unsplit Files for Non-Telegram Backends (Constraint 1)
            parts = [{
                "part_index": 0,
                "byte_offset": 0,
                "part_size_bytes": total_size,
                "part_sha256": sha256,
                "file_path": str(target_path)
            }]
            total_parts_count = 1

            if effective_task_id:
                upload_progress_tracker[effective_task_id].update({
                    "task_id": effective_task_id,
                    "status": "uploading",
                    "stage": f"Saving to {target_backend.upper()} storage...",
                    "uploaded_bytes": 0,
                    "total_bytes": total_size,
                    "speed_bps": 0,
                    "eta_seconds": 0,
                    "progress_percent": 25,
                    "current_part": 1,
                    "total_parts": 1,
                    "last_updated": time.time()
                })

            from ..storage.manager import get_storage_adapter
            adapter = get_storage_adapter(target_backend)
            res = await adapter.upload(
                local_path=target_path,
                file_id=file_id,
                filename=safe_filename,
                part_index=0,
                total_parts=1,
                byte_offset=0,
                part_size=total_size,
                sha256=sha256,
                mime_type=mime_type,
                category=category
            )
            uploaded_parts.append({
                "part_index": 0,
                "byte_offset": 0,
                "part_size_bytes": res.get("part_size_bytes", total_size),
                "part_sha256": sha256,
                "telegram_message_id": res.get("telegram_message_id", 0),
                "telegram_channel_id": res.get("telegram_channel_id", ""),
                "telegram_file_id": res.get("telegram_file_id"),
                "telegram_access_hash": res.get("telegram_access_hash"),
                "storage_backend": target_backend,
                "storage_uri": res.get("storage_uri", "")
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

        v_width = None
        v_height = None
        v_bitrate = None
        v_codec = None
        v_container = None
        v_duration = None
        if category == "video" and isinstance(exif, dict):
            dims = exif.get("dimensions", {})
            v_width = dims.get("width")
            v_height = dims.get("height")
            v_bitrate = exif.get("bitrate")
            v_codec = exif.get("codec")
            v_container = exif.get("container") or (Path(file.filename).suffix.lstrip(".").lower() if file.filename else None)
            v_duration = exif.get("duration_seconds")

        # I-13: persist capture date + GPS coordinates extracted from image EXIF
        taken_at = exif.get("taken_at") if isinstance(exif, dict) else None
        latitude = exif.get("latitude") if isinstance(exif, dict) else None
        longitude = exif.get("longitude") if isinstance(exif, dict) else None

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO files (
                    id, filename, mime_type, file_category, size_bytes, sha256,
                    is_split, total_parts, upload_status, nonce, encryption,
                    has_thumbnail, thumbnail_path, exif_json,
                    kind, proxy_status, width, height, video_bitrate_bps,
                    codec, container, duration_s, storage_backend,
                    taken_at, latitude, longitude
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'original', 'none', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id, file.filename, mime_type, category, total_size, sha256,
                    1 if len(parts) > 1 else 0, len(parts), "completed", file_nonce, encryption_mode,
                    1 if has_thumb else 0,
                    str(thumbnail_path) if has_thumb else None,
                    json.dumps(exif) if exif else None,
                    v_width, v_height, v_bitrate, v_codec, v_container, v_duration,
                    target_backend, taken_at, latitude, longitude
                )
            )

            for p in uploaded_parts:
                part_id = generate_part_id()
                await db.execute(
                    """
                    INSERT INTO file_parts (
                        id, file_id, part_index, byte_offset, part_size_bytes,
                        part_sha256, telegram_message_id, telegram_channel_id,
                        telegram_file_id, telegram_access_hash, storage_backend, storage_uri
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        part_id, file_id, p["part_index"], p["byte_offset"],
                        p["part_size_bytes"], p["part_sha256"], p.get("telegram_message_id", 0),
                        p.get("telegram_channel_id", str(settings.TELEGRAM_CHANNEL_ID)),
                        p.get("telegram_file_id"), p.get("telegram_access_hash"),
                        p.get("storage_backend", target_backend),
                        p.get("storage_uri", "")
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

        # Enqueue stream_v1 proxy generation if video and source is local
        if category == "video" and target_path.exists():
            proxy_source_dir = settings.DATA_DIR / "proxies_tmp"
            proxy_source_dir.mkdir(parents=True, exist_ok=True)
            proxy_source_path = proxy_source_dir / f"source_{file_id}.tmp"
            try:
                try:
                    os.link(target_path, proxy_source_path)
                except Exception:
                    shutil.copy2(target_path, proxy_source_path)
                asyncio.create_task(transcoding_manager.enqueue_proxy(file_id, proxy_source_path))
            except Exception as e:
                logger.warning(f"Could not stage proxy source for {file_id}: {e}")

        # Asynchronously sync updated master catalog or NAS snapshot
        if target_backend == "telegram":
            asyncio.create_task(sync_master_catalog())
        elif target_backend == "nas":
            from ..storage.manager import get_storage_adapter
            nas_adapter = get_storage_adapter("nas")
            if hasattr(nas_adapter, "save_catalog_snapshot"):
                asyncio.create_task(nas_adapter.save_catalog_snapshot())

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
            "id": file_id,
            "filename": file.filename,
            "size_bytes": total_size,
            "sha256": sha256,
            "total_parts": len(parts),
            "status": "confirmed",
            "storage_backend": target_backend,
            "nonce": file_nonce,
            "encryption": encryption_mode
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
    health = await storage_client.check_channel_health()
    health["catalog_sync"] = get_catalog_sync_state()
    return health


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


@router.get("/vault/status")
async def get_vault_encryption_status():
    """Returns whether the vault is initialized, unlocked, and whether 0600 key is stored."""
    from ..crypto import vault_manager
    return vault_manager.get_status()


@router.post("/vault/setup")
async def setup_vault(req: SetupVaultRequest):
    """Initializes vault with passphrase, generates 64-hex recovery key and saves 0600 key file."""
    if not req.passphrase or len(req.passphrase.strip()) < 4:
        raise HTTPException(status_code=400, detail="Passphrase must be at least 4 characters.")
    from ..crypto import vault_manager
    res = vault_manager.init_vault(req.passphrase.strip(), remember_device=req.remember_device)
    return res


@router.post("/vault/unlock")
async def unlock_vault(req: UnlockVaultRequest):
    """Unlocks in-memory vault encryption using master passphrase or 64-hex recovery key."""
    from ..crypto import vault_manager
    try:
        vault_manager.unlock(
            passphrase=req.passphrase,
            recovery_key=req.key,
            remember_device=req.remember_device
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "unlocked": True,
        "message": "Vault successfully unlocked"
    }


@router.post("/vault/lock")
async def lock_vault():
    """Clears active vault key from memory and removes local 0600 key file."""
    from ..crypto import vault_manager
    vault_manager.clear_key()
    return {
        "unlocked": False,
        "message": "Vault locked"
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

    from ..crypto import vault_manager, generate_file_nonce, EncryptedFileSlice, compute_slice_sha256
    # Targeted Client Encryption: never encrypt streamable video
    if category == "video":
        encryption = "none"
        file_nonce = None
    elif encryption == "ctr" or (encryption == "none" and (settings.ENCRYPTION_ENABLED or vault_manager.is_unlocked())):
        if vault_manager.is_unlocked() or settings.ENCRYPTION_ENABLED or encryption == "ctr":
            encryption = "ctr"
            if not file_nonce:
                file_nonce = generate_file_nonce()

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
    part_sha256: Optional[str] = Form(""),
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

    try:
        # B-5: staging chunk is always removed, on success or failure
        async with aiofiles.open(chunk_path, "wb") as out_file:
            while data := await chunk.read(1024 * 1024):
                await out_file.write(data)

        chunk_size = os.path.getsize(chunk_path)
        # B-2: keep the event loop responsive while hashing staging chunks
        computed_sha = await asyncio.to_thread(calculate_sha256, chunk_path)
        guessed_mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        category = detect_file_category(guessed_mime, filename)

        from ..crypto import vault_manager, EncryptedFileSlice, compute_slice_sha256
        vault_key = vault_manager.get_key()

        # Invariant: Never encrypt streamable video
        if category == "video":
            encryption = "none"
            nonce = None

        if encryption == "ctr" and vault_key and nonce:
            enc_slice = EncryptedFileSlice(
                source=chunk_path,
                key=vault_key,
                nonce_hex=nonce,
                byte_offset=byte_offset,
                slice_size=chunk_size
            )
            upload_target = enc_slice
            upload_sha = compute_slice_sha256(enc_slice)
        else:
            upload_target = str(chunk_path)
            upload_sha = computed_sha

        # For Part 0: extract thumbnail and video metadata immediately while the chunk is on local disk
        thumb_path = settings.THUMBNAIL_DIR / f"{file_id}.webp"
        video_meta = {}
        if part_index == 0:
            try:
                if category == "video":
                    await generate_video_thumbnail(chunk_path, thumb_path)
                    video_meta = await extract_video_metadata(chunk_path)
                elif category == "image":
                    # B-2: CPU-bound decode/EXIF runs in a worker thread
                    await asyncio.to_thread(generate_webp_thumbnail, chunk_path, thumb_path)
                    video_meta = await asyncio.to_thread(extract_image_exif, chunk_path)
            except Exception as meta_err:
                logger.warning(f"Failed extracting preview/metadata for {file_id}: {meta_err}")

        caption = format_part_caption(
            file_id=file_id,
            part_index=part_index,
            total_parts=total_parts,
            byte_offset=byte_offset,
            part_size=chunk_size,
            part_sha256=upload_sha,
            filename=filename,
            category=category,
            nonce=nonce,
            encryption=encryption,
            kind="original"
        )

        res = await storage_client.upload_part(upload_target, caption=caption)
        part_id = generate_part_id()

        msg_id = res["message_id"] if isinstance(res, dict) else res
        chan_id = res.get("channel_id", str(settings.TELEGRAM_CHANNEL_ID)) if isinstance(res, dict) else str(settings.TELEGRAM_CHANNEL_ID)
        tg_file_id = res.get("file_id") if isinstance(res, dict) else None
        tg_access_hash = res.get("access_hash") if isinstance(res, dict) else None

        v_width = None
        v_height = None
        v_bitrate = None
        v_codec = None
        v_container = None
        v_duration = None
        if category == "video" and isinstance(video_meta, dict):
            dims = video_meta.get("dimensions", {})
            v_width = dims.get("width")
            v_height = dims.get("height")
            v_bitrate = video_meta.get("bitrate")
            v_codec = video_meta.get("codec")
            v_container = video_meta.get("container") or (Path(filename).suffix.lstrip(".").lower() if filename else None)
            v_duration = video_meta.get("duration_seconds")

        # I-13: persist capture date + GPS coordinates extracted from image EXIF
        taken_at = video_meta.get("taken_at") if isinstance(video_meta, dict) else None
        latitude = video_meta.get("latitude") if isinstance(video_meta, dict) else None
        longitude = video_meta.get("longitude") if isinstance(video_meta, dict) else None

        async with get_db() as db:
            # Check/ensure file record exists
            cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
            if not await cur.fetchone():
                await db.execute(
                    """
                    INSERT INTO files (
                        id, filename, mime_type, file_category, size_bytes, sha256,
                        is_split, total_parts, upload_status, nonce, encryption,
                        has_thumbnail, thumbnail_path, exif_json,
                        kind, proxy_status, width, height, video_bitrate_bps,
                        codec, container, duration_s, taken_at, latitude, longitude
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'original', 'none', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id, filename, guessed_mime, category, total_size, "",
                        1 if total_parts > 1 else 0, total_parts, "uploading", nonce, encryption,
                        1 if thumb_path.exists() else 0,
                        str(thumb_path) if thumb_path.exists() else None,
                        json.dumps(video_meta) if video_meta else None,
                        v_width, v_height, v_bitrate, v_codec, v_container, v_duration,
                        taken_at, latitude, longitude
                    )
                )

            # B-5: re-uploaded parts must not orphan the previous Telegram message
            old_cur = await db.execute(
                "SELECT telegram_message_id FROM file_parts WHERE file_id = ? AND part_index = ?",
                (file_id, part_index)
            )
            old_row = await old_cur.fetchone()
            old_msg_id = old_row[0] if old_row else None
            if old_msg_id and old_msg_id != msg_id:
                logger.warning(f"Retry of part {part_index} for {file_id}: purging orphaned Telegram message {old_msg_id}...")
                asyncio.create_task(storage_client.delete_messages([old_msg_id]))

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
                    chunk_size, upload_sha, msg_id, chan_id,
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
                        exif_json = COALESCE(exif_json, ?),
                        width = COALESCE(width, ?),
                        height = COALESCE(height, ?),
                        video_bitrate_bps = COALESCE(video_bitrate_bps, ?),
                        codec = COALESCE(codec, ?),
                        container = COALESCE(container, ?),
                        duration_s = COALESCE(duration_s, ?),
                        taken_at = COALESCE(taken_at, ?),
                        latitude = COALESCE(latitude, ?),
                        longitude = COALESCE(longitude, ?)
                    WHERE id = ?
                    """,
                    (
                        guessed_mime,
                        has_thumb,
                        str(thumb_path) if has_thumb else None,
                        json.dumps(video_meta) if video_meta else None,
                        v_width,
                        v_height,
                        v_bitrate,
                        v_codec,
                        v_container,
                        v_duration,
                        taken_at,
                        latitude,
                        longitude,
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

    finally:
        # B-5: the staging chunk never survives this request (success or failure)
        try:
            if chunk_path.exists():
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


@router.post("/files/{file_id}/generate-proxy")
async def trigger_proxy_generation(
    file_id: str,
    force_download: bool = Query(False),
    source_path: Optional[str] = Query(None)
):
    """
    Manually triggers stream_v1 proxy generation for a video file.
    Requires local source file on disk or explicit user confirmation (force_download=True).
    """
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="File not found")
        f_dict = dict(row)

        if f_dict.get("file_category") != "video":
            raise HTTPException(status_code=400, detail="File is not a video")

        # Check if active proxy already exists
        p_cur = await db.execute("SELECT id FROM files WHERE proxy_of = ? AND upload_status = 'completed'", (file_id,))
        if await p_cur.fetchone():
            return {"status": "ready", "message": "Proxy already generated and ready."}

    # Search for local source in parameters, watch folders, or local folders
    local_source: Optional[Path] = None
    if source_path and Path(source_path).exists():
        local_source = Path(source_path)
    else:
        async with get_db() as db:
            w_cur = await db.execute("SELECT file_path FROM watch_folder_events WHERE file_id = ? ORDER BY id DESC LIMIT 1", (file_id,))
            w_row = await w_cur.fetchone()
            if w_row and Path(w_row[0]).exists():
                local_source = Path(w_row[0])
            else:
                candidates = [
                    Path.home() / "Desktop" / "gameplay" / f_dict["filename"],
                    Path.home() / "Desktop" / f_dict["filename"],
                    Path.home() / "Downloads" / f_dict["filename"],
                    settings.DATA_DIR / "uploads" / file_id / f_dict["filename"]
                ]
                for cand in candidates:
                    if cand.exists():
                        local_source = cand
                        break

    if not local_source and not force_download:
        raise HTTPException(
            status_code=400,
            detail="Local source video not found on disk. Explicit confirmation (force_download=True) required before downloading remote file."
        )

    if local_source:
        await transcoding_manager.enqueue_proxy(file_id, local_source)
        return {"status": "queued", "message": f"Proxy generation enqueued from local source ({local_source.name})."}

    return {"status": "queued", "message": "Proxy backfill enqueued."}


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

    # Parse range for downloads: do not cap range so downloads & resume stream continuously
    range_spec = parse_and_clamp_range(range_header, total_size, max_chunk_bytes=None)

    if not range_spec.is_satisfiable:
        headers = {
            "Content-Range": range_spec.content_range_header,
            "Accept-Ranges": "bytes"
        }
        return Response(status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE, headers=headers)

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(range_spec.content_length),
        "Content-Disposition": format_content_disposition(filename, disposition_type="attachment")
    }
    if range_spec.is_range:
        headers["Content-Range"] = range_spec.content_range_header
        status_code = status.HTTP_206_PARTIAL_CONTENT
    else:
        status_code = status.HTTP_200_OK

    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers, media_type=file_item["mime_type"])

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
        media_type=file_item["mime_type"]
    )


async def purge_parts_from_storage(parts: List[Dict[str, Any]]):
    """Purges parts from their respective underlying storage backends."""
    if not parts:
        return
    from ..storage.manager import get_storage_adapter
    tg_parts = [p for p in parts if (p.get("storage_backend") or "telegram") == "telegram"]
    if tg_parts:
        await storage_client.delete_parts(tg_parts)
    
    other_backends = {p.get("storage_backend") for p in parts if p.get("storage_backend") and p.get("storage_backend") != "telegram"}
    for b in other_backends:
        b_parts = [p for p in parts if p.get("storage_backend") == b]
        adapter = get_storage_adapter(b)
        await adapter.delete_parts(b_parts)


@router.delete("/files/{file_id}")
async def delete_file(file_id: str, purge: bool = False):
    """Soft deletes file to 30-day trash, or purges permanently from disk and Telegram (including child proxies)."""
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="File not found")

        if purge:
            # 1. Fetch parts for both original and child proxy before deletion
            p_cur = await db.execute(
                """
                SELECT telegram_message_id, telegram_channel_id, storage_backend, storage_uri FROM file_parts 
                WHERE file_id = ? OR file_id IN (SELECT id FROM files WHERE proxy_of = ?)
                """,
                (file_id, file_id)
            )
            parts = [dict(r) for r in await p_cur.fetchall()]

            # 2. Permanently delete parts from corresponding storage backends
            await purge_parts_from_storage(parts)

            # 3. Find proxy IDs to remove local thumbnail previews
            proxy_cur = await db.execute("SELECT id FROM files WHERE proxy_of = ?", (file_id,))
            proxy_ids = [r[0] for r in await proxy_cur.fetchall()]

            for fid in [file_id] + proxy_ids:
                thumb = settings.THUMBNAIL_DIR / f"{fid}.webp"
                if thumb.exists():
                    try:
                        os.remove(thumb)
                    except Exception:
                        pass

            # 4. Remove database records (foreign key cascade handles relations)
            await db.execute("DELETE FROM files WHERE id = ? OR proxy_of = ?", (file_id, file_id))
        else:
            await db.execute(
                "UPDATE files SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id = ? OR proxy_of = ?",
                (file_id, file_id)
            )
        await db.commit()
    asyncio.create_task(sync_master_catalog())
    return {"success": True, "purged": purge}


@router.post("/files/{file_id}/restore")
async def restore_file(file_id: str):
    """Restores soft-deleted file and its child proxy from trash."""
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="File not found")
        await db.execute(
            "UPDATE files SET is_deleted = 0, deleted_at = NULL WHERE id = ? OR proxy_of = ?",
            (file_id, file_id)
        )
        await db.commit()
    asyncio.create_task(sync_master_catalog())
    return {"success": True}


# --- Batch Operations ---
@router.post("/files/batch-trash")
async def batch_trash(body: BatchFileRequest):
    """Batch moves files and their child proxies to trash."""
    if not body.file_ids:
        return {"count": 0}
    placeholders = ",".join(["?"] * len(body.file_ids))
    async with get_db() as db:
        await db.execute(
            f"UPDATE files SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders}) OR proxy_of IN ({placeholders})",
            body.file_ids + body.file_ids
        )
        await db.commit()
    asyncio.create_task(sync_master_catalog())
    return {"count": len(body.file_ids)}


@router.post("/files/batch-restore")
async def batch_restore(body: BatchFileRequest):
    """Batch restores files and their child proxies from trash."""
    if not body.file_ids:
        return {"count": 0}
    placeholders = ",".join(["?"] * len(body.file_ids))
    async with get_db() as db:
        await db.execute(
            f"UPDATE files SET is_deleted = 0, deleted_at = NULL WHERE id IN ({placeholders}) OR proxy_of IN ({placeholders})",
            body.file_ids + body.file_ids
        )
        await db.commit()
    asyncio.create_task(sync_master_catalog())
    return {"count": len(body.file_ids)}


@router.post("/files/batch-purge")
async def batch_purge(body: BatchFileRequest):
    """Batch permanently purges files and child proxies from database, local disk, and Telegram channel."""
    if not body.file_ids:
        return {"count": 0, "messages_purged": 0}

    placeholders = ",".join(["?"] * len(body.file_ids))
    async with get_db() as db:
        # 1. Fetch parts for originals and their child proxies before deletion
        p_cur = await db.execute(
            f"""
            SELECT telegram_message_id, telegram_channel_id, storage_backend, storage_uri FROM file_parts 
            WHERE file_id IN ({placeholders}) OR file_id IN (SELECT id FROM files WHERE proxy_of IN ({placeholders}))
            """,
            body.file_ids + body.file_ids
        )
        parts = [dict(r) for r in await p_cur.fetchall()]

        # 2. Delete messages/files from corresponding storage backends
        await purge_parts_from_storage(parts)
        messages_purged = len(parts)

        # 3. Find proxy IDs to purge thumbnails
        proxy_cur = await db.execute(
            f"SELECT id FROM files WHERE proxy_of IN ({placeholders})",
            body.file_ids
        )
        proxy_ids = [r[0] for r in await proxy_cur.fetchall()]

        # 4. Delete database records
        await db.execute(
            f"DELETE FROM files WHERE id IN ({placeholders}) OR proxy_of IN ({placeholders})",
            body.file_ids + body.file_ids
        )
        await db.commit()

        # 5. Remove local thumbnails
        for fid in body.file_ids + proxy_ids:
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
    and their proxies from SQLite, disk thumbnails, and the storage backends.
    """
    async with get_db() as db:
        # 1. Get all soft-deleted file IDs
        cur = await db.execute("SELECT id FROM files WHERE is_deleted = 1")
        rows = await cur.fetchall()
        deleted_ids = [r[0] for r in rows]

        if not deleted_ids:
            return {"count": 0, "messages_purged": 0}

        placeholders = ",".join(["?"] * len(deleted_ids))

        # 2. Get all message parts for these files and child proxies
        p_cur = await db.execute(
            f"""
            SELECT telegram_message_id, telegram_channel_id, storage_backend, storage_uri FROM file_parts 
            WHERE file_id IN ({placeholders}) OR file_id IN (SELECT id FROM files WHERE proxy_of IN ({placeholders}))
            """,
            deleted_ids + deleted_ids
        )
        parts = [dict(r) for r in await p_cur.fetchall()]

        # 3. Delete from underlying storage backends
        await purge_parts_from_storage(parts)
        messages_purged = len(parts)

        # 4. Find proxy IDs for thumbnail removal
        proxy_cur = await db.execute(
            f"SELECT id FROM files WHERE proxy_of IN ({placeholders})",
            deleted_ids
        )
        proxy_ids = [r[0] for r in await proxy_cur.fetchall()]

        # 5. Delete files from DB
        await db.execute(
            f"DELETE FROM files WHERE id IN ({placeholders}) OR proxy_of IN ({placeholders})",
            deleted_ids + deleted_ids
        )
        await db.commit()

        # 6. Delete thumbnails
        for fid in set(deleted_ids + proxy_ids):
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


# --- Share Links Endpoints (F-4: Gated behind ENABLE_SHARE_LINKS until frontend UI is built) ---
def _require_share_links_enabled():
    """F-4: gates unused share-link endpoints behind a flag to eliminate attack surface."""
    if not getattr(settings, "ENABLE_SHARE_LINKS", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share links feature is disabled in v1"
        )

@router.get("/shares")
async def list_shares():
    """Lists all active share links."""
    _require_share_links_enabled()
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
    _require_share_links_enabled()
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
    _require_share_links_enabled()
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
    _require_share_links_enabled()
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


# --- NAS Storage Endpoints ---
@router.post("/nas/sync")
async def sync_nas_vault():
    """
    Zero-copy in-place sync for NAS vault:
    Indexes existing files and auto-maps subfolders to albums.
    """
    from ..storage.manager import get_storage_adapter
    nas_adapter = get_storage_adapter("nas")
    if not hasattr(nas_adapter, "sync_directory"):
        raise HTTPException(status_code=400, detail="NAS adapter not available")
    result = await nas_adapter.sync_directory()
    return {"status": "ok", **result}


@router.get("/nas/health")
async def check_nas_health():
    """Checks NAS vault connection, mount status, and free space."""
    from ..storage.manager import get_storage_adapter
    nas_adapter = get_storage_adapter("nas")
    return await nas_adapter.health_check()


# --- S3 Storage Endpoints ---
@router.get("/storage/s3/health")
async def check_s3_health():
    """Checks S3 / R2 / MinIO connectivity and bucket accessibility with secret redaction."""
    from ..storage.manager import get_storage_adapter
    s3_adapter = get_storage_adapter("s3")
    return await s3_adapter.health_check()


@router.post("/storage/s3/snapshot")
async def save_s3_snapshot():
    """Triggers atomic catalog snapshot write to S3 (.vayom/index.json)."""
    from ..storage.manager import get_storage_adapter
    s3_adapter = get_storage_adapter("s3")
    await s3_adapter.save_catalog_snapshot()
    return {"status": "ok", "message": "S3 catalog snapshot created successfully"}


# --- Nextcloud WebDAV Storage Endpoints ---
@router.get("/storage/nextcloud/health")
async def check_nextcloud_health():
    """Checks Nextcloud WebDAV connectivity and vault status with password redaction."""
    from ..storage.manager import get_storage_adapter
    nc_adapter = get_storage_adapter("nextcloud")
    return await nc_adapter.health_check()


@router.post("/storage/nextcloud/snapshot")
async def save_nextcloud_snapshot():
    """Triggers atomic catalog snapshot write to Nextcloud (.vayom/index.json via MOVE)."""
    from ..storage.manager import get_storage_adapter
    nc_adapter = get_storage_adapter("nextcloud")
    await nc_adapter.save_catalog_snapshot()
    return {"status": "ok", "message": "Nextcloud catalog snapshot created successfully"}


# --- Cryptographic Offline Licensing Endpoints ---
class LicenseActivationRequest(BaseModel):
    license_key: str


@router.get("/license")
async def get_license_info():
    """Returns the current offline cryptographic Pro license status."""
    from ..license.manager import license_manager
    return await license_manager.get_license_status()


@router.post("/license/activate")
async def activate_license_key(body: LicenseActivationRequest):
    """Activates an offline cryptographic Pro license key and stores it in SQLite."""
    from ..license.manager import license_manager
    from ..license.crypto import InvalidLicenseError, ExpiredLicenseError
    try:
        updated = await license_manager.activate_license(body.license_key)
        return {"status": "activated", "license": updated}
    except InvalidLicenseError as e:
        raise HTTPException(status_code=400, detail={"error": "INVALID_LICENSE", "message": str(e)})
    except ExpiredLicenseError as e:
        raise HTTPException(status_code=400, detail={"error": "EXPIRED_LICENSE", "message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": "ACTIVATION_FAILED", "message": str(e)})


# --- Multi-Storage Configuration Endpoints (S3 & Nextcloud) ---
class S3ConfigRequest(BaseModel):
    endpoint_url: str
    bucket_name: str
    access_key_id: str
    secret_access_key: Optional[str] = None
    region_name: Optional[str] = "us-east-1"
    vault_prefix: Optional[str] = "vayom_vault"


class NextcloudConfigRequest(BaseModel):
    webdav_url: str
    username: str
    app_password: Optional[str] = None
    vault_prefix: Optional[str] = "vayom_vault"


@router.get("/storage/config")
async def get_storage_configs():
    """
    Returns non-sensitive configuration status for all storage backends.
    Secret access keys and app passwords are redacted per Constraint 6.
    """
    nas_configured = False
    nas_root_str = str(getattr(settings, "NAS_VAULT_ROOT", ""))
    if nas_root_str and Path(nas_root_str).exists():
        nas_configured = True

    s3_configured = bool(
        getattr(settings, "S3_BUCKET_NAME", "") and
        getattr(settings, "S3_ACCESS_KEY_ID", "") and
        getattr(settings, "S3_SECRET_ACCESS_KEY", "")
    )

    nc_configured = bool(
        getattr(settings, "NEXTCLOUD_WEBDAV_URL", "") and
        getattr(settings, "NEXTCLOUD_USERNAME", "") and
        getattr(settings, "NEXTCLOUD_APP_PASSWORD", "")
    )

    return {
        "nas": {
            "vault_root": nas_root_str,
            "configured": nas_configured
        },
        "s3": {
            "endpoint_url": getattr(settings, "S3_ENDPOINT_URL", "") or "https://s3.amazonaws.com",
            "bucket_name": getattr(settings, "S3_BUCKET_NAME", ""),
            "access_key_id": getattr(settings, "S3_ACCESS_KEY_ID", ""),
            "region_name": getattr(settings, "S3_REGION_NAME", "us-east-1"),
            "vault_prefix": getattr(settings, "S3_VAULT_PREFIX", "vayom_vault"),
            "has_secret_key": bool(getattr(settings, "S3_SECRET_ACCESS_KEY", "")),
            "configured": s3_configured
        },
        "nextcloud": {
            "webdav_url": getattr(settings, "NEXTCLOUD_WEBDAV_URL", ""),
            "username": getattr(settings, "NEXTCLOUD_USERNAME", ""),
            "vault_prefix": getattr(settings, "NEXTCLOUD_VAULT_PREFIX", "vayom_vault"),
            "has_password": bool(getattr(settings, "NEXTCLOUD_APP_PASSWORD", "")),
            "configured": nc_configured
        }
    }


@router.post("/storage/s3/config")
async def save_s3_config(body: S3ConfigRequest):
    """
    Saves and activates S3 / R2 / MinIO storage configuration.
    Persists to SQLite system_settings and tests connectivity immediately.
    """
    from ..storage.manager import register_storage_adapter
    from ..storage.adapters.s3 import S3StorageAdapter

    secret_key = (body.secret_access_key or "").strip()
    if not secret_key:
        secret_key = getattr(settings, "S3_SECRET_ACCESS_KEY", "")

    if not secret_key:
        raise HTTPException(status_code=400, detail="Secret access key is required.")

    clean_endpoint = body.endpoint_url.strip().rstrip("/")
    clean_bucket = body.bucket_name.strip()
    clean_key_id = body.access_key_id.strip()
    clean_region = (body.region_name or "us-east-1").strip()
    clean_prefix = (body.vault_prefix or "vayom_vault").strip("/").strip() or "vayom_vault"

    settings.S3_ENDPOINT_URL = clean_endpoint
    settings.S3_BUCKET_NAME = clean_bucket
    settings.S3_ACCESS_KEY_ID = clean_key_id
    settings.S3_SECRET_ACCESS_KEY = secret_key
    settings.S3_REGION_NAME = clean_region
    settings.S3_VAULT_PREFIX = clean_prefix

    config_data = {
        "endpoint_url": clean_endpoint,
        "bucket_name": clean_bucket,
        "access_key_id": clean_key_id,
        "secret_access_key": secret_key,
        "region_name": clean_region,
        "vault_prefix": clean_prefix
    }

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO system_settings (key, value, updated_at) 
            VALUES ('s3_config', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (json.dumps(config_data),)
        )
        await db.commit()

    adapter = S3StorageAdapter()
    register_storage_adapter("s3", adapter)
    health = await adapter.health_check()

    return {
        "status": "saved",
        "configured": True,
        "health": health
    }


@router.post("/storage/nextcloud/config")
async def save_nextcloud_config(body: NextcloudConfigRequest):
    """
    Saves and activates Nextcloud WebDAV storage configuration.
    Persists to SQLite system_settings and tests connectivity immediately.
    """
    from ..storage.manager import register_storage_adapter
    from ..storage.adapters.nextcloud import NextcloudWebDAVAdapter

    password = (body.app_password or "").strip()
    if not password:
        password = getattr(settings, "NEXTCLOUD_APP_PASSWORD", "")

    if not password:
        raise HTTPException(status_code=400, detail="App password is required.")

    clean_url = body.webdav_url.strip()
    if not clean_url.endswith("/"):
        clean_url += "/"
    clean_user = body.username.strip()
    clean_prefix = (body.vault_prefix or "vayom_vault").strip("/").strip() or "vayom_vault"

    settings.NEXTCLOUD_WEBDAV_URL = clean_url
    settings.NEXTCLOUD_USERNAME = clean_user
    settings.NEXTCLOUD_APP_PASSWORD = password
    settings.NEXTCLOUD_VAULT_PREFIX = clean_prefix

    config_data = {
        "webdav_url": clean_url,
        "username": clean_user,
        "app_password": password,
        "vault_prefix": clean_prefix
    }

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO system_settings (key, value, updated_at) 
            VALUES ('nextcloud_config', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (json.dumps(config_data),)
        )
        await db.commit()

    adapter = NextcloudWebDAVAdapter()
    register_storage_adapter("nextcloud", adapter)
    health = await adapter.health_check()

    return {
        "status": "saved",
        "configured": True,
        "health": health
    }


async def load_persistent_storage_configs():
    """Loads saved S3 and Nextcloud storage configurations from system_settings into settings and adapters."""
    try:
        from ..storage.manager import register_storage_adapter
        from ..storage.adapters.s3 import S3StorageAdapter
        from ..storage.adapters.nextcloud import NextcloudWebDAVAdapter

        async with get_db() as db:
            cur = await db.execute("SELECT key, value FROM system_settings WHERE key IN ('s3_config', 'nextcloud_config')")
            rows = await cur.fetchall()
            for key, val in rows:
                try:
                    data = json.loads(val)
                    if key == "s3_config":
                        if data.get("endpoint_url"): settings.S3_ENDPOINT_URL = data["endpoint_url"].strip().rstrip("/")
                        if data.get("bucket_name"): settings.S3_BUCKET_NAME = data["bucket_name"].strip()
                        if data.get("access_key_id"): settings.S3_ACCESS_KEY_ID = data["access_key_id"].strip()
                        if data.get("secret_access_key"): settings.S3_SECRET_ACCESS_KEY = data["secret_access_key"]
                        if data.get("region_name"): settings.S3_REGION_NAME = data["region_name"].strip()
                        if data.get("vault_prefix"): settings.S3_VAULT_PREFIX = data["vault_prefix"].strip("/").strip()
                        register_storage_adapter("s3", S3StorageAdapter())
                        logger.info("Loaded persistent S3 storage configuration from database.")
                    elif key == "nextcloud_config":
                        if data.get("webdav_url"):
                            url = data["webdav_url"].strip()
                            if not url.endswith("/"): url += "/"
                            settings.NEXTCLOUD_WEBDAV_URL = url
                        if data.get("username"): settings.NEXTCLOUD_USERNAME = data["username"].strip()
                        if data.get("app_password"): settings.NEXTCLOUD_APP_PASSWORD = data["app_password"]
                        if data.get("vault_prefix"): settings.NEXTCLOUD_VAULT_PREFIX = data["vault_prefix"].strip("/").strip()
                        register_storage_adapter("nextcloud", NextcloudWebDAVAdapter())
                        logger.info("Loaded persistent Nextcloud WebDAV storage configuration from database.")
                except Exception as parse_err:
                    logger.warning(f"Error parsing persistent config for {key}: {parse_err}")
    except Exception as e:
        logger.warning(f"Could not load persistent storage configs from database: {e}")



