"""
Regression tests for the watcher ingestion pipeline fixes:
- I-5: bounded asyncio.Queue + fixed worker pool + per-path dedupe (no unbounded task spam)
- I-2: hash/split/PIL offloaded to worker threads; no duplicate full-file hash
- I-6: split temp dirs always cleaned up (success AND failure), no GB leaks on retry
"""
import asyncio
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.config import settings
from backend.db.connection import get_db, init_db
from backend.storage.client import storage_client
from backend.workstation import watcher as watcher_mod
from backend.workstation.watcher import LocalFolderWatcher, INGEST_WORKER_COUNT


@pytest.fixture(autouse=True)
def setup_test_db():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(init_db())
    old_auth_mode = settings.AUTH_MODE
    old_channel = settings.TELEGRAM_CHANNEL_ID
    settings.AUTH_MODE = "bot"
    settings.TELEGRAM_CHANNEL_ID = "-1001234567890"
    yield
    settings.AUTH_MODE = old_auth_mode
    settings.TELEGRAM_CHANNEL_ID = old_channel


async def _insert_folder(folder_id: str, path: str) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO watch_folders (id, path, label, auto_delete_local, is_active) VALUES (?, ?, ?, 0, 1)",
            (folder_id, path, "Pipeline Test")
        )
        await db.commit()


def test_ingest_pool_bounded_and_deduped():
    """I-5: concurrent processing never exceeds the worker pool; duplicate paths are rejected."""
    async def _run():
        watcher = LocalFolderWatcher()
        gate = asyncio.Event()
        processed = []
        active = 0
        peak = 0

        async def fake_process_file(folder_id, file_path, auto_delete_local=False, target_album_id=None):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await gate.wait()
                processed.append(str(file_path))
            finally:
                active -= 1

        watcher.process_file = fake_process_file

        with tempfile.TemporaryDirectory() as tmpdir:
            files = [Path(tmpdir) / f"f{i}.bin" for i in range(12)]
            results = []
            for f in files:
                results.append(await watcher._enqueue("wf_pipeline", f))
                results.append(await watcher._enqueue("wf_pipeline", f))  # duplicate must be rejected

        assert results.count(True) == 12
        assert results.count(False) == 12

        # Hold the gate open: workers pick up items but cannot finish, so concurrency is measurable
        await asyncio.sleep(0.05)
        assert active <= INGEST_WORKER_COUNT

        gate.set()
        await asyncio.wait_for(watcher._ingest_queue.join(), timeout=5.0)
        assert len(processed) == 12
        assert peak <= INGEST_WORKER_COUNT

        await watcher.stop()
        assert watcher._ingest_workers == []
        assert watcher._ingest_queue is None

    asyncio.run(_run())


