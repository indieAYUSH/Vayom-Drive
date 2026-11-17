import os
import json
import asyncio
import shutil
import collections
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import HTTPException

import backend.main
from backend.main import app, _process_is_alive
from backend.compression.splitter import BoundedFileSlice
from backend.crypto.cipher import EncryptedFileSlice
from backend.storage.client import storage_client
from backend.streaming.router import verify_loopback_security
from backend.config import settings
from backend.db.connection import get_db, init_db
from backend.api.routes import perform_rebuild_index


def test_bug_02_procfs_comm_parsing(tmp_path):
    """
    BUG-02: Verifies /proc/{pid}/stat parsing works when comm contains spaces or parens,
    and detects 'Z' state correctly.
    """
    fake_pid = 99999
    stat_file = tmp_path / "stat"
    # Format: pid (comm with spaces and parens) Z ...
    stat_file.write_text("99999 (my process (name)) Z 1 2 3 4 5", encoding="utf-8")

    with patch("builtins.open", MagicMock(return_value=stat_file.open("r", encoding="utf-8"))):
        with patch("os.kill"):
            alive = _process_is_alive(fake_pid)
            assert alive is False, "Zombie process with spaces in comm should be detected as dead"

    # Now with running process 'S'
    stat_file.write_text("99999 (my process (name)) S 1 2 3 4 5", encoding="utf-8")
    with patch("builtins.open", MagicMock(return_value=stat_file.open("r", encoding="utf-8"))):
        with patch("os.kill"):
            alive = _process_is_alive(fake_pid)
            assert alive is True, "Sleeping process should be detected as alive"


def test_bug_03_health_and_shutdown():
    """
    BUG-03: Exposes shutting_down on health and handles shutdown endpoint.
    """
    client = TestClient(app)

    # Initial health check is ok
    backend.main._is_shutting_down = False
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # When shutting down
    backend.main._is_shutting_down = True
    try:
        res = client.get("/api/v1/health")
        assert res.status_code == 200
        assert res.json()["status"] == "shutting_down"

        # Shutdown endpoint from loopback
        with patch("asyncio.create_task", side_effect=lambda coro: coro.close()) as mock_task:
            res_shut = client.post("/api/v1/system/shutdown")
            assert res_shut.status_code == 200
            assert res_shut.json()["status"] == "shutting_down"
            assert mock_task.called
    finally:
        backend.main._is_shutting_down = False


def test_bug_05_file_slice_closure(tmp_path):
    """
    BUG-05: BoundedFileSlice and EncryptedFileSlice close and __del__ release resources.
    """
    test_file = tmp_path / "slice_test.bin"
    test_file.write_bytes(b"A" * 1024)

    bfs = BoundedFileSlice(test_file, 0, 512)
    bfs.open()
    assert bfs._file is not None and not bfs._file.closed
    bfs.close()
    assert bfs._file is None

    # Test EncryptedFileSlice
    key = b"\x00" * 32
    efs = EncryptedFileSlice(test_file, key, "0011223344556677", 0, 512)
    efs.open()
    assert efs._file is not None and not efs._file.closed
    efs.close()
    assert efs._file is None

    # Test with underlying BoundedFileSlice
    bfs2 = BoundedFileSlice(test_file, 0, 512)
    efs2 = EncryptedFileSlice(bfs2, key, "0011223344556677", 0, 512)
    efs2.open()
    efs2.close()
    assert bfs2._file is None


def test_bug_08_verify_loopback_security():
    """
    BUG-08: Streaming loopback security rejects DNS-rebinding hosts like 127.evil.com.
    """
    req = MagicMock()
    req.client.host = "127.evil.com"
    req.headers.get.return_value = "127.evil.com"

    with pytest.raises(HTTPException) as exc_info:
        verify_loopback_security(req)
    assert exc_info.value.status_code == 403


def test_bug_13_storage_client_init():
    """
    BUG-13: Storage client initializes _flood_wait_until.
    """
    assert hasattr(storage_client, "_flood_wait_until")
    assert storage_client._flood_wait_until == 0.0


@pytest.mark.asyncio
async def test_bug_14_max_message_id_derivation():
    """
    BUG-14: If max_message_id is missing/0, derive from highest telegram_message_id in files.
    """
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM files")
        await db.commit()
    catalog = {
        "version": "1.0",
        "max_message_id": 0,
        "files": [
            {
                "id": "v_f1",
                "filename": "f1.bin",
                "parts": [{"part_index": 0, "telegram_message_id": 150}],
                "proxy": {
                    "id": "v_p1",
                    "parts": [{"part_index": 0, "telegram_message_id": 250}]
                }
            }
        ]
    }
    with patch.object(storage_client, "get_pinned_catalog", AsyncMock(return_value=catalog)):
        with patch.object(storage_client, "scan_channel_envelopes", AsyncMock(return_value=[])) as mock_scan:
            await perform_rebuild_index(force_full_scan=False)
            # Verify delta tail sync was triggered with min_id=250!
            mock_scan.assert_called_once_with(limit=1000, min_id=250)


