import pytest
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

import backend.storage.adapters.nas as nas_module
from backend.main import app
from backend.config import settings
from backend.db.connection import get_db, init_db
from backend.storage.adapters.nas import NASStorageAdapter
from backend.storage.manager import register_storage_adapter, get_storage_adapter
from backend.streaming.engine import StreamingEngine, RangeSpec


@pytest.fixture
def temp_nas_dir():
    temp_dir = tempfile.mkdtemp(prefix="vayom_nas_test_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def nas_adapter(temp_nas_dir):
    adapter = NASStorageAdapter(nas_root=temp_nas_dir)
    register_storage_adapter("nas", adapter)
    return adapter


@pytest.mark.asyncio
async def test_nas_native_unsplit_upload_and_stream(nas_adapter, temp_nas_dir):
    """Validates Constraint 1: single continuous unsplit file and range streaming."""
    await init_db()
    file_id = f"nas-test-{uuid.uuid4().hex[:8]}"
    content = b"Hello, this is a native unsplit NAS file test. 0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    # Create dummy source file
    src_file = temp_nas_dir / "temp_src.bin"
    src_file.write_bytes(content)

    res = await nas_adapter.upload(
        local_path=src_file,
        file_id=file_id,
        filename="my_document.bin",
        part_size=len(content),
        sha256="fake-sha"
    )

    # Constraint 1 verification
    assert res["storage_backend"] == "nas"
    assert res["part_index"] == 0
    assert res["total_parts"] == 1
    assert res["byte_offset"] == 0
    assert res["part_size_bytes"] == len(content)
    assert res["telegram_message_id"] == 0

    # Verify physical file on disk
    physical_path = temp_nas_dir / res["storage_uri"]
    assert physical_path.exists()
    assert physical_path.read_bytes() == content

    # Test Range Streaming
    streamed = bytearray()
    async for chunk in nas_adapter.stream_bytes(res["storage_uri"], offset=7, limit=10):
        streamed.extend(chunk)
    assert bytes(streamed) == content[7:17]


@pytest.mark.asyncio
async def test_nas_sandbox_security_path_traversal(nas_adapter):
    """Validates Constraint 5: rejects any path traversal attempts escaping nas_root."""
    await init_db()

    # Attempt ../ escape
    with pytest.raises(PermissionError):
        nas_adapter._resolve_and_verify("../../../etc/passwd")

    with pytest.raises(PermissionError):
        nas_adapter._resolve_and_verify("subfolder/../../../../etc/shadow")


@pytest.mark.asyncio
async def test_nas_collision_guard(nas_adapter, temp_nas_dir):
    """Validates collision handling: appends short ID if filename exists."""
    await init_db()
    f1 = temp_nas_dir / "src1.txt"
    f1.write_bytes(b"content 1")
    f2 = temp_nas_dir / "src2.txt"
    f2.write_bytes(b"content 2")

    res1 = await nas_adapter.upload(f1, file_id="file11112222", filename="notes.txt")
    assert res1["storage_uri"] == "notes.txt"

    res2 = await nas_adapter.upload(f2, file_id="file33334444", filename="notes.txt")
    assert res2["storage_uri"] != "notes.txt"
    assert "notes_file3333" in res2["storage_uri"]

    assert (temp_nas_dir / res1["storage_uri"]).read_bytes() == b"content 1"
    assert (temp_nas_dir / res2["storage_uri"]).read_bytes() == b"content 2"


@pytest.mark.asyncio
async def test_nas_zero_copy_sync_and_auto_folder_mapping(nas_adapter, temp_nas_dir):
    """Validates Zero-Copy In-Place indexing and automatic folder-to-album mapping."""
    await init_db()

    # Create directory tree inside nas_root
    summer_dir = temp_nas_dir / "Summer_Vacation"
    summer_dir.mkdir(parents=True)
    (summer_dir / "beach.txt").write_bytes(b"beach trip data")
    (summer_dir / "drone.txt").write_bytes(b"drone footage notes")

    taxes_dir = temp_nas_dir / "Taxes_2026"
    taxes_dir.mkdir(parents=True)
    (taxes_dir / "receipt.txt").write_bytes(b"expenses receipt")

    # System/hidden files to ignore
    git_dir = temp_nas_dir / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_bytes(b"git config")
    (temp_nas_dir / ".DS_Store").write_bytes(b"mac metadata")
    (temp_nas_dir / "temp.tmp").write_bytes(b"temp data")

    # Run sync
    sync_result = await nas_adapter.sync_directory()
    assert sync_result["indexed_files"] >= 3
    assert "Summer_Vacation" in sync_result["mapped_albums"]
    assert "Taxes_2026" in sync_result["mapped_albums"]

    # Verify SQLite records
    async with get_db() as db:
        # Check albums
        cur = await db.execute("SELECT name FROM albums WHERE name IN ('Summer_Vacation', 'Taxes_2026')")
        album_names = {r[0] for r in await cur.fetchall()}
        assert "Summer_Vacation" in album_names
        assert "Taxes_2026" in album_names

        # Check file_albums linking
        cur = await db.execute(
            """
            SELECT f.filename, a.name
            FROM files f
            JOIN file_albums fa ON f.id = fa.file_id
            JOIN albums a ON fa.album_id = a.id
            WHERE f.storage_backend = 'nas'
            """
        )
        linked = {r[0]: r[1] for r in await cur.fetchall()}
        assert linked.get("beach.txt") == "Summer_Vacation"
        assert linked.get("drone.txt") == "Summer_Vacation"
        assert linked.get("receipt.txt") == "Taxes_2026"

    # Verify atomic catalog snapshot .vayom/index.json exists
    snapshot_path = temp_nas_dir / ".vayom" / "index.json"
    assert snapshot_path.exists()


@pytest.mark.asyncio
async def test_nas_multi_device_snapshot_restore(nas_adapter, temp_nas_dir):
    """Validates multi-device catalog sync via .vayom/index.json snapshot."""
    await init_db()

    # Upload test file and save snapshot
    test_file = temp_nas_dir / "test_sync.txt"
    test_file.write_bytes(b"multi-device sync file")
    fid = f"sync-{uuid.uuid4().hex[:8]}"

    res = await nas_adapter.upload(test_file, file_id=fid, filename="test_sync.txt")
    async with get_db() as db:
        await db.execute(
            "INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, storage_backend) VALUES (?, ?, 'text/plain', 'document', 22, 'sha', 'nas')",
            (fid, "test_sync.txt")
        )
        await db.execute(
            "INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id, storage_backend, storage_uri) VALUES (?, ?, 0, 0, 22, 'sha', 0, 'nas', ?)",
            (str(uuid.uuid4()), fid, res["storage_uri"])
        )
        await db.commit()

    await nas_adapter.save_catalog_snapshot()

    # Now simulate a second device with a fresh DB
    async with get_db() as db:
        await db.execute("DELETE FROM file_parts WHERE file_id = ?", (fid,))
        await db.execute("DELETE FROM files WHERE id = ?", (fid,))
        await db.commit()

    # Restore from snapshot
    restored_count = await nas_adapter.load_catalog_snapshot()
    assert restored_count >= 1

    async with get_db() as db:
        cur = await db.execute("SELECT id, filename FROM files WHERE id = ?", (fid,))
        row = await cur.fetchone()
        assert row is not None
        assert row["filename"] == "test_sync.txt"


def test_api_upload_with_nas_storage_backend(nas_adapter, temp_nas_dir):
    """End-to-end integration test: upload to NAS via API, verify feed metadata and streaming."""
    client = TestClient(app)

    file_content = b"Integration test payload for NAS storage adapter 1234567890."

    # 1. Pro features are unlocked: upload to NAS via header succeeds directly
    response = client.post(
        "/api/v1/upload",
        files={"file": ("integration_nas.txt", file_content, "text/plain")},
        headers={"X-Storage-Backend": "nas"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert data["storage_backend"] == "nas"
    file_id = data["file_id"]

    # 2. Verify file in Unified Feed has storage_backend = 'nas'
    list_resp = client.get("/api/v1/files")
    assert list_resp.status_code == 200
    files_list = list_resp.json()
    matched = next((f for f in files_list if f["id"] == file_id), None)
    assert matched is not None
    assert matched["storage_backend"] == "nas"

    # 3. Verify metrics returns aggregate stats with NAS
    metrics_resp = client.get("/api/v1/system/metrics")
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()
    assert "backend_stats" in metrics
    assert "nas" in metrics["backend_stats"]

    # 4. Stream range from NAS file
    stream_resp = client.get(
        f"/api/v1/files/{file_id}/download",
        headers={"Range": "bytes=0-15"}
    )
    assert stream_resp.status_code == 206
    assert stream_resp.content == file_content[:16]


@pytest.mark.asyncio
async def test_nas_in_place_upload(nas_adapter, temp_nas_dir):
    """Validates in_place zero-copy upload leaves file in-place and returns correct uri."""
    await init_db()
    f = temp_nas_dir / "existing_video.mp4"
    f.write_bytes(b"existing video bytes for in_place test")

    res = await nas_adapter.upload(
        f,
        file_id="vid123",
        filename="custom_name.mp4",
        in_place=True
    )
    assert res["storage_uri"] == "existing_video.mp4"
    assert res["part_size_bytes"] == len(b"existing video bytes for in_place test")


@pytest.mark.asyncio
async def test_nas_sync_skips_unreadable_file(nas_adapter, temp_nas_dir):
    """I-40: one unreadable entry is collected and skipped, not fatal to the scan."""
    await init_db()
    (temp_nas_dir / "good.txt").write_bytes(b"good bytes")
    (temp_nas_dir / "bad.txt").write_bytes(b"bad bytes")

    real_sha = nas_module.calculate_sha256

    def flaky_sha(path):
        if str(path).endswith("bad.txt"):
            raise PermissionError("simulated unreadable file")
        return real_sha(path)

    with patch.object(nas_module, "calculate_sha256", side_effect=flaky_sha):
        result = await nas_adapter.sync_directory()

    assert result["indexed_files"] >= 1
    assert result["failed_files"] == 1
    assert result["errors"][0]["path"] == "bad.txt"
    assert "simulated unreadable file" in result["errors"][0]["error"]

    async with get_db() as db:
        cur = await db.execute(
            "SELECT filename FROM files WHERE storage_backend = 'nas'"
        )
        names = {row[0] for row in await cur.fetchall()}
    assert "good.txt" in names
    assert "bad.txt" not in names