def test_scan_folder_returns_without_blocking_processing():
    """I-5: scan_folder hands the batch to one bounded producer and returns the count immediately."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            folder_id = f"wf_{uuid.uuid4().hex[:12]}"
            await _insert_folder(folder_id, tmpdir)
            for i in range(3):
                Path(tmpdir, f"scan_{i}.txt").write_text(f"scan {i}")

            watcher = LocalFolderWatcher()
            seen = []

            async def fake_process_file(folder_id, file_path, auto_delete_local=False, target_album_id=None):
                seen.append(Path(file_path).name)

            watcher.process_file = fake_process_file

            count = await asyncio.wait_for(watcher.scan_folder(folder_id), timeout=2.0)
            assert count == 3

            for _ in range(200):  # producer task enqueues asynchronously; allow up to 2s
                if len(seen) == 3:
                    break
                await asyncio.sleep(0.01)
            assert sorted(seen) == ["scan_0.txt", "scan_1.txt", "scan_2.txt"]

            await watcher.stop()

    asyncio.run(_run())


def test_split_temp_dir_cleaned_on_failure_and_success():
    """I-6: split temp dir is removed on upload failure and on success; failure records 'failed'."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            folder_id = f"wf_{uuid.uuid4().hex[:12]}"
            await _insert_folder(folder_id, tmpdir)

            uploads_dir = Path(tmpdir) / "uploads"
            uploads_dir.mkdir()
            filename = f"big_{uuid.uuid4().hex[:8]}.bin"
            src = Path(tmpdir) / filename
            src.write_bytes(b"A" * 100)  # 4 parts at 32 bytes each

            watcher = LocalFolderWatcher()

            with patch.object(settings, "MAX_PART_BYTES", 32), \
                 patch.object(settings, "UPLOAD_TEMP_DIR", uploads_dir), \
                 patch.object(storage_client, "upload_part", new_callable=AsyncMock) as mock_upload, \
                 patch.object(watcher, "_sync_catalog", new_callable=AsyncMock), \
                 patch("asyncio.sleep", new_callable=AsyncMock):

                # 1. Failure path: upload blows up mid-flight
                mock_upload.side_effect = RuntimeError("telegram boom")
                await watcher.process_file(folder_id=folder_id, file_path=src, auto_delete_local=False)

                assert list(uploads_dir.glob("workstation_*")) == [], "split temp dir leaked on failure"
                async with get_db() as db:
                    cur = await db.execute(
                        "SELECT status FROM watch_folder_events WHERE folder_id = ?", (folder_id,)
                    )
                    assert (await cur.fetchone())[0] == "failed"
                    cur = await db.execute("SELECT COUNT(*) FROM files WHERE filename = ?", (filename,))
                    assert (await cur.fetchone())[0] == 0

                # 2. Retry succeeds: temp dir still removed, file + all parts committed
                mock_upload.reset_mock()
                mock_upload.side_effect = None
                mock_upload.return_value = {
                    "message_id": 424242,
                    "channel_id": "-1001234567890",
                    "file_id": 777,
                    "access_hash": 555
                }
                await watcher.process_file(folder_id=folder_id, file_path=src, auto_delete_local=False)

                assert mock_upload.call_count == 4
                assert list(uploads_dir.glob("workstation_*")) == [], "split temp dir leaked on success"
                async with get_db() as db:
                    cur = await db.execute("SELECT id, total_parts FROM files WHERE filename = ?", (filename,))
                    row = await cur.fetchone()
                    assert row is not None and row[1] == 4
                    cur = await db.execute("SELECT COUNT(*) FROM file_parts WHERE file_id = ?", (row[0],))
                    assert (await cur.fetchone())[0] == 4

    asyncio.run(_run())


def test_hash_and_split_offloaded_to_threads_and_hashed_once():
    """I-2: SHA-256 and splitting run in worker threads; the splitter reuses the precomputed hash."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            folder_id = f"wf_{uuid.uuid4().hex[:12]}"
            await _insert_folder(folder_id, tmpdir)

            src = Path(tmpdir) / "note.txt"
            src.write_bytes(b"hello world " * 100)
            old = time.time() - 10  # already quiet -> no 3s debounce polling
            os.utime(src, (old, old))

            watcher = LocalFolderWatcher()
            main_ident = threading.get_ident()

            real_sha = watcher_mod.calculate_sha256
            real_split = watcher_mod.split_file_if_needed
            sha_threads = []
            split_threads = []
            split_kwargs = []

            def spy_sha(path):
                sha_threads.append(threading.get_ident())
                return real_sha(path)

            def spy_split(*args, **kwargs):
                split_threads.append(threading.get_ident())
                split_kwargs.append(kwargs)
                return real_split(*args, **kwargs)

            sleep_calls = []

            async def spy_sleep(duration, *a, **kw):
                sleep_calls.append(duration)

            with patch.object(storage_client, "upload_part", new_callable=AsyncMock) as mock_upload, \
                 patch.object(watcher, "_sync_catalog", new_callable=AsyncMock), \
                 patch("backend.workstation.watcher.calculate_sha256", side_effect=spy_sha), \
                 patch("backend.workstation.watcher.split_file_if_needed", side_effect=spy_split), \
                 patch("backend.compression.splitter.calculate_sha256", side_effect=spy_sha), \
                 patch("asyncio.sleep", side_effect=spy_sleep):

                mock_upload.return_value = {
                    "message_id": 1, "channel_id": "-1001234567890", "file_id": 2, "access_hash": 3
                }
                await watcher.process_file(folder_id=folder_id, file_path=src, auto_delete_local=False)

            # Hash ran exactly once on the watcher side and never inside the splitter (precomputed)
            assert len(sha_threads) == 1
            assert len(split_threads) == 1
            # Both ran in worker threads, not on the event loop thread
            assert sha_threads[0] != main_ident
            assert split_threads[0] != main_ident
            # Splitter reused the precomputed hash instead of re-reading the file
            assert split_kwargs[0].get("precomputed_sha") is not None
            # Already-quiet file skipped the 3s debounce polling entirely
            assert 3.0 not in sleep_calls

            assert mock_upload.call_count == 1
            async with get_db() as db:
                cur = await db.execute("SELECT COUNT(*) FROM files WHERE filename = ?", ("note.txt",))
                assert (await cur.fetchone())[0] == 1

    asyncio.run(_run())