@pytest.mark.asyncio
async def test_bug_07_pinned_catalog_protection():
    """
    BUG-07: Pinned catalog old message is only deleted if pinning succeeded.
    """
    catalog = {"version": "1.0", "files": []}
    mock_msg = MagicMock()
    mock_msg.id = 500

    with patch.object(storage_client, "client") as mock_client:
        mock_client.send_file = AsyncMock(return_value=mock_msg)
        # Pinning fails
        mock_client.pin_message = AsyncMock(side_effect=Exception("Pin failed"))
        mock_client.delete_messages = AsyncMock()

        # Call save_and_pin_catalog with old_pinned_id=400
        with patch.object(storage_client, "_is_connected", True):
            await storage_client.save_and_pin_catalog(catalog, old_pinned_id=400)

        # delete_messages must NOT be called for old pinned message 400!
        assert not mock_client.delete_messages.called


def test_bug_06_disk_space_guard(tmp_path):
    """
    BUG-06: Verifies 507 Insufficient Storage when free disk space is less than upload size * 1.05.
    """
    client = TestClient(app)
    # Mock disk_usage to report only 50 bytes free
    Usage = collections.namedtuple("Usage", ["total", "used", "free"])
    mock_usage = Usage(total=1000, used=950, free=50)

    with patch("shutil.disk_usage", return_value=mock_usage):
        # File of 100 bytes (> 50 / 1.05)
        file_payload = b"X" * 100
        res = client.post(
            "/api/v1/upload",
            files={"file": ("large_test.bin", file_payload, "application/octet-stream")}
        )
        assert res.status_code == 507
        assert "Insufficient Storage" in res.json()["detail"]


@pytest.mark.asyncio
async def test_bug_09_10_delta_tail_sync_sorting_and_status():
    """
    BUG-09 & BUG-10: Delta Tail Sync sorts parts ascending by part index,
    and sets upload_status='interrupted' if len(parts) < total_expected, or 'completed' if full.
    """
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM file_parts")
        await db.execute("DELETE FROM files")
        await db.commit()

    catalog = {
        "version": "1.0",
        "max_message_id": 100,
        "files": [
            {
                "id": "v_pinned_000000000000000000000001",
                "filename": "pinned.txt",
                "mime_type": "text/plain",
                "file_category": "document",
                "size_bytes": 50,
                "sha256": "sha_pinned",
                "total_parts": 1,
                "upload_status": "completed",
                "parts": [
                    {
                        "part_index": 0,
                        "byte_offset": 0,
                        "part_size_bytes": 50,
                        "part_sha256": "sha_pinned",
                        "telegram_message_id": 99
                    }
                ]
            }
        ]
    }

    # Simulate Telegram returning envelopes out-of-order for a completed file (parts 1 then 0)
    # and an incomplete file (part 0 only of total 3)
    fake_envelopes = [
        # File 1: completed, parts out of order
        {
            "v": "1.0",
            "id": "v_11111111111111111111111111111111",
            "part": 1,
            "total": 2,
            "name": "full.bin",
            "size": 2000,
            "offset": 1000,
            "sha256": "abc",
            "telegram_message_id": 105,
            "telegram_channel_id": "-1001",
            "tg_file_id": "f_105",
            "tg_access_hash": "a_105"
        },
        {
            "v": "1.0",
            "id": "v_11111111111111111111111111111111",
            "part": 0,
            "total": 2,
            "name": "full.bin",
            "size": 2000,
            "offset": 0,
            "sha256": "abc",
            "telegram_message_id": 104,
            "telegram_channel_id": "-1001",
            "tg_file_id": "f_104",
            "tg_access_hash": "a_104"
        },
        # File 2: interrupted (only part 0 of 3 arrived)
        {
            "v": "1.0",
            "id": "v_22222222222222222222222222222222",
            "part": 0,
            "total": 3,
            "name": "broken.bin",
            "size": 3000,
            "offset": 0,
            "sha256": "def",
            "telegram_message_id": 106,
            "telegram_channel_id": "-1001",
            "tg_file_id": "f_106",
            "tg_access_hash": "a_106"
        }
    ]

    with patch.object(storage_client, "get_pinned_catalog", AsyncMock(return_value=catalog)):
        with patch.object(storage_client, "scan_channel_envelopes", AsyncMock(return_value=fake_envelopes)):
            count = await perform_rebuild_index(force_full_scan=False)
            # 1 from pinned catalog + 2 from Delta Tail Sync = 3
            assert count == 3

    async with get_db() as db:
        # Check file 1 (reconciled by Delta Tail Sync as completed)
        cur1 = await db.execute("SELECT upload_status, total_parts FROM files WHERE id = 'v_11111111111111111111111111111111'")
        row1 = await cur1.fetchone()
        assert row1 is not None
        assert row1[0] == "completed"
        assert row1[1] == 2

        # Check file 1 parts ordering in db (sorted ascending)
        cur_p = await db.execute("SELECT part_index FROM file_parts WHERE file_id = 'v_11111111111111111111111111111111' ORDER BY part_index ASC")
        parts1 = [r[0] for r in await cur_p.fetchall()]
        assert parts1 == [0, 1]

        # Check file 2 (reconciled by Delta Tail Sync as interrupted because only 1 of 3 parts arrived)
        cur2 = await db.execute("SELECT upload_status, total_parts FROM files WHERE id = 'v_22222222222222222222222222222222'")
        row2 = await cur2.fetchone()
        assert row2 is not None
        assert row2[0] == "interrupted"
        assert row2[1] == 3


