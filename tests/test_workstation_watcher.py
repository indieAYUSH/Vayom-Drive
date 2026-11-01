import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch
import asyncio
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.db.connection import get_db, init_db
from backend.workstation.watcher import LocalFolderWatcher
from backend.storage.client import storage_client

client = TestClient(app)


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


def test_watch_folder_rest_crud():
    """Tests the full REST lifecycle of workstation watch folders."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Error: Non-existent directory
        resp = client.post(
            "/api/v1/workstation/watch-folders",
            json={"path": "/non/existent/path/xyz_123", "label": "Invalid"}
        )
        assert resp.status_code == 400

        # 2. Success: Add valid directory
        resp = client.post(
            "/api/v1/workstation/watch-folders",
            json={"path": tmpdir, "label": "Test Watch Drop", "auto_delete_local": False}
        )
        assert resp.status_code == 200
        data = resp.json()
        folder_id = data["id"]
        assert data["label"] == "Test Watch Drop"
        assert data["exists"] is True

        # 3. Conflict: Registering same path twice
        dup_resp = client.post(
            "/api/v1/workstation/watch-folders",
            json={"path": tmpdir, "label": "Duplicate"}
        )
        assert dup_resp.status_code == 409

        # 4. List folders
        list_resp = client.get("/api/v1/workstation/watch-folders")
        assert list_resp.status_code == 200
        folders = list_resp.json()
        assert any(f["id"] == folder_id for f in folders)

        # 5. Patch folder
        patch_resp = client.patch(
            f"/api/v1/workstation/watch-folders/{folder_id}",
            json={"label": "Updated Test Label", "auto_delete_local": True}
        )
        assert patch_resp.status_code == 200

        # 6. Delete folder
        del_resp = client.delete(f"/api/v1/workstation/watch-folders/{folder_id}")
        assert del_resp.status_code == 200

        # Verify deleted
        list_resp2 = client.get("/api/v1/workstation/watch-folders")
        assert not any(f["id"] == folder_id for f in list_resp2.json())


def test_local_folder_watcher_ingestion_and_deduplication():
    """
    Tests LocalFolderWatcher file processing:
    1. First ingestion: chunks, encrypts, uploads via storage_client, commits to files & watch_folder_events.
    2. Second ingestion of identical file: detects matching SHA-256 and skips re-upload.
    """
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            folder_id = f"wf_{uuid.uuid4().hex[:12]}"
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO watch_folders (id, path, label, auto_delete_local, is_active) VALUES (?, ?, ?, 0, 1)",
                    (folder_id, tmpdir, "Unit Test Watcher")
                )
                await db.commit()

            test_file = Path(tmpdir) / "drone_clip.txt"
            test_file.write_text("Unique sample content for media workstation ingestion testing.")

            mock_upload_res = {
                "message_id": 999123,
                "channel_id": "-1001234567890",
                "file_id": 888777,
                "access_hash": 666555
            }

            watcher = LocalFolderWatcher()

            with patch.object(storage_client, "upload_part", new_callable=AsyncMock) as mock_upload, \
                 patch.object(watcher, "_sync_catalog", new_callable=AsyncMock):

                mock_upload.return_value = mock_upload_res

                # Fast debounce during unit tests: patch asyncio.sleep to no-op
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await watcher.process_file(folder_id=folder_id, file_path=test_file, auto_delete_local=False)

                assert mock_upload.called
                assert mock_upload.call_count == 1

                async with get_db() as db:
                    cur = await db.execute("SELECT id, filename, sha256 FROM files WHERE filename = ?", (test_file.name,))
                    file_row = await cur.fetchone()
                    assert file_row is not None
                    file_id = file_row[0]

                    cur_enc = await db.execute("SELECT encryption, nonce FROM files WHERE id = ?", (file_id,))
                    enc_row = await cur_enc.fetchone()
                    assert enc_row[0] == "none"
                    assert enc_row[1] is None

                    cur2 = await db.execute("SELECT status, file_id FROM watch_folder_events WHERE folder_id = ?", (folder_id,))
                    event_row = await cur2.fetchone()
                    assert event_row is not None
                    assert event_row[0] == "synced"
                    assert event_row[1] == file_id

                mock_upload.reset_mock()

                # Deduplication: same file again
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await watcher.process_file(folder_id=folder_id, file_path=test_file, auto_delete_local=False)

                assert mock_upload.call_count == 0

    asyncio.run(_run())


def test_watcher_folder_matching_uses_path_components():
    """
    I-8: a changed path must be matched to its watch root by path components,
    never by raw string prefix, and nested roots resolve to the most specific one.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        media = base / "media"
        media1 = base / "media1"
        nested = media / "sub"
        for d in (media, media1, nested):
            d.mkdir(parents=True, exist_ok=True)

        cfg_media = {"id": "wf_media", "auto_delete_local": False, "target_album_id": None}
        cfg_media1 = {"id": "wf_media1", "auto_delete_local": True, "target_album_id": "al_1"}
        cfg_nested = {"id": "wf_nested", "auto_delete_local": True, "target_album_id": "al_2"}
        valid_folders = {
            str(media): cfg_media,
            str(media1): cfg_media1,
            str(nested): cfg_nested,
        }

        match = LocalFolderWatcher._match_folder_config(str(media1 / "clip.mp4"), valid_folders)
        assert match is cfg_media1  # sibling prefix trap: must NOT be cfg_media

        match = LocalFolderWatcher._match_folder_config(str(media / "clip.mp4"), valid_folders)
        assert match is cfg_media

        # The most specific nested root wins regardless of dict order
        match = LocalFolderWatcher._match_folder_config(str(nested / "clip.mp4"), valid_folders)
        assert match is cfg_nested

        # A path outside every registered root matches nothing
        match = LocalFolderWatcher._match_folder_config(str(base / "elsewhere" / "clip.mp4"), valid_folders)
        assert match is None


