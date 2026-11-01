import pytest
import uuid
import aiosqlite
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from backend.db.connection import get_db, init_db
from backend.storage.adapters.base import BaseStorageAdapter
from backend.storage.adapters.telegram import TelegramStorageAdapter
from backend.storage.manager import get_storage_adapter, register_storage_adapter, stream_part_bytes_universal
from backend.streaming.engine import StreamingEngine, RangeSpec


@pytest.mark.asyncio
async def test_phase1_db_schema_and_indexes():
    """Validates that storage_backend and storage_uri columns and covering indexes exist."""
    await init_db()
    async with get_db() as db:
        # Check files table columns
        cursor = await db.execute("PRAGMA table_info(files)")
        files_cols = [row["name"] for row in await cursor.fetchall()]
        assert "storage_backend" in files_cols

        # Check file_parts table columns
        cursor = await db.execute("PRAGMA table_info(file_parts)")
        parts_cols = [row["name"] for row in await cursor.fetchall()]
        assert "storage_backend" in parts_cols
        assert "storage_uri" in parts_cols

        # Check covering index on file_parts
        cursor = await db.execute("PRAGMA index_list(file_parts)")
        indexes = [row["name"] for row in await cursor.fetchall()]
        assert "idx_file_parts_storage_covering" in indexes


@pytest.mark.asyncio
async def test_phase1_non_telegram_parts_insert_without_constraint_violation():
    """Validates that file_parts without telegram_message_id (message_id=0) can be inserted cleanly."""
    await init_db()
    file_id = f"test-phase1-{uuid.uuid4().hex[:8]}"

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, storage_backend)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, "test_doc.pdf", "application/pdf", "document", 1024, "fake-sha", "nas")
        )

        # Insert part with telegram_message_id = 0, storage_backend = 'nas', storage_uri = 'nas:///mnt/vault/test_doc.pdf'
        part_id = str(uuid.uuid4())
        await db.execute(
            """
            INSERT INTO file_parts (
                id, file_id, part_index, byte_offset, part_size_bytes, part_sha256,
                telegram_message_id, storage_backend, storage_uri
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (part_id, file_id, 0, 0, 1024, "fake-sha", 0, "nas", "nas:///mnt/vault/test_doc.pdf")
        )
        await db.commit()

        # Query back using covering index fields
        cursor = await db.execute(
            """
            SELECT byte_offset, part_size_bytes, storage_backend, storage_uri, telegram_message_id
            FROM file_parts WHERE file_id = ?
            """,
            (file_id,)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["storage_backend"] == "nas"
        assert row["storage_uri"] == "nas:///mnt/vault/test_doc.pdf"
        assert row["telegram_message_id"] == 0

        # Clean up
        await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await db.commit()


@pytest.mark.asyncio
async def test_phase1_telegram_adapter_upload_and_stream():
    """Validates TelegramStorageAdapter upload, streaming, and delete delegations."""
    mock_client = MagicMock()
    mock_client.upload_part = AsyncMock(return_value={
        "message_id": 12345,
        "channel_id": "-1009999",
        "file_id": 999,
        "access_hash": 888
    })

    async def _mock_stream(*args, **kwargs):
        yield b"chunk1"
        yield b"chunk2"

    mock_client.stream_part_bytes = MagicMock(side_effect=_mock_stream)
    mock_client.delete_parts = AsyncMock(return_value=1)
    mock_client.check_channel_health = AsyncMock(return_value={"status": "online"})

    adapter = TelegramStorageAdapter(client=mock_client)
    assert adapter.backend_name == "telegram"

    # Test upload
    res = await adapter.upload(
        local_path=Path("/tmp/fake.bin"),
        file_id="f-123",
        filename="fake.bin",
        part_index=0,
        total_parts=1,
        byte_offset=0,
        part_size=12,
        sha256="abc"
    )
    assert res["storage_backend"] == "telegram"
    assert res["storage_uri"] == "tg://-1009999/12345"
    assert res["telegram_message_id"] == 12345

    # Test stream_bytes
    streamed = bytearray()
    async for block in adapter.stream_bytes(storage_uri="tg://-1009999/12345", offset=0, limit=12):
        streamed.extend(block)
    assert bytes(streamed) == b"chunk1chunk2"

    # Test delete
    del_count = await adapter.delete_parts([{"telegram_message_id": 12345}])
    assert del_count == 1


@pytest.mark.asyncio
async def test_phase1_storage_manager_dispatch():
    """Validates get_storage_adapter and stream_part_bytes_universal registry."""
    # Default is telegram
    adapter_default = get_storage_adapter()
    assert adapter_default.backend_name == "telegram"

    # Register custom mock adapter
    class MockCustomAdapter(BaseStorageAdapter):
        backend_name = "mock_cloud"

        async def upload(self, *args, **kwargs):
            return {"storage_backend": "mock_cloud", "storage_uri": "mock://1"}

        async def stream_bytes(self, storage_uri, offset=0, limit=None, chunk_size=512*1024, **kwargs):
            yield b"mock_cloud_data"

        async def delete_parts(self, parts):
            return len(parts)

        async def health_check(self):
            return {"status": "ok"}

    register_storage_adapter("mock_cloud", MockCustomAdapter())
    adapter_mock = get_storage_adapter("mock_cloud")
    assert adapter_mock.backend_name == "mock_cloud"

    # Test universal streaming dispatch
    part_mock = {
        "storage_backend": "mock_cloud",
        "storage_uri": "mock://path/to/object",
        "byte_offset": 0,
        "part_size_bytes": 15
    }
    buf = bytearray()
    async for chunk in stream_part_bytes_universal(part_mock, offset=0, limit=15):
        buf.extend(chunk)
    assert bytes(buf) == b"mock_cloud_data"


@pytest.mark.asyncio
async def test_phase1_streaming_engine_with_multi_storage_part():
    """Validates VirtualStreamingEngine streaming from a non-telegram backend via storage adapter."""
    engine = StreamingEngine(chunk_size=64 * 1024, cache_size=64)

    class MockStorageAdapter(BaseStorageAdapter):
        backend_name = "test_nas"

        async def upload(self, *args, **kwargs):
            return {}

        async def stream_bytes(self, storage_uri, offset=0, limit=None, chunk_size=512*1024, **kwargs):
            # Return predictable bytes: b"0123456789" repeated
            full = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 10  # 260 bytes
            sliced = full[offset:offset + (limit or len(full))]
            yield sliced

        async def delete_parts(self, parts):
            return 1

        async def health_check(self):
            return {}

    register_storage_adapter("test_nas", MockStorageAdapter())

    parts = [{
        "id": "p-test-1",
        "file_id": "f-test-1",
        "part_index": 0,
        "byte_offset": 0,
        "part_size_bytes": 260,
        "storage_backend": "test_nas",
        "storage_uri": "nas:///vault/test.bin",
        "telegram_message_id": 0
    }]

    range_spec = RangeSpec(
        start=10,
        end=29,
        total_size=260,
        content_length=20,
        is_range=True,
        is_satisfiable=True
    )

    result_bytes = bytearray()
    async for chunk in engine.stream_range(
        parts=parts,
        total_size=260,
        range_spec=range_spec,
        storage_client=MagicMock()
    ):
        result_bytes.extend(chunk)

    expected = (b"ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 10)[10:30]
    assert bytes(result_bytes) == expected
