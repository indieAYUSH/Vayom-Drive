import os
import uuid
import asyncio
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.main import app, heartbeat_loop
from backend.config import settings
from backend.db.connection import get_db, init_db
from backend.storage.client import storage_client


async def _init_test_env():
    await init_db()
    settings.ensure_directories()
    await storage_client.disconnect()


def test_soft_deleted_file_blocked_from_thumbnail_and_download():
    async def _run():
        await _init_test_env()
        old_shares = settings.ENABLE_SHARE_LINKS
        settings.ENABLE_SHARE_LINKS = True
        client = TestClient(app)
        file_id = f"test-file-{uuid.uuid4().hex[:8]}"
        thumb_path = settings.THUMBNAIL_DIR / f"{file_id}.webp"
        thumb_path.write_bytes(b"RIFFmockwebp")
        # F-5: mock streaming now requires the blob to exist — seed it for message 12345
        mock_dir = settings.DATA_DIR / "mock_blobs"
        mock_dir.mkdir(parents=True, exist_ok=True)
        (mock_dir / "12345.bin").write_bytes(b"\x00" * 100)

        try:
            async with get_db() as db:
                await db.execute(
                    """
                    INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, has_thumbnail, is_deleted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (file_id, "photo.jpg", "image/jpeg", "image", 100, "fake-sha", 0, 1, 1)
                )
                await db.execute(
                    """
                    INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), file_id, 0, 0, 100, "fake-part-sha", 12345)
                )
                # Create a share link too
                token = f"tok-{uuid.uuid4().hex[:8]}"
                await db.execute(
                    """
                    INSERT INTO share_links (id, token, file_id, expires_at, max_views, view_count, is_revoked)
                    VALUES (?, ?, ?, datetime('now', '+1 day'), NULL, 0, 0)
                    """,
                    (f"shr-{uuid.uuid4().hex[:8]}", token, file_id)
                )
                await db.commit()

            # 1. When active, thumbnail and download return 200
            thumb_res = client.get(f"/api/v1/files/{file_id}/thumbnail")
            assert thumb_res.status_code == 200
            assert thumb_res.headers["content-type"] == "image/webp"

            dl_res = client.get(f"/api/v1/files/{file_id}/download")
            assert dl_res.status_code == 200

            share_res = client.get(f"/api/v1/s/{token}")
            assert share_res.status_code == 200

            # 2. Soft-delete the file
            del_res = client.delete(f"/api/v1/files/{file_id}")
            assert del_res.status_code == 200
            assert del_res.json() == {"success": True, "purged": False}

            # 3. Soft-deleted file must return 404 for thumbnail, download, and share link
            thumb_res_del = client.get(f"/api/v1/files/{file_id}/thumbnail")
            assert thumb_res_del.status_code == 404
            assert thumb_res_del.json()["detail"] == "Thumbnail not found"

            dl_res_del = client.get(f"/api/v1/files/{file_id}/download")
            assert dl_res_del.status_code == 404
            assert dl_res_del.json()["detail"] == "File not found"

            share_res_del = client.get(f"/api/v1/s/{token}")
            assert share_res_del.status_code == 404

            # 4. Check list_files has null thumbnail_url and preview_url for trash items
            list_res = client.get("/api/v1/files?is_deleted=true")
            assert list_res.status_code == 200
            trash_items = [f for f in list_res.json() if f["id"] == file_id]
            assert len(trash_items) == 1
            assert trash_items[0]["thumbnail_url"] is None
            assert trash_items[0]["preview_url"] is None

            # 5. Restore the file and verify access is restored
            rest_res = client.post(f"/api/v1/files/{file_id}/restore")
            assert rest_res.status_code == 200

            thumb_res_restored = client.get(f"/api/v1/files/{file_id}/thumbnail")
            assert thumb_res_restored.status_code == 200

        finally:
            settings.ENABLE_SHARE_LINKS = old_shares
            if thumb_path.exists():
                thumb_path.unlink()
            async with get_db() as db:
                await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
                await db.commit()

    asyncio.run(_run())


def test_nonexistent_ids_return_404():
    async def _run():
        await _init_test_env()
        client = TestClient(app)
        bad_id = f"nonexistent-{uuid.uuid4().hex}"

        # Soft delete nonexistent file
        res = client.delete(f"/api/v1/files/{bad_id}")
        assert res.status_code == 404
        assert res.json()["detail"] == "File not found"

        # Purge nonexistent file
        res = client.delete(f"/api/v1/files/{bad_id}?purge=true")
        assert res.status_code == 404
        assert res.json()["detail"] == "File not found"

        # Restore nonexistent file
        res = client.post(f"/api/v1/files/{bad_id}/restore")
        assert res.status_code == 404
        assert res.json()["detail"] == "File not found"

        # Revoke nonexistent share link
        res = client.post(f"/api/v1/shares/{bad_id}/revoke")
        assert res.status_code == 404
        assert res.json()["detail"] in ("Share link not found", "Share links feature is disabled in v1")

        # Delete nonexistent album
        res = client.delete(f"/api/v1/albums/{bad_id}")
        assert res.status_code == 404
        assert res.json()["detail"] == "Album not found"

    asyncio.run(_run())


