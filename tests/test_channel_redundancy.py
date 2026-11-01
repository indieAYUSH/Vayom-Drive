import io
import json
import uuid
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.db.connection import get_db, init_db
from backend.storage.client import TelegramStorageClient, storage_client

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_db():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(init_db())
    yield


def test_channel_health_endpoint():
    """Validates GET /api/v1/vault/channel-health response structure and fields."""
    response = client.get("/api/v1/vault/channel-health")
    assert response.status_code == 200
    data = response.json()

    assert "online" in data
    assert "flood_wait_seconds" in data
    assert "max_concurrent_workers" in data
    assert data["max_concurrent_workers"] == settings.MAX_CONCURRENT_UPLOADS
    assert "primary_channel" in data
    assert "backup_channel" in data
    assert "saved_messages_mirror" in data
    assert "local_backup" in data
    assert "exists" in data["local_backup"]


def test_set_backup_channel_endpoint():
    """Validates configuring secondary mirror channel via POST /api/v1/vault/backup-channel."""
    test_backup_id = "-10088887777"
    response = client.post(
        "/api/v1/vault/backup-channel",
        json={"backup_channel_id": test_backup_id}
    )
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "configured"
    assert res_data["backup_channel_id"] == test_backup_id
    assert settings.TELEGRAM_BACKUP_CHANNEL_ID == test_backup_id


def test_catalog_export_and_import_roundtrip():
    """
    Validates disaster recovery:
    1. Seed database with test file & part
    2. Export catalog via GET /api/v1/vault/catalog/export
    3. Wipe database file records
    4. Restore via POST /api/v1/vault/catalog/import
    5. Verify complete restoration of file metadata and part pointers
    """
    async def _run():
        file_id = f"test-export-{uuid.uuid4().hex[:8]}"
        part_id = f"part-{uuid.uuid4().hex[:8]}"

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, is_deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (file_id, "mission_critical_doc.pdf", "application/pdf", "document", 1048576, "fake-sha-256", 0, 1)
            )
            await db.execute(
                """
                INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id, telegram_channel_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (part_id, file_id, 0, 0, 1048576, "fake-sha-256", 45678, "-1001234567890")
            )
            await db.commit()

        # 1. Export catalog
        export_res = client.get("/api/v1/vault/catalog/export")
        assert export_res.status_code == 200
        assert "attachment; filename=" in export_res.headers.get("content-disposition", "")
        catalog_json = export_res.json()
        assert "files" in catalog_json
        assert any(f["id"] == file_id for f in catalog_json["files"])

        # 2. Wipe database
        async with get_db() as db:
            await db.execute("DELETE FROM file_parts WHERE file_id = ?", (file_id,))
            await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
            await db.commit()

        # Verify wipe
        async with get_db() as db:
            cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
            assert await cur.fetchone() is None

        # 3. Import catalog
        import_payload = json.dumps(catalog_json).encode("utf-8")
        import_res = client.post(
            "/api/v1/vault/catalog/import",
            files={"catalog_file": ("backup.json", io.BytesIO(import_payload), "application/json")}
        )
        assert import_res.status_code == 200
        import_data = import_res.json()
        assert import_data["status"] == "success"
        assert import_data["restored_files"] >= 1

        # 4. Verify restored record
        async with get_db() as db:
            cur = await db.execute("SELECT id, filename, size_bytes FROM files WHERE id = ?", (file_id,))
            row = await cur.fetchone()
            assert row is not None
            assert row["filename"] == "mission_critical_doc.pdf"
            assert row["size_bytes"] == 1048576

            p_cur = await db.execute("SELECT part_index, telegram_message_id FROM file_parts WHERE file_id = ?", (file_id,))
            p_row = await p_cur.fetchone()
            assert p_row is not None
            assert p_row["telegram_message_id"] == 45678

    asyncio.run(_run())


def test_multitier_catalog_recovery_fallback():
    """
    Validates 4-Tier Fallback in get_pinned_catalog:
    - Tier 1: Primary Channel
    - Tier 2: Secondary Backup Channel
    - Tier 3: Telegram Saved Messages ('me')
    - Tier 4: Local Snapshot File (vault_catalog_backup.json)
    """
    async def _run():
        tc = TelegramStorageClient()
        tc._is_connected = True
        settings.TELEGRAM_CHANNEL_ID = "-1001111"
        settings.TELEGRAM_BACKUP_CHANNEL_ID = "-1002222"
        settings.AUTO_MIRROR_SAVED_MESSAGES = True
        settings.AUTH_MODE = "qr"

        mock_client = AsyncMock()
        mock_full_chat = MagicMock()
        mock_full_chat.pinned_msg_id = None
        mock_full_channel = MagicMock()
        mock_full_channel.full_chat = mock_full_chat
        mock_client.return_value = mock_full_channel
        tc.client = mock_client

        # Preserve existing local snapshot if present
        local_backup_path = settings.DATA_DIR / "vault_catalog_backup.json"
        saved_snapshot = local_backup_path.read_text(encoding="utf-8") if local_backup_path.exists() else None
        if local_backup_path.exists():
            local_backup_path.unlink()

        try:
            # Case 1: Primary channel has catalog
            async def mock_get_messages_t1(entity, **kwargs):
                if str(entity) == "-1001111":
                    msg = MagicMock()
                    msg.text = 'VAYOM:CATALOG:{"source": "primary_channel"}'
                    msg.media = None
                    return [msg]
                return []

            mock_client.get_messages = AsyncMock(side_effect=mock_get_messages_t1)
            catalog = await tc.get_pinned_catalog()
            assert catalog is not None
            assert catalog.get("source") == "primary_channel"

            # Case 2: Primary channel empty/fails, secondary backup channel succeeds
            async def mock_get_messages_t2(entity, **kwargs):
                if str(entity) == "-1002222":
                    msg = MagicMock()
                    msg.text = 'VAYOM:CATALOG:{"source": "backup_channel"}'
                    msg.media = None
                    return [msg]
                return []

            mock_client.get_messages = AsyncMock(side_effect=mock_get_messages_t2)
            catalog = await tc.get_pinned_catalog()
            assert catalog is not None
            assert catalog.get("source") == "backup_channel"

            # Case 3: Primary & secondary fail, Saved Messages ('me') succeeds
            async def mock_get_messages_t3(entity, **kwargs):
                if entity == "me":
                    msg = MagicMock()
                    msg.text = 'VAYOM:CATALOG:{"source": "saved_messages"}'
                    msg.media = None
                    return [msg]
                return []

            mock_client.get_messages = AsyncMock(side_effect=mock_get_messages_t3)
            catalog = await tc.get_pinned_catalog()
            assert catalog is not None
            assert catalog.get("source") == "saved_messages"

            # Case 4: All Telegram channels fail, falls back to local snapshot file
            mock_client.get_messages = AsyncMock(return_value=[])
            local_backup_path.write_text(json.dumps({"source": "local_snapshot"}), encoding="utf-8")

            catalog = await tc.get_pinned_catalog()
            assert catalog is not None
            assert catalog.get("source") == "local_snapshot"

        finally:
            # Restore saved snapshot
            if saved_snapshot is not None:
                local_backup_path.write_text(saved_snapshot, encoding="utf-8")
            elif local_backup_path.exists():
                local_backup_path.unlink()

    asyncio.run(_run())
