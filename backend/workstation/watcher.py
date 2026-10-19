import asyncio
import json
import logging
import mimetypes
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Set, List

import watchfiles
from watchfiles import Change

from ..config import settings
from ..db.connection import get_db
from ..storage.client import storage_client, format_part_caption
from ..compression.splitter import split_file_if_needed, calculate_sha256
from ..index.metadata import detect_file_category, generate_webp_thumbnail, generate_video_thumbnail, extract_image_exif, extract_video_metadata
from ..transcoding.manager import transcoding_manager
from ..crypto import EncryptedFileSlice, compute_slice_sha256, generate_file_nonce, vault_manager
from .security import validate_watch_folder_path, is_safe_file_path, is_contained

logger = logging.getLogger("vayom.workstation.watcher")

# I-5: bounded ingestion pipeline — change events and scans feed one queue, a fixed worker pool drains it
INGEST_QUEUE_MAX = 1024
INGEST_WORKER_COUNT = 4


class LocalFolderWatcher:
    """
    Background directory ingestion engine.
    Monitors registered local directories, enforces a 3-second file stability debounce,
    performs SHA-256 deduplication, extracts EXIF/video codec metadata,
    chunks > 1.9 GB files, uploads encrypted parts to Telegram storage channel,
    and updates SQLite index and WAL master catalog.
    """

    def __init__(self):
        self._running: bool = False
        self._worker_task: Optional[asyncio.Task] = None
        self._reload_event: asyncio.Event = asyncio.Event()
        self._needs_reload: bool = False
        self._iteration_stop_event: Optional[asyncio.Event] = None
        self._syncing_paths: Set[str] = set()
        # I-5: bounded queue + fixed worker pool (created lazily / on start)
        self._ingest_queue: Optional[asyncio.Queue] = None
        self._ingest_workers: List[asyncio.Task] = []
        self._queued_paths: Set[str] = set()
        # I-34: change events that arrived while their path was actively syncing.
        # They are replayed by the ingest worker once the active run finishes so
        # the newest bytes are never skipped (auto-delete is also gated on this).
        self._dirty_items: Dict[str, Dict[str, Any]] = {}
        self._stopping: bool = False
        self._producer_tasks: Set[asyncio.Task] = set()

    async def start(self):
        """Starts the background directory monitoring worker."""
        if self._running:
            return
        self._running = True
        self._stopping = False
        # I-22: crash recovery — anything still marked 'syncing' from a previous run
        # (power loss / hard restart) is reconciled so files never stay stuck forever.
        try:
            async with get_db() as db:
                await db.execute("UPDATE watch_folder_events SET status = 'interrupted' WHERE status = 'syncing'")
                await db.commit()
            logger.info("Reconciled stale 'syncing' watch folder events on startup.")
        except Exception as e:
            logger.warning(f"Startup reconciliation of stale syncing events failed: {e}")
        self._ensure_ingest_pool()
        self._worker_task = asyncio.create_task(self._watch_loop(), name="vayom_workstation_watcher")
        logger.info("LocalFolderWatcher service started.")

    async def stop(self):
        """Stops the watcher worker cleanly."""
        self._running = False
        self._stopping = True
        self._needs_reload = False
        if self._iteration_stop_event:
            self._iteration_stop_event.set()
        self._reload_event.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        # I-5: cancel pending scan producers, then the bounded ingestion pool
        for producer in list(self._producer_tasks):
            producer.cancel()
        if self._producer_tasks:
            await asyncio.gather(*self._producer_tasks, return_exceptions=True)
            self._producer_tasks.clear()
        for worker in self._ingest_workers:
            worker.cancel()
        if self._ingest_workers:
            await asyncio.gather(*self._ingest_workers, return_exceptions=True)
        self._ingest_workers = []
        self._ingest_queue = None
        self._queued_paths.clear()
        self._dirty_items.clear()
        logger.info("LocalFolderWatcher service stopped.")

    async def reload(self):
        """
        I-19: Signals the watcher to reload active folders from the database.
        Sets both _needs_reload and _reload_event and triggers any active iteration
        stop event so reloads arriving during folder queries or loop setup are never lost.
        """
        self._needs_reload = True
        self._reload_event.set()
        if self._iteration_stop_event and not self._iteration_stop_event.is_set():
            self._iteration_stop_event.set()

    def _ensure_ingest_pool(self):
        """I-5: lazily creates the bounded queue and the fixed worker pool."""
        if self._ingest_queue is None:
            self._ingest_queue = asyncio.Queue(maxsize=INGEST_QUEUE_MAX)
        if not self._ingest_workers:
            self._ingest_workers = [
                asyncio.create_task(self._ingest_worker(), name=f"vayom_ingest_worker_{i}")
                for i in range(INGEST_WORKER_COUNT)
            ]

    async def _enqueue(
        self,
        folder_id: str,
        file_path: Path,
        auto_delete_local: bool = False,
        target_album_id: Optional[str] = None
    ) -> bool:
        """
        I-5: single entry point for all ingestion work. Applies queue backpressure and
        deduplicates paths that are already queued or being processed.
        """
        self._ensure_ingest_pool()
        key = str(file_path.resolve())
        item = {
            "folder_id": folder_id,
            "file_path": file_path,
            "auto_delete_local": auto_delete_local,
            "target_album_id": target_album_id
        }
        if key in self._syncing_paths:
            # I-34: never drop a change that lands mid-sync — remember the newest
            # event and replay it once the active ingest finishes. The stability
            # check then re-runs against the newest bytes (critical with
            # auto_delete_local, which is gated on this marker).
            self._dirty_items[key] = item
            return False
        if key in self._queued_paths:
            return False
        self._queued_paths.add(key)
        try:
            await self._ingest_queue.put((key, item))
        except BaseException:
            self._queued_paths.discard(key)
            raise
        return True

    async def _ingest_worker(self):
        """Drains the bounded queue, running process_file for each item."""
        while True:
            key, item = await self._ingest_queue.get()
            try:
                await self.process_file(**item)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Ingest worker failed on {item.get('file_path')}: {e}", exc_info=True)
            finally:
                self._queued_paths.discard(key)
                self._ingest_queue.task_done()
                # I-34: if a change event arrived while this path was syncing,
                # replay it through the normal queue now that the path is free.
                dirty_item = self._dirty_items.pop(key, None)
                if dirty_item is not None and not self._stopping:
                    try:
                        await self._enqueue(**dirty_item)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(f"Failed to re-enqueue changed path {dirty_item.get('file_path')}: {e}")

    async def _get_active_folders(self) -> List[Dict[str, Any]]:
        """Queries database for all active watch folders."""
        try:
            async with get_db() as db:
                cur = await db.execute(
                    "SELECT id, path, label, target_album_id, auto_delete_local, is_active FROM watch_folders WHERE is_active = 1"
                )
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to query active watch folders: {e}")
            return []

    @staticmethod
    def _match_folder_config(
        path_str: str, valid_folders: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        I-8: selects the watch folder config that owns a changed path.

        Matching is done by path components, never by raw string prefix, so a
        sibling folder sharing a prefix (e.g. /data/media1 vs /data/media) can
        no longer inherit the wrong folder_id, target_album_id, or
        auto_delete_local flag. When roots are nested, the most specific
        (longest) root wins instead of whichever dict entry came first.
        """
        changed = Path(os.path.normpath(path_str))
        best_cfg: Optional[Dict[str, Any]] = None
        best_depth = -1
        for folder_p_str, cfg in valid_folders.items():
            root = Path(os.path.normpath(folder_p_str))
            if len(root.parts) > best_depth and changed.is_relative_to(root):
                best_cfg = cfg
                best_depth = len(root.parts)
        return best_cfg

    async def _watch_loop(self):
        """Continuous watching loop using watchfiles.awatch."""
        while self._running:
            self._needs_reload = False
            self._reload_event.clear()
            folders = await self._get_active_folders()

            # I-19: reload arrived while querying folders; restart loop to pick up new rows
            if self._needs_reload or self._reload_event.is_set():
                continue

            valid_folders: Dict[str, Dict[str, Any]] = {}
            for f in folders:
                folder_path = Path(f["path"]).expanduser().resolve()
                if folder_path.is_dir():
                    try:
                        validate_watch_folder_path(str(folder_path))
                        valid_folders[str(folder_path)] = f
                    except Exception as e:
                        logger.warning(f"Skipping watch folder {folder_path} due to security validation error: {e}")
                else:
                    logger.warning(f"Watch folder path does not exist or is not a directory: {f['path']}")

            # I-19: reload arrived during path validation; restart loop
            if self._needs_reload or self._reload_event.is_set():
                continue

            if not valid_folders:
                # No active watch folders; wait for reload signal or timeout
                try:
                    await asyncio.wait_for(self._reload_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    break
                continue

            active_paths = list(valid_folders.keys())
            iteration_stop = asyncio.Event()
            self._iteration_stop_event = iteration_stop

            # I-19: atomic stop check before launching awatch
            if self._needs_reload or self._reload_event.is_set():
                iteration_stop.set()
                continue

            logger.info(f"Watching {len(active_paths)} directory(ies) for media ingestion: {active_paths}")

            try:
                async for changes in watchfiles.awatch(
                    *active_paths,
                    stop_event=iteration_stop,
                    recursive=True,
                    debounce=1600
                ):
                    for change_type, path_str in changes:
                        if change_type in (Change.added, Change.modified):
                            file_p = Path(path_str)
                            if self._should_ignore(file_p) or not is_safe_file_path(file_p):
                                logger.warning(f"Skipping ignored or unsafe file change event: {file_p}")
                                continue
                            # I-8: component-aware match, longest root wins — a raw
                            # startswith let /data/media1 inherit the /data/media config.
                            matching_config = self._match_folder_config(path_str, valid_folders)

                            if matching_config:
                                # I-5: queued with backpressure instead of one unbounded task per event
                                await self._enqueue(
                                    folder_id=matching_config["id"],
                                    file_path=file_p,
                                    auto_delete_local=bool(matching_config.get("auto_delete_local")),
                                    target_album_id=matching_config.get("target_album_id")
                                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Watch loop iteration encountered error: {e}")
                await asyncio.sleep(2.0)

    async def scan_folder(self, folder_id: str) -> int:
        """
        Performs an on-demand full recursive rescan of a watch folder.
        Returns the number of files queued for processing.
        """
        async with get_db() as db:
            cur = await db.execute(
                "SELECT id, path, target_album_id, auto_delete_local FROM watch_folders WHERE id = ?",
                (folder_id,)
            )
            row = await cur.fetchone()
            if not row:
                raise ValueError("Watch folder not found")
            folder_info = dict(row)

        folder_path = Path(folder_info["path"]).expanduser().resolve()
        if not folder_path.is_dir():
            raise ValueError(f"Watch folder path does not exist: {folder_path}")
        validate_watch_folder_path(str(folder_path))

        # I-18: recursive tree walk runs in a worker thread so scanning huge
        # folders never stalls the FastAPI event loop.
        pending: List[Path] = await asyncio.to_thread(self._walk_sync, folder_path)

        count = len(pending)

        # I-5: hand the batch to a single bounded producer instead of one task per file;
        # the producer applies queue backpressure while this endpoint responds immediately.
        self._ensure_ingest_pool()

        async def _scan_producer():
            try:
                for item in pending:
                    await self._enqueue(
                        folder_id=folder_info["id"],
                        file_path=item,
                        auto_delete_local=bool(folder_info.get("auto_delete_local")),
                        target_album_id=folder_info.get("target_album_id")
                    )
            except asyncio.CancelledError:
                pass

        producer = asyncio.create_task(_scan_producer(), name=f"vayom_scan_producer_{folder_id}")
        self._producer_tasks.add(producer)
        producer.add_done_callback(self._producer_tasks.discard)

        return count

    def _walk_sync(self, folder_path: Path) -> List[Path]:
        """I-18: synchronous recursive walk (always invoked via asyncio.to_thread)."""
        pending: List[Path] = []
        for item in folder_path.rglob("*"):
            if item.is_file() and not self._should_ignore(item):
                if not is_safe_file_path(item):
                    logger.warning(f"Skipping unsafe file during scan: {item}")
                    continue
                pending.append(item)
        return pending

    def _should_ignore(self, path: Path) -> bool:
        """
        Determines if a file is temporary, hidden, system generated, database/WAL,
        or resides within the application's internal data/cache directories.
        """
        name = path.name
        if name.startswith(".") or name.startswith("~"):
            return True
        ignored_extensions = {
            ".tmp", ".crdownload", ".part", ".swp", ".lock",
            ".db", ".db-wal", ".db-shm", ".journal"
        }
        if path.suffix.lower() in ignored_extensions:
            return True

        # I-7: Hard-exclude application DATA_DIR, UPLOAD_TEMP_DIR, THUMBNAIL_DIR, and ROOT_DIR
        try:
            real_p = os.path.realpath(str(path))
            app_dirs = (
                settings.DATA_DIR,
                settings.UPLOAD_TEMP_DIR,
                settings.THUMBNAIL_DIR,
                settings.ROOT_DIR,
            )
            for app_dir in app_dirs:
                if is_contained(real_p, os.path.realpath(str(app_dir))):
                    return True
        except Exception:
            return True

        return False

    async def _wait_for_file_stability(self, file_path: Path) -> Optional[int]:
        """
        I-21: Returns the stable size of file_path once BOTH its size and mtime have
        stopped changing across consecutive 3 s intervals, or None if the file is
        missing, empty, or never stabilizes within the 30 s window (warning logged).
        """
        try:
            st = file_path.stat()
        except OSError:
            return None

        if st.st_size > 0 and (time.time() - st.st_mtime) >= 3.0:
            return st.st_size

        prev_size = -1
        prev_mtime: Optional[float] = None
        for _ in range(10):  # Maximum 30s stabilization window
            if not file_path.exists() or not file_path.is_file():
                return None
            try:
                curr = file_path.stat()
            except Exception:
                return None

            if curr.st_size > 0 and curr.st_size == prev_size and curr.st_mtime == prev_mtime:
                return curr.st_size

            prev_size = curr.st_size
            prev_mtime = curr.st_mtime
            await asyncio.sleep(3.0)

        if prev_size > 0:
            logger.warning(f"File did not stabilize within 30s window; deferring ingest until the next change event: {file_path}")
        return None

    async def process_file(
        self,
        folder_id: str,
        file_path: Path,
        auto_delete_local: bool = False,
        target_album_id: Optional[str] = None
    ):
        """
        Processes a single detected file:
        1. Debounces file size over 3 seconds to guarantee write completion.
        2. Computes SHA-256 for instant deduplication.
        3. Extracts EXIF / video codec metadata and generates WebP thumbnail.
        4. Slices file if > 1.9 GB and uploads via storage_client.upload_part.
        5. Atomically commits record to SQLite and synchronizes WAL master catalog.
        """
        str_path = str(file_path.resolve())
        if str_path in self._syncing_paths:
            return

        if self._should_ignore(file_path):
            return

        if not is_safe_file_path(file_path):
            logger.warning(f"Refusing to process unsafe file path: {file_path}")
            return

        self._syncing_paths.add(str_path)
        event_id = f"we_{uuid.uuid4().hex}"
        split_temp_dir: Optional[Path] = None

        try:
            # 1. Stability check (I-21): quiet files (mtime older than 3 s) ingest directly;
            # recently-written files must keep BOTH size and mtime unchanged across a 3 s
            # window, otherwise the ingest is deferred until the next change event.
            if not file_path.is_file():
                return
            total_size = await self._wait_for_file_stability(file_path)
            if total_size is None:
                return

            # 2. SHA-256 Calculation & Deduplication Check (offloaded — never blocks the event loop)
            sha256 = await asyncio.to_thread(calculate_sha256, file_path)

            async with get_db() as db:
                # Check if already present in files table
                cur = await db.execute("SELECT id FROM files WHERE sha256 = ? AND is_deleted = 0", (sha256,))
                existing_file = await cur.fetchone()

                if existing_file:
                    existing_id = existing_file[0]
                    logger.info(f"File {file_path.name} already ingested (id={existing_id}). Recording sync event.")
                    await db.execute(
                        """
                        INSERT INTO watch_folder_events (
                            id, folder_id, file_path, file_size_bytes, status, file_id
                        ) VALUES (?, ?, ?, ?, 'synced', ?)
                        """,
                        (event_id, folder_id, str_path, total_size, existing_id)
                    )
                    await db.execute(
                        "UPDATE watch_folders SET last_synced_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (folder_id,)
                    )
                    await db.commit()

                    if auto_delete_local:
                        if str_path in self._dirty_items:
                            # I-34: the file changed while it was being processed —
                            # keep it so the deferred re-ingest uploads the newest bytes.
                            logger.warning(f"File changed during sync; keeping local copy for re-ingest: {file_path}")
                        else:
                            try:
                                file_path.unlink(missing_ok=True)
                                logger.info(f"Auto-deleted local file after sync: {file_path}")
                            except Exception as e:
                                logger.warning(f"Could not delete local file {file_path}: {e}")
                    return

                # Record event as 'syncing'
                await db.execute(
                    """
                    INSERT INTO watch_folder_events (
                        id, folder_id, file_path, file_size_bytes, status
                    ) VALUES (?, ?, ?, ?, 'syncing')
                    """,
                    (event_id, folder_id, str_path, total_size)
                )
                await db.commit()

            # 3. Metadata Extraction & Thumbnail Generation
            file_id = f"v_{uuid.uuid4().hex}"
            mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            category = detect_file_category(mime_type, file_path.name)

            # Targeted Client Encryption: encrypt photos, docs, audio, archives; keep video unencrypted for native streaming
            # I-43: If encryption is enabled in settings but vault is locked, refuse ingestion to avoid plaintext leak
            vault_key = vault_manager.get_key()
            encryption_required = (category != "video") and settings.ENCRYPTION_ENABLED
            if encryption_required and vault_key is None:
                logger.warning(
                    f"Vault is locked: deferring ingestion for encrypted category '{category}' file '{file_path.name}' "
                    f"to prevent plaintext upload. Unlock the vault to ingest."
                )
                async with get_db() as db:
                    await db.execute(
                        "UPDATE watch_folder_events SET status = 'vault_locked_deferred', error_message = 'Vault is locked' WHERE id = ?",
                        (event_id,)
                    )
                    await db.commit()
                return

            if (category != "video") and vault_key is not None:
                file_nonce = generate_file_nonce()
                encryption_mode = "ctr"
            else:
                file_nonce = None
                encryption_mode = "none"

            thumbnail_path = settings.THUMBNAIL_DIR / f"{file_id}.webp"
            has_thumb = False
            if category == "image":
                has_thumb = await asyncio.to_thread(generate_webp_thumbnail, file_path, thumbnail_path)
            elif category == "video":
                has_thumb = await generate_video_thumbnail(file_path, thumbnail_path)

            exif: Dict[str, Any] = {}
            if category == "image":
                exif = await asyncio.to_thread(extract_image_exif, file_path)
            elif category == "video":
                exif = await extract_video_metadata(file_path)

            # 4. Zero-copy pointer slicing (Hardening 2: avoid duplicating multi-gigabyte files to disk)
            parts = await asyncio.to_thread(
                split_file_if_needed,
                file_path,
                max_part_bytes=settings.MAX_PART_BYTES,
                output_dir=None,
                precomputed_sha=sha256 if total_size <= settings.MAX_PART_BYTES else None
            )

            # 5. Upload each part via storage_client (respects 3-worker concurrency & FloodWait)
            uploaded_parts = []
            for part in parts:
                if encryption_mode == "ctr" and vault_key:
                    enc_slice = EncryptedFileSlice(
                        source=part.get("slice") or part["file_path"],
                        key=vault_key,
                        nonce_hex=file_nonce,
                        byte_offset=part["byte_offset"],
                        slice_size=part["part_size_bytes"]
                    )
                    part_sha = compute_slice_sha256(enc_slice)
                    upload_target = enc_slice
                    part_record = {**part, "part_sha256": part_sha}
                else:
                    part_sha = part["part_sha256"]
                    upload_target = part.get("slice") or part["file_path"]
                    part_record = part

                caption = format_part_caption(
                    file_id=file_id,
                    part_index=part["part_index"],
                    total_parts=len(parts),
                    byte_offset=part["byte_offset"],
                    part_size=part["part_size_bytes"],
                    part_sha256=part_sha,
                    filename=file_path.name,
                    category=category,
                    nonce=file_nonce,
                    encryption=encryption_mode,
                    kind="original"
                )
                res = await storage_client.upload_part(upload_target, caption=caption)
                if isinstance(res, dict):
                    uploaded_parts.append({
                        **part_record,
                        "telegram_message_id": res["message_id"],
                        "telegram_channel_id": res.get("channel_id", str(settings.TELEGRAM_CHANNEL_ID)),
                        "telegram_file_id": res.get("file_id"),
                        "telegram_access_hash": res.get("access_hash")
                    })
                else:
                    uploaded_parts.append({
                        **part_record,
                        "telegram_message_id": res,
                        "telegram_channel_id": str(settings.TELEGRAM_CHANNEL_ID),
                        "telegram_file_id": None,
                        "telegram_access_hash": None
                    })

            # Extract video metadata if available
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
                v_container = exif.get("container") or file_path.suffix.lstrip(".").lower()
                v_duration = exif.get("duration_seconds")

            # I-13: persist capture date + GPS coordinates extracted from image EXIF
            taken_at = exif.get("taken_at") if isinstance(exif, dict) else None
            latitude = exif.get("latitude") if isinstance(exif, dict) else None
            longitude = exif.get("longitude") if isinstance(exif, dict) else None

            # 6. Atomic SQLite Commit
            async with get_db() as db:
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
                        file_id, file_path.name, mime_type, category, total_size, sha256,
                        1 if len(parts) > 1 else 0, len(parts), "completed", file_nonce, encryption_mode,
                        1 if has_thumb else 0,
                        str(thumbnail_path) if has_thumb else None,
                        json.dumps(exif) if exif else None,
                        v_width, v_height, v_bitrate, v_codec, v_container, v_duration,
                        taken_at, latitude, longitude
                    )
                )

                for p in uploaded_parts:
                    part_id = f"vp_{uuid.uuid4().hex}"
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

                # Link to target album if specified
                if target_album_id:
                    await db.execute(
                        "INSERT OR IGNORE INTO file_albums (file_id, album_id) VALUES (?, ?)",
                        (file_id, target_album_id)
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

                # Update watch folder event and folder record
                await db.execute(
                    "UPDATE watch_folder_events SET status = 'synced', file_id = ? WHERE id = ?",
                    (file_id, event_id)
                )
                await db.execute(
                    "UPDATE watch_folders SET last_synced_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (folder_id,)
                )
                await db.commit()

            # 7. Proxy enqueue and Auto-delete local source file if requested
            if category == "video":
                if auto_delete_local:
                    proxy_source_dir = settings.DATA_DIR / "proxies_tmp"
                    proxy_source_dir.mkdir(parents=True, exist_ok=True)
                    proxy_source_path = proxy_source_dir / f"source_{file_id}.tmp"
                    try:
                        try:
                            os.link(file_path, proxy_source_path)
                        except Exception:
                            shutil.copy2(file_path, proxy_source_path)
                        asyncio.create_task(transcoding_manager.enqueue_proxy(file_id, proxy_source_path))
                    except Exception as e:
                        logger.warning(f"Could not stage proxy source for {file_id}: {e}")
                else:
                    asyncio.create_task(transcoding_manager.enqueue_proxy(file_id, file_path))

            if auto_delete_local:
                if str_path in self._dirty_items:
                    # I-34: never delete a file that changed while it was syncing —
                    # the deferred re-ingest must still find the newer bytes on disk.
                    logger.warning(f"File changed during sync; keeping local copy for re-ingest: {file_path}")
                elif not is_safe_file_path(file_path):
                    logger.error(f"Refusing to auto-delete unsafe file path: {file_path}")
                else:
                    try:
                        file_path.unlink(missing_ok=True)
                        logger.info(f"Auto-deleted local file after sync: {file_path}")
                    except Exception as e:
                        logger.warning(f"Could not delete local file {file_path}: {e}")

            # 8. Asynchronously update master catalog in Telegram channel WAL
            asyncio.create_task(self._sync_catalog())
            logger.info(f"WatchFolder successfully ingested {file_path.name} as {file_id}")

        except Exception as e:
            logger.error(f"Failed to process watch file {file_path}: {e}", exc_info=True)
            async with get_db() as db:
                await db.execute(
                    "UPDATE watch_folder_events SET status = 'failed', error_message = ? WHERE id = ?",
                    (str(e), event_id)
                )
                await db.commit()
        finally:
            # I-6: split temp dir is always removed — success or failure (no GB leaks on retry)
            if split_temp_dir is not None:
                try:
                    shutil.rmtree(split_temp_dir, ignore_errors=True)
                except Exception:
                    pass
            self._syncing_paths.discard(str_path)

    async def _sync_catalog(self):
        """Compacts SQLite index and pins master catalog index.json to Telegram channel."""
        try:
            from ..api.routes import sync_master_catalog
            await sync_master_catalog(delay_seconds=5.0)
        except Exception as e:
            logger.warning(f"Watch folder could not trigger master catalog sync: {e}")


# Global singleton instance
workstation_watcher = LocalFolderWatcher()