def test_system_metrics_distinct_messages_and_total_parts():
    async def _run():
        await _init_test_env()
        client = TestClient(app)
        active_id = f"act-{uuid.uuid4().hex[:8]}"
        deleted_id = f"del-{uuid.uuid4().hex[:8]}"

        async with get_db() as db:
            # Clear existing test data
            await db.execute("DELETE FROM file_parts")
            await db.execute("DELETE FROM files")

            # Active file: 2 parts, 2 distinct telegram messages (1001, 1002)
            await db.execute(
                """
                INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, is_deleted)
                VALUES (?, 'active.bin', 'application/octet-stream', 'document', 2000, 'sha1', 1, 2, 0)
                """,
                (active_id,)
            )
            await db.execute(
                """
                INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id)
                VALUES (?, ?, 0, 0, 1000, 'p1', 1001), (?, ?, 1, 1000, 1000, 'p2', 1002)
                """,
                (str(uuid.uuid4()), active_id, str(uuid.uuid4()), active_id)
            )

            # Soft-deleted file: 1 part, message 1003
            await db.execute(
                """
                INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, is_deleted)
                VALUES (?, 'deleted.bin', 'application/octet-stream', 'document', 500, 'sha2', 0, 1, 1)
                """,
                (deleted_id,)
            )
            await db.execute(
                """
                INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id)
                VALUES (?, ?, 0, 0, 500, 'p3', 1003)
                """,
                (str(uuid.uuid4()), deleted_id)
            )
            await db.commit()

        res = client.get("/api/v1/system/metrics")
        assert res.status_code == 200
        data = res.json()

        assert data["total_files"] == 1
        assert data["total_bytes"] == 2000
        assert data["total_parts"] == 2
        assert data["telegram_messages"] == 2

    asyncio.run(_run())


def test_storage_client_flood_wait_and_metrics():
    async def _run():
        await _init_test_env()
        client = TestClient(app)

        # Initially offline / reconnecting
        storage_client._is_connected = False
        assert storage_client.online_status == "reconnecting"
        assert storage_client.flood_wait_seconds == 0

        # Simulate connected
        storage_client._is_connected = True
        assert storage_client.online_status == "online"

        # Set flood wait for 45 seconds
        storage_client.set_flood_wait(45)
        assert storage_client.flood_wait_seconds >= 40
        assert storage_client.online_status == "flood_wait"

        res = client.get("/api/v1/system/metrics")
        assert res.status_code == 200
        data = res.json()
        assert data["online_status"] == "flood_wait"
        assert data["flood_wait_seconds"] >= 40

        # Disconnect clears flood wait
        await storage_client.disconnect()
        assert storage_client.flood_wait_seconds == 0
        assert storage_client.online_status == "reconnecting"

    asyncio.run(_run())


def test_heartbeat_loop_clean_cancellation():
    async def _run():
        task = asyncio.create_task(heartbeat_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.done()

    asyncio.run(_run())


def test_upload_failure_cleanup():
    async def _run():
        await _init_test_env()
        client = TestClient(app, raise_server_exceptions=False)
        task_id = f"fail-task-{uuid.uuid4().hex[:8]}"

        deleted_parts_recorded = []
        original_delete_parts = storage_client.delete_parts
        async def mock_delete_parts(parts):
            deleted_parts_recorded.extend(parts)
            return await original_delete_parts(parts)

        storage_client.delete_parts = mock_delete_parts

        # Mock split_file_if_needed to simulate 2 parts
        from backend.api import routes
        original_split = routes.split_file_if_needed
        def mock_split(file_path, *args, **kwargs):
            return [
                {"part_index": 0, "byte_offset": 0, "part_size_bytes": 10, "part_sha256": "sha1", "file_path": str(file_path)},
                {"part_index": 1, "byte_offset": 10, "part_size_bytes": 10, "part_sha256": "sha2", "file_path": str(file_path)},
            ]
        routes.split_file_if_needed = mock_split

        upload_call_count = 0
        original_upload_part = storage_client.upload_part
        async def mock_upload_part(file_path, caption=None, progress_callback=None):
            nonlocal upload_call_count
            upload_call_count += 1
            if upload_call_count == 1:
                return {
                    "message_id": 999123,
                    "channel_id": "mock_chan",
                    "file_id": "mock_f1",
                    "access_hash": "0"
                }
            raise RuntimeError("Simulated MTProto connection crash on part 2")

        storage_client.upload_part = mock_upload_part

        try:
            # Check temp dirs before
            existing_temp_dirs = set(os.listdir(settings.UPLOAD_TEMP_DIR)) if settings.UPLOAD_TEMP_DIR.exists() else set()

            files = {"file": ("test_fail.bin", b"01234567890123456789", "application/octet-stream")}
            res = client.post(f"/api/v1/upload?task_id={task_id}", files=files)
            assert res.status_code == 500

            # 1. Verify orphan Telegram message from part 1 was deleted
            assert len(deleted_parts_recorded) == 1
            assert deleted_parts_recorded[0]["telegram_message_id"] == 999123

            # 2. Verify temp dir was cleaned up
            current_temp_dirs = set(os.listdir(settings.UPLOAD_TEMP_DIR)) if settings.UPLOAD_TEMP_DIR.exists() else set()
            new_temp_dirs = current_temp_dirs - existing_temp_dirs
            assert len(new_temp_dirs) == 0

            # 3. Verify progress tracker was set to failed with error message
            progress_res = client.get(f"/api/v1/upload/{task_id}/progress")
            assert progress_res.status_code == 200
            prog_data = progress_res.json()
            assert prog_data["status"] == "failed"
            assert "Simulated MTProto connection crash" in prog_data["error"]

            # 4. Verify no files or parts committed to SQLite
            async with get_db() as db:
                c1 = await db.execute("SELECT COUNT(*) FROM files WHERE filename = 'test_fail.bin'")
                assert (await c1.fetchone())[0] == 0
                c2 = await db.execute("SELECT COUNT(*) FROM file_parts WHERE telegram_message_id = 999123")
                assert (await c2.fetchone())[0] == 0

        finally:
            storage_client.delete_parts = original_delete_parts
            storage_client.upload_part = original_upload_part
            routes.split_file_if_needed = original_split

    asyncio.run(_run())

