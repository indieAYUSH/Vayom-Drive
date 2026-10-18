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
from ..storage.client import storage_client
from ..compression.splitter import split_file_if_needed, calculate_sha256
from ..index.metadata import detect_file_category, generate_webp_thumbnail, generate_video_thumbnail, extract_image_exif, extract_video_metadata
from .security import validate_watch_folder_path, is_safe_file_path

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
        self._iteration_stop_event: Optional[asyncio.Event] = None
        self._syncing_paths: Set[str] = set()
        # I-5: bounded queue + fixed worker pool (created lazily / on start)
        self._ingest_queue: Optional[asyncio.Queue] = None
        self._ingest_workers: List[asyncio.Task] = []
        self._queued_paths: Set[str] = set()
        self._producer_tasks: Set[asyncio.Task] = set()

    async def start(self):
        """Starts the background directory monitoring worker."""
        if self._running:
            return
        self._running = True
        self._ensure_ingest_pool()
        self._worker_task = asyncio.create_task(self._watch_loop(), name="vayom_workstation_watcher")
        logger.info("LocalFolderWatcher service started.")

    async def stop(self):
        """Stops the watcher worker cleanly."""
        self._running = False
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
        logger.info("LocalFolderWatcher service stopped.")

    async def reload(self):
        """Signals the watcher to reload active folders from the database."""
        if self._iteration_stop_event:
            self._iteration_stop_event.set()
        self._reload_event.set()

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
        if key in self._queued_paths or key in self._syncing_paths:
            return False
        self._queued_paths.add(key)
        item = {
            "folder_id": folder_id,
            "file_path": file_path,
            "auto_delete_local": auto_delete_local,
            "target_album_id": target_album_id
        }
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
            self._reload_event.clear()
            folders = await self._get_active_folders()

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
            self._iteration_stop_event = asyncio.Event()

            logger.info(f"Watching {len(active_paths)} directory(ies) for media ingestion: {active_paths}")

            try:
                async for changes in watchfiles.awatch(
                    *active_paths,
                    stop_event=self._iteration_stop_event,
                    recursive=True,
                    debounce=1600
                ):
                    for change_type, path_str in changes:
                        if change_type in (Change.added, Change.modified):
                            file_p = Path(path_str)
                            if not is_safe_file_path(file_p):
                                logger.warning(f"Skipping unsafe file change event: {file_p}")
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

        pending: List[Path] = []
        for item in folder_path.rglob("*"):
            if item.is_file() and not self._should_ignore(item):
                if not is_safe_file_path(item):
                    logger.warning(f"Skipping unsafe file during scan: {item}")
                    continue
                pending.append(item)

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

    def _should_ignore(self, path: Path) -> bool:
        """Determines if a file is temporary, hidden, or system generated."""
        name = path.name
        if name.startswith(".") or name.startswith("~"):
            return True
        ignored_extensions = {".tmp", ".crdownload", ".part", ".swp", ".lock"}
        if path.suffix.lower() in ignored_extensions:
            return True
        return False

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
            # 1. Stability check: files already quiet (mtime older than 3 s) are ingested directly;
            # recently-written files are polled until their size is unchanged across a 3 s window.
            if not file_path.is_file():
                return
            try:
                st = file_path.stat()
            except OSError:
                return

            is_stable = False
            prev_size = -1
            if st.st_size > 0 and (time.time() - st.st_mtime) >= 3.0:
                is_stable = True
                prev_size = st.st_size
            else:
                for _ in range(10):  # Maximum 30s stabilization window
                    if not file_path.exists() or not file_path.is_file():
                        return
                    try:
                        curr_size = file_path.stat().st_size
                    except Exception:
                        return

                    if curr_size > 0 and curr_size == prev_size:
                        is_stable = True
                        break

                    prev_size = curr_size
                    await asyncio.sleep(3.0)

            if not is_stable or prev_size <= 0:
                return

            total_size = prev_size

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
            nonce = None
            mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            category = detect_file_category(mime_type, file_path.name)

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

            # 4. Slicing (if > 1.9 GB) into isolated temp directory (offloaded to a thread)
            if total_size > settings.MAX_PART_BYTES:
                split_temp_dir = settings.UPLOAD_TEMP_DIR / f"workstation_{file_id}"
                split_temp_dir.mkdir(parents=True, exist_ok=True)
                parts = await asyncio.to_thread(
                    split_file_if_needed, file_path,
                    max_part_bytes=settings.MAX_PART_BYTES, output_dir=split_temp_dir
                )
            else:
                # Re-use the SHA already computed above — no second full-file read
                parts = await asyncio.to_thread(
                    split_file_if_needed, file_path,
                    max_part_bytes=settings.MAX_PART_BYTES, precomputed_sha=sha256
                )

            # 5. Upload each part via storage_client (respects 3-worker concurrency & FloodWait)
            uploaded_parts = []
            for part in parts:
                caption = (
                    f"VAYOM:v1:{json.dumps({'id': file_id, 'name': file_path.name, 'part': part['part_index'], 'total': len(parts), 'offset': part['byte_offset'], 'size': part['part_size_bytes'], 'sha256': part['part_sha256'], 'cat': category, 'nonce': None, 'enc': 'none'})}"
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

            # 6. Atomic SQLite Commit
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
                        file_id, file_path.name, mime_type, category, total_size, sha256,
                        1 if len(parts) > 1 else 0, len(parts), "completed", None, "none",
                        1 if has_thumb else 0,
                        str(thumbnail_path) if has_thumb else None,
                        json.dumps(exif) if exif else None
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

            # 7. Auto-delete local source file if requested
            if auto_delete_local:
                if not is_safe_file_path(file_path):
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
            await sync_master_catalog()
        except Exception as e:
            logger.warning(f"Watch folder could not trigger master catalog sync: {e}")


# Global singleton instance
workstation_watcher = LocalFolderWatcher()