def test_watcher_should_ignore_hard_excludes_data_dir_and_db_files(tmp_path):
    """
    I-7: Verifies that _should_ignore strictly excludes:
    1. settings.DATA_DIR and subdirectories (vayom.db, UPLOAD_TEMP_DIR, THUMBNAIL_DIR).
    2. settings.ROOT_DIR.
    3. SQLite/database files (.db, .db-wal, .db-shm, .journal).
    4. Temporary/hidden files (.tmp, .part, .crdownload, .swp, .lock, .*, ~*).
    5. Normal media files outside data directories are NOT ignored.
    """
    watcher = LocalFolderWatcher()

    # 1. Application data & internal subdirectories
    assert watcher._should_ignore(settings.DATA_DIR / "vayom.db") is True
    assert watcher._should_ignore(settings.DATA_DIR / "vayom.db-wal") is True
    assert watcher._should_ignore(settings.DATA_DIR / "vayom.db-shm") is True
    assert watcher._should_ignore(settings.UPLOAD_TEMP_DIR / "chunk_123.bin") is True
    assert watcher._should_ignore(settings.THUMBNAIL_DIR / "preview.webp") is True
    assert watcher._should_ignore(settings.ROOT_DIR / ".env") is True
    assert watcher._should_ignore(settings.ROOT_DIR / "backend" / "main.py") is True

    # 2. Database and temporary file extensions even if placed elsewhere
    assert watcher._should_ignore(tmp_path / "custom.db") is True
    assert watcher._should_ignore(tmp_path / "custom.db-wal") is True
    assert watcher._should_ignore(tmp_path / "custom.db-shm") is True
    assert watcher._should_ignore(tmp_path / "custom.journal") is True
    assert watcher._should_ignore(tmp_path / "clip.tmp") is True
    assert watcher._should_ignore(tmp_path / "clip.crdownload") is True
    assert watcher._should_ignore(tmp_path / "clip.part") is True
    assert watcher._should_ignore(tmp_path / "clip.swp") is True
    assert watcher._should_ignore(tmp_path / "clip.lock") is True
    assert watcher._should_ignore(tmp_path / ".hidden_clip.mp4") is True
    assert watcher._should_ignore(tmp_path / "~backup_clip.mp4") is True

    # 3. Legitimate user media files outside DATA_DIR must NOT be ignored
    assert watcher._should_ignore(tmp_path / "my_holiday_video.mp4") is False
    assert watcher._should_ignore(tmp_path / "sunset.jpg") is False
    assert watcher._should_ignore(tmp_path / "audio_track.flac") is False


