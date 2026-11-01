import os
import json
import time
import asyncio
import tempfile
import pytest
from pathlib import Path
from backend.config import settings
from backend.compression.splitter import BoundedFileSlice, split_file_if_needed
from backend.storage.client import storage_client
from backend.workstation.security import validate_watch_folder_path
from backend.api.routes import sync_master_catalog, perform_rebuild_index
from backend.db.connection import get_db, init_db


def test_hardening_1_aligned_split_boundaries():
    """
    Hardening 1: Standard (1,887,436,800 bytes) and Premium (3,879,731,200 bytes)
    boundaries must be exact multiples of 2 MiB, 512 KiB, and 4,096 bytes.
    """
    two_mib = 2 * 1024 * 1024
    chunk_512k = 512 * 1024
    sector_4k = 4096

    assert settings.STANDARD_PART_BYTES == 1887436800
    assert settings.PREMIUM_PART_BYTES == 3879731200

    for bound in (settings.STANDARD_PART_BYTES, settings.PREMIUM_PART_BYTES):
        assert bound % two_mib == 0, f"{bound} not multiple of 2 MiB"
        assert bound % chunk_512k == 0, f"{bound} not multiple of 512 KiB"
        assert bound % sector_4k == 0, f"{bound} not multiple of 4,096 bytes"


def test_hardening_2_zero_copy_virtual_slicing(tmp_path):
    """
    Hardening 2: BoundedFileSlice provides zero-copy virtual stream slicing
    without writing temporary .part files to disk.
    """
    dummy_file = tmp_path / "large_source.bin"
    # Create 100 KB test payload
    payload = os.urandom(100 * 1024)
    dummy_file.write_bytes(payload)

    # Split using max_part_bytes=25 KB without output_dir -> zero-copy BoundedFileSlice
    parts = split_file_if_needed(dummy_file, max_part_bytes=25 * 1024, output_dir=None)
    assert len(parts) == 4

    # Verify no .part files created on disk
    part_files_on_disk = list(tmp_path.glob("*.part*"))
    assert len(part_files_on_disk) == 0

    # Verify each part has a valid BoundedFileSlice
    assembled = bytearray()
    for idx, part in enumerate(parts):
        assert part["part_index"] == idx
        part_slice = part["slice"]
        assert isinstance(part_slice, BoundedFileSlice)
        assert part_slice.seekable() is True
        assert part_slice.readable() is True
        assert part_slice.writable() is False
        assert len(part_slice) == part["part_size_bytes"]

        # Read slice
        data = part_slice.read()
        assembled.extend(data)

    assert bytes(assembled) == payload


@pytest.mark.asyncio
async def test_hardening_3_catalog_max_message_id_and_delta_tail_sync(tmp_path):
    """
    Hardening 3: Master catalog stores max_message_id, and scan_channel_envelopes
    supports min_id for sub-second Delta Tail Sync.
    """
    await init_db()
    mock_dir = settings.DATA_DIR / "mock_blobs"
    mock_dir.mkdir(parents=True, exist_ok=True)

    # Write mock blobs: msg 100, 200, 300
    for mid in (100, 200, 300):
        (mock_dir / f"{mid}.bin").write_bytes(b"blob data")
        env = {
            "id": f"v_file_{mid}",
            "name": f"test_{mid}.txt",
            "part": 0,
            "total": 1,
            "offset": 0,
            "size": 9,
            "sha256": "fake_sha",
            "cat": "document",
            "mime": "text/plain",
            "nonce": None,
            "enc": "none"
        }
        (mock_dir / f"{mid}.caption").write_text(f"VAYOM:v1:{json.dumps(env)}", encoding="utf-8")

    # Query with min_id=200 -> only message 300 should be returned!
    delta_envelopes = await storage_client.scan_channel_envelopes(min_id=200)
    msg_ids = [e["telegram_message_id"] for e in delta_envelopes]
    assert 300 in msg_ids
    assert 100 not in msg_ids
    assert 200 not in msg_ids


@pytest.mark.asyncio
async def test_hardening_4_read_vs_write_flood_wait_isolation():
    """
    Hardening 4: Write flood-wait tracking does not freeze read or streaming requests.
    """
    # Simulate a write flood wait of 30 seconds
    storage_client.set_write_flood_wait(30)
    assert storage_client.write_flood_wait_seconds > 0
    assert storage_client.read_flood_wait_seconds == 0

    # _wait_if_read_flooded must return immediately without sleeping
    start = time.time()
    await storage_client._wait_if_read_flooded()
    elapsed = time.time() - start
    assert elapsed < 0.1, "Read request blocked on write flood wait!"

    # Clean up
    storage_client._write_flood_wait_until = 0.0
    storage_client._read_flood_wait_until = 0.0


def test_hardening_5_external_drive_sandbox_support(tmp_path):
    """
    Hardening 5: Watch folder sandbox allows legitimate external drive mounts
    (/Volumes/*, /media/*, /run/media/*, D:\\) while maintaining system blacklists.
    """
    # Create fake external drive folder on tmp_path
    ext_mount = tmp_path / "media" / "usb_drive" / "RAW_PHOTOS"
    ext_mount.mkdir(parents=True, exist_ok=True)

    # Should validate safely
    valid_path = validate_watch_folder_path(str(ext_mount))
    assert valid_path == ext_mount.resolve()

    # System roots must remain strictly rejected
    with pytest.raises(Exception):
        validate_watch_folder_path("/etc")

    with pytest.raises(Exception):
        validate_watch_folder_path(str(settings.DATA_DIR))
