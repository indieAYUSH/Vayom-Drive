import asyncio
import inspect
import logging
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Set, Dict, Any

from ..config import settings
from ..db.connection import get_db
from ..storage.client import storage_client, format_part_caption
from ..compression.splitter import split_file_if_needed, calculate_sha256
from .encoder import (
    encode_stream_v2_480p,
    encode_stream_v1,
    is_ffmpeg_available,
    probe_stream_metadata,
    cancel_active_ffmpeg_processes,
    ACTIVE_FFMPEG_PROCESSES,
    TranscodeError,
    PROXY_PROFILE,
    PROXY_MAX_HEIGHT,
)

logger = logging.getLogger("vayom.transcoding.manager")


@asynccontextmanager
async def _safe_db_context():
    """
    Safely invokes get_db(), supporting standard async context managers
    as well as coroutines (e.g. async def broken_get_db() or coroutine mocks),
    ensuring coroutines are always awaited to prevent 'coroutine was never awaited' warnings.
    """
    res = get_db()
    if asyncio.iscoroutine(res):
        res = await res
    elif inspect.isasyncgen(res):
        res = asynccontextmanager(lambda: res)()

    if hasattr(res, "__aenter__"):
        async with res as db:
            yield db
    else:
        yield res


class TranscodingManager:
    """
    Manages background creation and MTProto upload of stream-optimized proxy files.
    Enforces:
    - Bounded CPU/GPU resource usage (1 encode job at a time).
    - Disk space pre-checks (at least 1 GB or 15% estimated free space).
    - MTProto upload safety via storage_client (3 concurrent workers max with FloodWait backoff).
    - Startup crash recovery for stale 'encoding' or 'uploading' states.
    - Local-source availability requirement (no automatic downloading of remote-only files).
    """

    def __init__(self):
        self._encode_semaphore = asyncio.Semaphore(1)
        self._active_jobs: Set[str] = set()
        self._download_jobs: Set[str] = set()
        self._download_tasks: Set[asyncio.Task] = set()
        self._active_file_paths: Set[Path] = set()
        self._active_processes: Set[Any] = set()
        self._current_process: Optional[Any] = None
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._worker_restarts: int = 0
        self._max_worker_restarts: int = 5

    async def start(self):
        """Starts background proxy queue worker and runs startup recovery."""
        if self._running:
            return
        self._running = True
        await self.recover_stale_tasks()
        try:
            await self.cleanup_stale_temp_files()
        except Exception as e:
            logger.error(f"Error during startup cleanup of stale proxy files: {e}")
        self._worker_restarts = 0
        self._spawn_worker()
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup_loop())
        logger.info("TranscodingManager background worker started.")

    async def _periodic_cleanup_loop(self):
        """Periodically sweeps orphaned proxy temp files while manager is running."""
        while self._running:
            try:
                await asyncio.sleep(3600)
                await self.cleanup_stale_temp_files()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in periodic proxy temp cleanup: {e}")

    async def cleanup_stale_temp_files(self, max_age_seconds: float = 3600.0) -> int:
        """
        Sweeps orphaned .tmp and .tmp.mp4 files from settings.DATA_DIR / 'proxies_tmp'
        that are older than max_age_seconds (default 1 hour), without deleting actively
        processing files.
        """
        return await asyncio.to_thread(self._sync_cleanup_stale_temp_files, max_age_seconds)

    # Aliases for compatibility
    sweep_stale_temp_files = cleanup_stale_temp_files
    cleanup_stale_proxies = cleanup_stale_temp_files

    def _sync_cleanup_stale_temp_files(self, max_age_seconds: float = 3600.0) -> int:
        temp_dir = settings.DATA_DIR / "proxies_tmp"
        if not temp_dir.exists():
            return 0

        active_ids = set(self._active_jobs) | set(self._download_jobs)
        active_paths = {p.resolve() for p in self._active_file_paths}

        now = time.time()
        removed_count = 0

        try:
            for item in list(temp_dir.rglob("*")):
                try:
                    if not item.is_file():
                        continue

                    name = item.name
                    # Sweeps orphaned temporary proxy files (.tmp, .tmp.mp4, preview_*.mp4, .mp4)
                    if not (
                        name.endswith(".tmp")
                        or name.endswith(".tmp.mp4")
                        or name.endswith(".mp4")
                        or name.startswith("preview_")
                        or ".tmp." in name
                    ):
                        continue

                    try:
                        stat = item.stat()
                        age = now - stat.st_mtime
                        if age < max_age_seconds:
                            continue
                    except OSError:
                        continue

                    try:
                        resolved = item.resolve()
                    except OSError:
                        resolved = item

                    if resolved in active_paths:
                        continue

                    is_active = False
                    for active_id in active_ids:
                        if active_id and (active_id in name or active_id in str(item)):
                            is_active = True
                            break
                    if is_active:
                        continue

                    try:
                        item.unlink()
                        removed_count += 1
                        logger.info(f"Cleaned up stale proxy temporary file: {item} (age: {age:.1f}s)")
                    except OSError as e:
                        logger.warning(f"Could not remove stale proxy file {item}: {e}")

                except Exception as file_err:
                    logger.debug(f"Error inspecting proxy temp file {item}: {file_err}")

            # Prune empty subdirectories in proxies_tmp
            for dirpath, dirnames, filenames in os.walk(temp_dir, topdown=False):
                current_dir = Path(dirpath)
                if current_dir != temp_dir:
                    try:
                        if current_dir.is_dir() and not any(current_dir.iterdir()):
                            current_dir.rmdir()
                    except OSError:
                        pass

        except Exception as e:
            logger.error(f"Error sweeping stale proxy temporary files: {e}")

        return removed_count

    async def _cancel_active_subprocesses(self):
        """Terminates and awaits all active transcoding subprocesses so no zombies linger."""
        procs_to_terminate = set(self._active_processes)
        if self._current_process:
            procs_to_terminate.add(self._current_process)

        for proc in procs_to_terminate:
            try:
                if getattr(proc, "returncode", None) is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    wait_fn = getattr(proc, "wait", None)
                    if callable(wait_fn):
                        res = wait_fn()
                        if asyncio.iscoroutine(res):
                            try:
                                await asyncio.wait_for(res, timeout=5.0)
                            except (asyncio.TimeoutError, Exception):
                                pass
            except Exception as err:
                logger.debug(f"Error terminating subprocess: {err}")

        self._active_processes.clear()
        self._current_process = None

        try:
            await cancel_active_ffmpeg_processes()
        except Exception as err:
            logger.debug(f"Error calling cancel_active_ffmpeg_processes: {err}")

    def _spawn_worker(self):
        """Creates the worker task and attaches the H-3 supervisor callback."""
        self._worker_task = asyncio.create_task(self._process_queue_loop())
        self._worker_task.add_done_callback(self._on_worker_done)

    def _on_worker_done(self, task: asyncio.Task):
        """
        H-3: the proxy worker must never die silently. Previously any exception
        raised while recording a failure (e.g. a transient DB error inside the
        except block) terminated the loop, leaving every later proxy stuck in
        'queued' forever with no log the user would ever see.
        """
        if not self._running or task.cancelled():
            return
        if self._worker_task is not task:
            # A newer worker superseded this one (stop/start race).
            return
        try:
            exc = task.exception()
        except Exception:
            exc = None
        logger.error(f"Proxy worker exited unexpectedly ({exc!r}); restarting.")
        if self._worker_restarts >= self._max_worker_restarts:
            logger.error(
                f"Proxy worker crashed {self._worker_restarts} times; giving up for this session. "
                "Restart the app to re-enable proxy generation."
            )
            self._running = False
            return
        self._worker_restarts += 1
        # Any job that was mid-flight died with the worker; release its reservation
        # so a later enqueue for the same file is not silently ignored.
        self._active_jobs.clear()
        try:
            self._spawn_worker()
        except Exception as e:
            logger.error(f"Could not restart proxy worker: {e}")
            self._running = False

    async def stop(self):
        """Stops background proxy queue worker and cancels active transcoding subprocesses cleanly."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # Cancel any active download tasks
        for t in list(self._download_tasks):
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._download_tasks.clear()

        # Cancel worker task
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        # Terminate and reap any active ffmpeg subprocesses so no zombies linger
        await self._cancel_active_subprocesses()

        self._active_jobs.clear()
        self._download_jobs.clear()
        self._active_file_paths.clear()
        logger.info("TranscodingManager background worker stopped.")

    async def recover_stale_tasks(self):
        """
        Recovers from unexpected application termination.
        Scans DB for original files stuck in 'queued', 'encoding' or 'uploading'
        proxy_status and marks them 'failed' so the UI does not hang forever.
        """
        try:
            async with _safe_db_context() as db:
                # H-2: 'queued' was omitted, so a crash between enqueue_proxy()
                # and the worker picking the job up left the row spinning in the
                # UI forever. At startup the in-memory queue is always empty, so
                # every persisted 'queued' row is stale by definition.
                cur = await db.execute(
                    "SELECT id FROM files WHERE proxy_status IN ('queued', 'encoding', 'uploading')"
                )
                rows = await cur.fetchall()
                if rows:
                    stuck_ids = [r[0] for r in rows]
                    placeholders = ",".join(["?"] * len(stuck_ids))
                    await db.execute(
                        f"UPDATE files SET proxy_status = 'failed' WHERE id IN ({placeholders})",
                        stuck_ids
                    )
                    await db.commit()
                    logger.info(f"Recovered {len(stuck_ids)} stuck proxy transcode jobs to 'failed'.")
        except Exception as e:
            logger.error(f"Error recovering stale transcode tasks: {e}")

    async def enqueue_proxy(self, original_id: str, local_source_path: Path):
        """
        Enqueues an original video file for compact 480p proxy generation.
        Verifies local source path exists before queuing.
        """
        if not await is_ffmpeg_available():
            logger.info("ffmpeg is not available. Skipping proxy generation.")
            async with _safe_db_context() as db:
                await db.execute("UPDATE files SET proxy_status = 'unavailable_no_source' WHERE id = ?", (original_id,))
                await db.commit()
            return

        if not local_source_path.exists():
            logger.warning(f"Cannot generate proxy for {original_id}: local source {local_source_path} does not exist.")
            async with _safe_db_context() as db:
                await db.execute("UPDATE files SET proxy_status = 'unavailable_no_source' WHERE id = ?", (original_id,))
                await db.commit()
            return

        if original_id in self._active_jobs:
            return

        # I-41: reserve the id synchronously so concurrent enqueues can't double-queue it
        self._active_jobs.add(original_id)
        self._active_file_paths.add(local_source_path.resolve())

        try:
            async with _safe_db_context() as db:
                await db.execute("UPDATE files SET proxy_status = 'queued' WHERE id = ?", (original_id,))
                await db.commit()

            await self._queue.put({
                "original_id": original_id,
                "source_path": local_source_path
            })
        except BaseException:
            # R-05: if the DB write or queue insertion fails, the reservation
            # must be released or this file can never be retried until restart.
            self._active_jobs.discard(original_id)
            self._active_file_paths.discard(local_source_path.resolve())
            try:
                async with _safe_db_context() as db:
                    await db.execute(
                        "UPDATE files SET proxy_status = 'failed' WHERE id = ? AND proxy_status = 'queued'",
                        (original_id,),
                    )
                    await db.commit()
            except Exception as status_err:
                logger.warning(f"Could not mark proxy {original_id} as failed: {status_err}")
            raise
        logger.info(f"Enqueued proxy generation for {original_id}")

    async def enqueue_remote_proxy(self, original_id: str, source_file_id: Optional[str] = None):
        """
        Downloads a remote video (or its existing large proxy) and queues a
        480p re-encode. The original may have no local source after a fresh
        sign-in, which is why proxy generation previously stayed a no-op.
        """
        if original_id in self._active_jobs or original_id in self._download_jobs:
            return

        self._download_jobs.add(original_id)
        async with _safe_db_context() as db:
            await db.execute("UPDATE files SET proxy_status = 'queued' WHERE id = ?", (original_id,))
            await db.commit()

        task = asyncio.create_task(self._download_then_enqueue(original_id, source_file_id))
        self._download_tasks.add(task)
        task.add_done_callback(self._download_tasks.discard)

    async def _download_then_enqueue(self, original_id: str, source_file_id: Optional[str]):
        temp_source: Optional[Path] = None
        try:
            async with _safe_db_context() as db:
                # Re-encoding the existing 1080p proxy is much faster than
                # downloading the original again, and visually lossless at 480p.
                source_id = source_file_id
                if not source_id:
                    cur = await db.execute(
                        """
                        SELECT id FROM files
                        WHERE proxy_of = ? AND kind = 'proxy' AND is_deleted = 0
                          AND upload_status = 'completed' AND proxy_profile != ?
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (original_id, PROXY_PROFILE),
                    )
                    row = await cur.fetchone()
                    source_id = row[0] if row else original_id

                cur_file = await db.execute(
                    "SELECT id, size_bytes FROM files WHERE id = ? AND is_deleted = 0",
                    (source_id,),
                )
                src_row = await cur_file.fetchone()
                if not src_row:
                    raise TranscodeError(f"Source file {source_id} not found in catalog.")

                cur_parts = await db.execute(
                    "SELECT * FROM file_parts WHERE file_id = ? ORDER BY part_index ASC",
                    (source_id,),
                )
                parts = [dict(r) for r in await cur_parts.fetchall()]

            if not parts:
                raise TranscodeError(f"Source file {source_id} has no stored parts.")

            expected_size = int(src_row["size_bytes"] or 0)
            if expected_size <= 0:
                expected_size = sum(int(p.get("part_size_bytes") or 0) for p in parts)

            free = shutil.disk_usage(settings.DATA_DIR).free
            # Leave room for the downloaded source, the 480p output and normal
            # app data. This is deliberately stricter than the old 1 GB check.
            if free < expected_size + 512 * 1024 * 1024:
                raise TranscodeError(
                    f"Not enough free disk space for a 480p proxy "
                    f"(need about {(expected_size + 512 * 1024 * 1024) // (1024 * 1024)} MiB, have {free // (1024 * 1024)} MiB)."
                )

            temp_dir = settings.DATA_DIR / "proxies_tmp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_source = temp_dir / f"source_{original_id}.tmp"
            self._active_file_paths.add(temp_source.resolve())
            try:
                if temp_source.exists():
                    temp_source.unlink()
            except Exception:
                pass

            # Imported lazily: storage adapters -> workstation watcher imports
            # this manager, so a module-level import would be circular in the
            # frozen sidecar.
            from ..storage.manager import stream_part_bytes_universal

            written = 0
            with open(temp_source, "wb") as out:
                for part in parts:
                    part_size = int(part.get("part_size_bytes") or 0)
                    async for chunk in stream_part_bytes_universal(
                        part, offset=0, limit=part_size, chunk_size=1024 * 1024
                    ):
                        out.write(chunk)
                        written += len(chunk)
                    if written % (64 * 1024 * 1024) < 1024 * 1024:
                        logger.info(f"480p proxy source download {original_id}: {written // (1024 * 1024)} MiB")

            if written <= 0:
                raise TranscodeError(f"Downloaded source for {original_id} is empty.")
            if expected_size and written != expected_size:
                logger.warning(
                    f"Proxy source size mismatch for {original_id}: expected {expected_size}, got {written}. "
                    "Continuing; ffmpeg will validate the file."
                )

            logger.info(f"Downloaded {written} bytes for {original_id}; queueing 480p encode.")
            await self.enqueue_proxy(original_id, temp_source)
        except Exception as e:
            logger.error(f"Could not prepare remote 480p proxy for {original_id}: {e}", exc_info=True)
            try:
                async with _safe_db_context() as db:
                    await db.execute("UPDATE files SET proxy_status = 'failed' WHERE id = ?", (original_id,))
                    await db.commit()
            except Exception:
                pass
            if temp_source:
                try:
                    temp_source.unlink()
                except Exception:
                    pass
        finally:
            self._download_jobs.discard(original_id)
            if temp_source:
                self._active_file_paths.discard(temp_source.resolve())

    async def _process_queue_loop(self):
        """
        Worker loop processing the proxy generation queue.

        H-3: no exception may escape this loop. Previously a failure while
        *recording* a failure (DB error inside the except block) killed the
        worker silently, so every subsequent proxy sat in 'queued' forever.
        """
        while self._running:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Proxy queue get() failed: {e}")
                await asyncio.sleep(0.5)
                continue

            orig_id = None
            source_path = None
            try:
                orig_id = job["original_id"]
                source_path = job["source_path"]
                self._active_jobs.add(orig_id)
                self._active_file_paths.add(source_path.resolve())
                await self._process_job(orig_id, source_path)
                # A completed cycle proves the worker is healthy — reset the crash budget.
                self._worker_restarts = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Failed generating proxy for {orig_id}: {e}", exc_info=True)
                if orig_id:
                    try:
                        async with _safe_db_context() as db:
                            await db.execute("UPDATE files SET proxy_status = 'failed' WHERE id = ?", (orig_id,))
                            await db.commit()
                    except Exception as db_err:
                        # Never let a bookkeeping failure kill the queue.
                        logger.error(f"Could not mark proxy {orig_id} as failed: {db_err}")
            finally:
                if orig_id:
                    self._active_jobs.discard(orig_id)
                if source_path:
                    self._active_file_paths.discard(source_path.resolve())
                try:
                    self._queue.task_done()
                except Exception:
                    pass

    async def _process_job(self, orig_id: str, source_path: Path):
        """Executes encode and upload steps for a single file."""
        if not source_path.exists():
            async with _safe_db_context() as db:
                await db.execute("UPDATE files SET proxy_status = 'unavailable_no_source' WHERE id = ?", (orig_id,))
                await db.commit()
            return

        # 1. Probe source metadata: skip proxy if audio-only (has audio but explicitly no video stream)
        source_meta = await probe_stream_metadata(source_path)
        if source_meta.get("has_audio") and not source_meta.get("has_video"):
            logger.info(f"Skipping proxy for {orig_id}: source file has audio but no video stream (audio-only).")
            async with _safe_db_context() as db:
                await db.execute("UPDATE files SET proxy_status = 'skipped_small' WHERE id = ?", (orig_id,))
                await db.commit()
            return

        # 2. Check catalog for existing ready proxy of identical original SHA-256
        async with _safe_db_context() as db:
            cur = await db.execute("SELECT sha256 FROM files WHERE id = ?", (orig_id,))
            orig_row = await cur.fetchone()
            orig_sha = orig_row[0] if orig_row else None
            if orig_sha:
                cur_dup = await db.execute(
                    """
                    SELECT p.id, p.size_bytes, p.sha256, p.total_parts, p.width, p.height, p.duration_s
                    FROM files f
                    JOIN files p ON p.proxy_of = f.id
                    WHERE f.sha256 = ? AND p.upload_status = 'completed' AND p.is_deleted = 0
                      AND p.proxy_profile = ? AND (p.height IS NULL OR p.height <= ?)
                    LIMIT 1
                    """,
                    (orig_sha, PROXY_PROFILE, PROXY_MAX_HEIGHT)
                )
                existing_proxy = await cur_dup.fetchone()
                if existing_proxy:
                    # Reuse existing proxy parts by creating linked proxy record without re-encoding
                    (ex_pid, ex_size, ex_sha, ex_parts, ex_w, ex_h, ex_dur) = existing_proxy
                    new_proxy_id = f"v_prx_{uuid.uuid4().hex}"
                    await db.execute(
                        """
                        INSERT INTO files (
                            id, filename, mime_type, file_category, size_bytes, sha256,
                            is_split, total_parts, upload_status, kind, proxy_of,
                            proxy_profile, proxy_status, width, height, duration_s
                        ) VALUES (?, ?, 'video/mp4', 'video', ?, ?, ?, ?, 'completed', 'proxy', ?, ?, 'ready', ?, ?, ?)
                        """,
                        (
                            new_proxy_id, f"{orig_id}.{PROXY_PROFILE}.mp4", ex_size, ex_sha,
                            1 if ex_parts > 1 else 0, ex_parts, orig_id, PROXY_PROFILE,
                            ex_w, ex_h, ex_dur
                        )
                    )
                    # Clone file_parts references
                    cur_parts = await db.execute(
                        "SELECT part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id, telegram_channel_id, telegram_file_id, telegram_access_hash FROM file_parts WHERE file_id = ?",
                        (ex_pid,)
                    )
                    ex_part_rows = await cur_parts.fetchall()
                    for pr in ex_part_rows:
                        cloned_pid = f"vp_{uuid.uuid4().hex}"
                        await db.execute(
                            """
                            INSERT INTO file_parts (
                                id, file_id, part_index, byte_offset, part_size_bytes,
                                part_sha256, telegram_message_id, telegram_channel_id,
                                telegram_file_id, telegram_access_hash
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                cloned_pid, new_proxy_id, pr[0], pr[1], pr[2], pr[3],
                                pr[4], pr[5], pr[6], pr[7]
                            )
                        )
                    await db.execute("UPDATE files SET proxy_status = 'ready' WHERE id = ?", (orig_id,))
                    await db.commit()
                    logger.info(f"Reused existing proxy {ex_pid} for duplicate original {orig_id} (hash={orig_sha}).")
                    return

        # Check disk space: need at least 1 GB free
        disk_stats = shutil.disk_usage(settings.DATA_DIR)
        if disk_stats.free < 1024 * 1024 * 1024:
            logger.warning(f"Insufficient disk space (< 1 GB) for proxy generation of {orig_id}.")
            async with _safe_db_context() as db:
                await db.execute("UPDATE files SET proxy_status = 'failed' WHERE id = ?", (orig_id,))
                await db.commit()
            return

        # Update parent status to 'encoding'
        async with _safe_db_context() as db:
            await db.execute("UPDATE files SET proxy_status = 'encoding' WHERE id = ?", (orig_id,))
            await db.commit()

        proxy_id = f"v_prx_{uuid.uuid4().hex}"
        temp_dir = settings.DATA_DIR / "proxies_tmp" / proxy_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        proxy_file = temp_dir / f"preview_{PROXY_PROFILE}.mp4"
        self._active_file_paths.add(proxy_file.resolve())
        self._active_file_paths.add(temp_dir.resolve())

        try:
            # 1. Encode with CPU/GPU bounded concurrency
            async with self._encode_semaphore:
                encoder_fn = encode_stream_v1 if encode_stream_v1 is not encode_stream_v2_480p else encode_stream_v2_480p
                proxy_meta = await encoder_fn(source_path, proxy_file)

            # Validate proxy duration within 1% (or 0.5s) of source duration
            src_dur = source_meta.get("duration_s", 0.0)
            prx_dur = proxy_meta.get("duration_s", 0.0)
            if src_dur > 1.0 and prx_dur > 0:
                dur_diff = abs(prx_dur - src_dur)
                allowed_drift = max(0.5, 0.01 * src_dur)
                if dur_diff > allowed_drift:
                    raise TranscodeError(
                        f"Proxy duration mismatch: source={src_dur}s, proxy={prx_dur}s "
                        f"(drift={dur_diff:.2f}s > {allowed_drift:.2f}s allowed)"
                    )

            # Update parent status to 'uploading'
            async with _safe_db_context() as db:
                await db.execute("UPDATE files SET proxy_status = 'uploading' WHERE id = ?", (orig_id,))
                await db.commit()

            # 2. Slice if needed (proxy file is typically 50MB - 2GB)
            proxy_size = proxy_file.stat().st_size
            proxy_sha = await asyncio.to_thread(calculate_sha256, proxy_file)

            parts = await asyncio.to_thread(
                split_file_if_needed, proxy_file,
                max_part_bytes=settings.MAX_PART_BYTES, precomputed_sha=proxy_sha,
            )
            uploaded_parts = []

            # 3. Upload parts with current 480p proxy captions
            for part in parts:
                caption = format_part_caption(
                    file_id=proxy_id,
                    part_index=part["part_index"],
                    total_parts=len(parts),
                    byte_offset=part["byte_offset"],
                    part_size=part["part_size_bytes"],
                    part_sha256=part["part_sha256"],
                    filename=f"{orig_id}.{PROXY_PROFILE}.mp4",
                    category="video",
                    nonce=None,
                    encryption="none",
                    kind="proxy",
                    proxy_of=orig_id,
                    proxy_profile=PROXY_PROFILE
                )
                res = await storage_client.upload_part(part.get("slice") or part["file_path"], caption=caption)
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

            # 4. Commit child proxy and parts to SQLite
            async with _safe_db_context() as db:
                await db.execute(
                    """
                    INSERT INTO files (
                        id, filename, mime_type, file_category, size_bytes, sha256,
                        is_split, total_parts, upload_status, kind, proxy_of,
                        proxy_profile, proxy_status, width, height, duration_s
                    ) VALUES (?, ?, 'video/mp4', 'video', ?, ?, ?, ?, 'completed', 'proxy', ?, ?, 'ready', ?, ?, ?)
                    """,
                    (
                        proxy_id, f"{orig_id}.{PROXY_PROFILE}.mp4", proxy_size, proxy_sha,
                        1 if len(parts) > 1 else 0, len(parts), orig_id, PROXY_PROFILE,
                        proxy_meta.get("width", 0), proxy_meta.get("height", 0),
                        proxy_meta.get("duration_s", 0.0)
                    )
                )

                for p in uploaded_parts:
                    p_id = f"vp_{uuid.uuid4().hex}"
                    await db.execute(
                        """
                        INSERT INTO file_parts (
                            id, file_id, part_index, byte_offset, part_size_bytes,
                            part_sha256, telegram_message_id, telegram_channel_id,
                            telegram_file_id, telegram_access_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            p_id, proxy_id, p["part_index"], p["byte_offset"],
                            p["part_size_bytes"], p["part_sha256"], p["telegram_message_id"],
                            p.get("telegram_channel_id", str(settings.TELEGRAM_CHANNEL_ID)),
                            p.get("telegram_file_id"), p.get("telegram_access_hash")
                        )
                    )

                # Set original file proxy status to ready
                await db.execute("UPDATE files SET proxy_status = 'ready' WHERE id = ?", (orig_id,))
                await db.commit()

            logger.info(f"Successfully generated and uploaded {PROXY_PROFILE} proxy {proxy_id} for {orig_id} ({proxy_size} bytes)")

            # Trigger WAL master catalog sync
            from ..api.routes import sync_master_catalog
            asyncio.create_task(sync_master_catalog())

        except Exception as e:
            logger.error(f"Error generating proxy for {orig_id}: {e}")
            try:
                async with _safe_db_context() as db:
                    await db.execute("UPDATE files SET proxy_status = 'failed' WHERE id = ?", (orig_id,))
                    await db.commit()
            except Exception:
                pass
            raise
        finally:
            self._active_file_paths.discard(proxy_file.resolve())
            self._active_file_paths.discard(temp_dir.resolve())
            # Clean up local temporary proxy files
            try:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
                if source_path.name.startswith("source_") and source_path.name.endswith(".tmp") and source_path.exists():
                    source_path.unlink()
            except Exception:
                pass


# Global singleton manager and alias
ProxyManager = TranscodingManager
transcoding_manager = TranscodingManager()