@pytest.mark.asyncio
async def test_watcher_refuses_processing_data_dir_files(tmp_path):
    """
    I-7: Verifies that LocalFolderWatcher.process_file immediately bails out
    when presented with a file in DATA_DIR, never uploading or auto-deleting it.
    """
    watcher = LocalFolderWatcher()
    mock_upload = AsyncMock()

    test_db_file = settings.DATA_DIR / "test_sentinel.db"
    test_db_file.write_text("critical database content")

    try:
        with patch.object(storage_client, "upload_part", mock_upload):
            await watcher.process_file(
                folder_id="wf_test",
                file_path=test_db_file,
                auto_delete_local=True
            )

        # Upload was never triggered
        mock_upload.assert_not_called()
        # File was never deleted
        assert test_db_file.exists()
        assert test_db_file.read_text() == "critical database content"
    finally:
        test_db_file.unlink(missing_ok=True)


def test_watcher_replays_change_events_that_arrive_during_sync():
    """
    I-34: a change event for a path that is mid-sync must not be dropped —
    the ingest worker replays it after the active run finishes.
    """
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            folder_id = "wf_dirty_replay"
            test_file = Path(tmpdir) / "live_edit.bin"
            test_file.write_text("v1")

            watcher = LocalFolderWatcher()
            key = str(test_file.resolve())
            observed = []

            async def fake_process_file(folder_id, file_path, auto_delete_local=False, target_album_id=None):
                observed.append(file_path.read_text())
                if len(observed) == 1:
                    # The user writes again while the first ingest is running
                    test_file.write_text("v2")
                    watcher._syncing_paths.add(key)
                    try:
                        queued = await watcher._enqueue(folder_id, file_path, auto_delete_local=False)
                        assert queued is False
                        assert key in watcher._dirty_items
                    finally:
                        watcher._syncing_paths.discard(key)

            try:
                with patch.object(watcher, "process_file", new=fake_process_file):
                    watcher._ensure_ingest_pool()
                    await watcher._enqueue(folder_id, test_file)

                    # Wait for the deferred replay (second ingest) to run
                    for _ in range(300):
                        if len(observed) >= 2:
                            break
                        await asyncio.sleep(0.01)

                assert observed == ["v1", "v2"]
                assert key not in watcher._dirty_items
            finally:
                await watcher.stop()

    asyncio.run(_run())


def test_watcher_keeps_dirty_file_when_auto_delete_requested():
    """
    I-34: with auto_delete_local=1, a file that changed during sync must be
    kept on disk so the deferred re-ingest can upload the newer bytes; a
    clean (non-dirty) file is still deleted as before.
    """
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            folder_id = f"wf_{uuid.uuid4().hex[:12]}"
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO watch_folders (id, path, label, auto_delete_local, is_active) VALUES (?, ?, ?, 1, 1)",
                    (folder_id, tmpdir, "Dirty Guard Test")
                )
                await db.commit()

            test_file = Path(tmpdir) / "changed_clip.txt"
            test_file.write_text("newer bytes that arrived during sync")
            key = str(test_file.resolve())

            watcher = LocalFolderWatcher()
            # Simulate the change event that landed while this path was syncing
            watcher._dirty_items[key] = {
                "folder_id": folder_id,
                "file_path": test_file,
                "auto_delete_local": True,
                "target_album_id": None,
            }

            mock_upload_res = {
                "message_id": 999321,
                "channel_id": "-1001234567890",
                "file_id": 888111,
                "access_hash": 666222
            }

            with patch.object(storage_client, "upload_part", new_callable=AsyncMock) as mock_upload, \
                 patch.object(watcher, "_sync_catalog", new_callable=AsyncMock):
                mock_upload.return_value = mock_upload_res
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await watcher.process_file(folder_id=folder_id, file_path=test_file, auto_delete_local=True)

            assert mock_upload.called
            # I-34: the dirty file must survive the auto-delete pass
            assert test_file.exists()
            assert key in watcher._dirty_items

            # Sanity: once the path is no longer dirty, auto-delete still works
            test_file.write_text("completely different final content")
            watcher._dirty_items.clear()

            with patch.object(storage_client, "upload_part", new_callable=AsyncMock) as mock_upload2, \
                 patch.object(watcher, "_sync_catalog", new_callable=AsyncMock):
                mock_upload2.return_value = mock_upload_res
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await watcher.process_file(folder_id=folder_id, file_path=test_file, auto_delete_local=True)

            assert mock_upload2.called
            assert not test_file.exists()

    asyncio.run(_run())