@pytest.mark.asyncio
async def test_bug_11_rebuild_single_flight_and_skip():
    """
    BUG-11: perform_rebuild_index skips non-forced rebuild when files exist in db,
    and locks concurrency with _rebuild_lock.
    """
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM file_parts")
        await db.execute("DELETE FROM files")
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, upload_status, sha256)
            VALUES ('v_33333333333333333333333333333333', 'test.txt', 'text/plain', 'document', 10, 'completed', 'sha_dummy')
            """
        )
        await db.commit()

    with patch("backend.api.routes._perform_rebuild_index_locked", AsyncMock()) as mock_locked:
        # Non-forced rebuild with existing files should skip
        res = await perform_rebuild_index(force_full_scan=False)
        assert res == 0
        assert not mock_locked.called

        # Forced rebuild should proceed
        mock_locked.return_value = 5
        res_forced = await perform_rebuild_index(force_full_scan=True)
        assert res_forced == 5
        assert mock_locked.called


@pytest.mark.asyncio
async def test_bug_12_chunk_upload_tg_message_cleanup_on_error():
    """
    BUG-12: If SQLite database write fails during chunk upload, the uploaded Telegram message
    is deleted so it does not become an orphan in Telegram.
    """
    from httpx import AsyncClient, ASGITransport
    file_id = "v_44444444444444444444444444444444"
    fake_msg_id = 888777
    chan_id = "-100123456"

    mock_part_res = {
        "message_id": fake_msg_id,
        "channel_id": chan_id,
        "file_id": "tg_fid_1",
        "access_hash": "tg_hash_1",
        "mock": False
    }

    await init_db()

    real_get_db = get_db

    call_count = 0
    def failing_get_db():
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise RuntimeError("Simulated SQLite write/lock error")
        return real_get_db()

    with patch.object(storage_client, "upload_part", AsyncMock(return_value=mock_part_res)):
        with patch.object(storage_client, "delete_messages", AsyncMock()) as mock_delete:
            with patch("backend.api.routes.get_db", side_effect=failing_get_db):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as ac:
                    with pytest.raises(RuntimeError, match="Simulated SQLite write/lock error"):
                        await ac.post(
                            "/api/v1/upload/chunk",
                            data={
                                "file_id": file_id,
                                "part_index": "0",
                                "total_parts": "1",
                                "byte_offset": "0",
                                "filename": "err.bin",
                                "total_size": "100"
                            },
                            files={"chunk": ("err.bin", b"X" * 100, "application/octet-stream")}
                        )
                    # Let pending background cleanup task run
                    await asyncio.sleep(0.05)
                    # Verify Telegram message cleanup was dispatched!
                    mock_delete.assert_called_once_with([fake_msg_id], channel_id=chan_id)


def test_bug_01_zip_guard_limit_in_frontend():
    """
    BUG-01: Verifies that frontend enforces the 500 MB MAX_IN_MEMORY_ZIP_BYTES guard.
    """
    frontend_store_file = settings.ROOT_DIR / "frontend" / "src" / "store" / "useUploadStore.ts"
    assert frontend_store_file.exists(), "useUploadStore.ts should exist"
    content = frontend_store_file.read_text(encoding="utf-8")
    assert "MAX_IN_MEMORY_ZIP_BYTES = 500 * 1024 * 1024" in content
    assert "exceeds 500 MB limit for in-memory ZIP generation" in content
    assert "get().addFilesToUpload(files)" in content


def test_bug_04_tauri_sigterm_and_grace_period():
    """
    BUG-04: Verifies src-tauri/src/lib.rs invokes loopback shutdown endpoint,
    sends SIGTERM (-15), and waits up to 2 seconds before force killing child.
    """
    lib_rs = settings.ROOT_DIR / "src-tauri" / "src" / "lib.rs"
    assert lib_rs.exists(), "lib.rs should exist"
    content = lib_rs.read_text(encoding="utf-8")
    assert 'const SHUTDOWN_URL: &str = "http://127.0.0.1:8000/api/v1/system/shutdown";' in content
    assert "ureq::post(SHUTDOWN_URL)" in content
    assert 'args(["-15", &child.id().to_string()])' in content
    assert "Duration::from_secs(2)" in content
    assert "child.try_wait()" in content
    assert "child.kill()" in content


@pytest.mark.asyncio
async def test_bug_07_pinned_catalog_semaphore():
    """
    BUG-07: Verifies storage_client._upload_semaphore protects catalog uploads.
    """
    assert hasattr(storage_client, "_upload_semaphore")
    assert isinstance(storage_client._upload_semaphore, asyncio.Semaphore)
    # Concurrency limit should be 3 workers
    assert storage_client._upload_semaphore._value == 3


