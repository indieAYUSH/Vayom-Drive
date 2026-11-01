import os
import json
import pytest
import struct
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import app
from backend.config import settings, Settings
from backend.crypto.cipher import (
    generate_file_nonce,
    build_ctr_counter,
    derive_key_pbkdf2,
    aes_ctr_encrypt,
    aes_ctr_decrypt,
    aes_gcm_encrypt,
    aes_gcm_decrypt,
)
from backend.api.routes import generate_file_id, generate_part_id, perform_rebuild_index, sync_master_catalog
from backend.storage.client import storage_client
from backend.db.connection import get_db, init_db

import asyncio

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_test_db():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(init_db())
    yield

def test_alphanumeric_uuid_generation():
    """Validates that continuous v_ IDs have no hyphens and are search-token friendly."""
    file_id = generate_file_id()
    assert file_id.startswith("v_"), f"ID must start with 'v_', got {file_id}"
    assert "-" not in file_id, f"ID must not contain hyphens: {file_id}"
    assert len(file_id) == 34, f"ID length must be 34 characters (v_ + 32 hex), got {len(file_id)}"

    part_id = generate_part_id()
    assert part_id.startswith("vp_"), f"Part ID must start with 'vp_', got {part_id}"
    assert "-" not in part_id, f"Part ID must not contain hyphens: {part_id}"

def test_mtproto_chunk_alignment_and_zero_overhead():
    """
    Validates Section 0 Invariant 1 and Section 2 requirement:
    - MTProto 524,288-byte (512 KiB) alignment unit test.
    - Zero-overhead encryption: len(plaintext) === len(ciphertext).
    """
    CHUNK_SIZE = 524_288  # 512 KiB
    nonce_hex = generate_file_nonce()
    assert len(nonce_hex) == 16, "Nonce must be 16 hex characters (8 bytes)"

    key = derive_key_pbkdf2("test-vault-password", b"salt123456789012")
    plaintext = os.urandom(CHUNK_SIZE)

    # Encrypt
    ciphertext = aes_ctr_encrypt(plaintext, key, nonce_hex, aligned_byte_offset=0)
    assert len(ciphertext) == len(plaintext), f"Ciphertext size ({len(ciphertext)}) must equal plaintext size ({len(plaintext)})"
    assert ciphertext != plaintext

    # Decrypt
    decrypted = aes_ctr_decrypt(ciphertext, key, nonce_hex, aligned_byte_offset=0)
    assert decrypted == plaintext, "Decrypted bytes must match original plaintext"

def test_ctr_block_counter_math():
    """
    Validates Invariant 1 block counter math:
    - 8-byte file nonce in bytes 0..7.
    - 64-bit Big-Endian block index in bytes 8..15.
    - Raises ValueError on non-16-byte aligned offsets or invalid nonce length.
    """
    nonce_hex = "0123456789abcdef"
    nonce_bytes = bytes.fromhex(nonce_hex)

    # Offset 0 -> block index 0
    cb0 = build_ctr_counter(nonce_hex, 0)
    assert len(cb0) == 16
    assert cb0[:8] == nonce_bytes
    assert struct.unpack(">Q", cb0[8:])[0] == 0

    # Offset 524,288 -> block index 524288 // 16 = 32,768
    cb_512k = build_ctr_counter(nonce_hex, 524_288)
    assert cb_512k[:8] == nonce_bytes
    assert struct.unpack(">Q", cb_512k[8:])[0] == 32768

    # Offset 1,048,576 -> block index 1048576 // 16 = 65,536
    cb_1m = build_ctr_counter(nonce_hex, 1_048_576)
    assert cb_1m[:8] == nonce_bytes
    assert struct.unpack(">Q", cb_1m[8:])[0] == 65536

    # Offset not aligned to 16 bytes must raise ValueError
    with pytest.raises(ValueError, match="multiple of 16"):
        build_ctr_counter(nonce_hex, 15)

    with pytest.raises(ValueError, match="multiple of 16"):
        build_ctr_counter(nonce_hex, 4097)

    # Invalid nonce length must raise ValueError
    with pytest.raises(ValueError, match="16 hex characters"):
        build_ctr_counter("short", 0)

def test_random_access_stream_seeking():
    """
    Validates that any 512 KiB aligned chunk of an encrypted stream can be decrypted
    independently at random offsets without decrypting prior bytes.
    """
    key = os.urandom(32)
    nonce_hex = generate_file_nonce()
    CHUNK_SIZE = 524_288
    TOTAL_SIZE = CHUNK_SIZE * 3  # 1.5 MiB

    plaintext = os.urandom(TOTAL_SIZE)
    # Encrypt full stream from offset 0
    full_ciphertext = aes_ctr_encrypt(plaintext, key, nonce_hex, aligned_byte_offset=0)

    # Slice out chunk 2 (offset 1,048,576)
    offset = CHUNK_SIZE * 2
    chunk_cipher = full_ciphertext[offset:offset + CHUNK_SIZE]

    # Decrypt chunk 2 directly using offset 1,048,576
    chunk_decrypted = aes_ctr_decrypt(chunk_cipher, key, nonce_hex, aligned_byte_offset=offset)
    expected_plaintext_chunk = plaintext[offset:offset + CHUNK_SIZE]

    assert chunk_decrypted == expected_plaintext_chunk, "Direct seeking chunk decryption must match plaintext slice"

def test_aes_gcm_master_catalog_encryption():
    """Validates authenticated encryption for master catalog payloads."""
    key = os.urandom(32)
    catalog_data = json.dumps({"version": "1.0", "files_count": 50}).encode("utf-8")

    nonce, ciphertext, tag = aes_gcm_encrypt(catalog_data, key)
    assert len(nonce) == 12
    assert len(tag) == 16

    # Clean round-trip decryption
    decrypted = aes_gcm_decrypt(ciphertext, key, nonce, tag)
    assert decrypted == catalog_data

    # Tampering detection
    tampered_cipher = bytearray(ciphertext)
    tampered_cipher[0] ^= 0xFF
    with pytest.raises(Exception):
        aes_gcm_decrypt(bytes(tampered_cipher), key, nonce, tag)

@pytest.mark.asyncio
async def test_pinned_master_catalog_cycle():
    """Tests saving, pinning, and fast-boot retrieval of index.json catalog."""
    catalog_data = {
        "version": "1.0",
        "updated_at": "2026-10-17T23:00:00Z",
        "total_files": 1,
        "total_bytes": 1024,
        "files": [
            {
                "id": "v_testcatalogfile1234567890abcdef",
                "filename": "document.pdf",
                "mime_type": "application/pdf",
                "file_category": "document",
                "size_bytes": 1024,
                "sha256": "fakehash",
                "is_split": False,
                "total_parts": 1,
                "upload_status": "completed",
                "nonce": "1122334455667788",
                "encryption": "ctr",
                "parts": [
                    {
                        "part_index": 0,
                        "offset": 0,
                        "size": 1024,
                        "sha256": "fakehash",
                        "telegram_message_id": 999
                    }
                ]
            }
        ]
    }

    # Save and pin
    msg_id = await storage_client.save_and_pin_catalog(catalog_data)
    assert msg_id is not None

    # Retrieve pinned catalog
    retrieved = await storage_client.get_pinned_catalog()
    assert retrieved is not None
    assert retrieved["version"] == "1.0"
    assert len(retrieved["files"]) == 1
    assert retrieved["files"][0]["id"] == "v_testcatalogfile1234567890abcdef"

    # Test sub-second perform_rebuild_index fast path
    restored = await perform_rebuild_index()
    assert restored >= 1

    async with get_db() as db:
        cur = await db.execute("SELECT * FROM files WHERE id = 'v_testcatalogfile1234567890abcdef'")
        row = await cur.fetchone()
        assert row is not None
        assert row["filename"] == "document.pdf"
        assert row["nonce"] == "1122334455667788"

@pytest.mark.asyncio
async def test_incomplete_upload_reconciliation():
    """
    Tests detecting incomplete uploads:
    - Total parts = 3, uploaded parts = 1
    - Status flagged as 'interrupted'
    - Resumption status returns missing_parts [1, 2] and next_part 1
    - Clean up incomplete purge endpoint removes orphan parts
    """
    file_id = generate_file_id()
    nonce = generate_file_nonce()

    # 1. Initialize multi-part file in SQLite
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (
                id, filename, mime_type, file_category, size_bytes, sha256,
                is_split, total_parts, upload_status, nonce, encryption
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id, "interrupted_video.mp4", "video/mp4", "video", 1500000, "hash",
                1, 3, "uploading", nonce, "ctr"
            )
        )
        # Only commit part 0
        part_id = generate_part_id()
        await db.execute(
            """
            INSERT INTO file_parts (
                id, file_id, part_index, byte_offset, part_size_bytes,
                part_sha256, telegram_message_id, telegram_channel_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                part_id, file_id, 0, 0, 500000, "part0hash", 2001, "mock_channel"
            )
        )
        await db.commit()

    # 2. Check upload status
    res = client.get(f"/api/v1/files/{file_id}/upload-status")
    assert res.status_code == 200
    status_data = res.json()
    assert status_data["is_interrupted"] is True
    assert status_data["uploaded_count"] == 1
    assert status_data["total_parts"] == 3
    assert status_data["missing_parts"] == [1, 2]
    assert status_data["next_part"] == 1

    # 3. Check list_files flags it as is_interrupted
    list_res = client.get("/api/v1/files")
    assert list_res.status_code == 200
    files = list_res.json()
    interrupted_item = next((f for f in files if f["id"] == file_id), None)
    assert interrupted_item is not None
    assert interrupted_item["is_interrupted"] is True
    assert interrupted_item["uploaded_parts"] == 1

    # 4. Clean up incomplete upload
    del_res = client.delete(f"/api/v1/files/{file_id}/incomplete")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # 5. Verify file record is removed
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
        assert await cur.fetchone() is None

def test_upload_progress_tracker():
    """Validates live upload progress tracking via /api/v1/upload/{task_id}/progress."""
    task_id = "test_progress_task_999"

    # Query before upload begins -> unknown
    pre_res = client.get(f"/api/v1/upload/{task_id}/progress")
    assert pre_res.status_code == 200
    assert pre_res.json()["status"] == "unknown"

    # Perform upload with task_id
    payload = b"Live Telegram MTProto progress test payload " * 1000
    res = client.post(
        f"/api/v1/upload?task_id={task_id}",
        files={"file": ("progress_test.txt", payload, "text/plain")}
    )
    assert res.status_code == 200
    file_id = res.json()["file_id"]

    # Query after upload completes -> done with 100% and stage
    post_res = client.get(f"/api/v1/upload/{task_id}/progress")
    assert post_res.status_code == 200
    data = post_res.json()
    assert data["status"] == "done"
    assert data["progress_percent"] == 100
    assert data["uploaded_bytes"] == len(payload)
    assert data["total_bytes"] == len(payload)
    assert "stage" in data
    assert data["stage"] == "Upload completed successfully"


def test_max_part_bytes_resets_from_premium_to_default(tmp_path, monkeypatch):
    """
    F-2: MAX_PART_BYTES must never stick at the 4 GB premium value.
    Logging in with a non-premium / expired profile, or clearing auth, must
    fall back to the standard 1.9 GB slicing limit.
    """
    monkeypatch.setenv("MAX_PART_BYTES", "1887436800")
    s = Settings()
    cfg = tmp_path / "auth_config.json"
    s.auth_config_file = cfg

    # Premium login upgrades slicing to 3,879,731,200 (1850 * 2 MiB)
    cfg.write_text(json.dumps({"auth_mode": "qr", "user_profile": {"is_premium": True}}), encoding="utf-8")
    s.load_auth_config()
    assert s.MAX_PART_BYTES == 3879731200

    # Premium expired -> reset to the standard 1,887,436,800 limit
    cfg.write_text(json.dumps({"auth_mode": "qr", "user_profile": {"is_premium": False}}), encoding="utf-8")
    s.load_auth_config()
    assert s.MAX_PART_BYTES == 1887436800

    # Profile without an is_premium field -> also reset
    s.MAX_PART_BYTES = 3879731200
    cfg.write_text(json.dumps({"auth_mode": "bot", "user_profile": {}}), encoding="utf-8")
    s.load_auth_config()
    assert s.MAX_PART_BYTES == 1887436800

    # Logging out clears any premium slice limit as well
    s.MAX_PART_BYTES = 3879731200
    s.clear_auth_config()
    assert s.MAX_PART_BYTES == 1887436800


def test_splitter_multi_part_success_and_mkdir(tmp_path):
    """
    Tests that split_file_if_needed correctly slices files larger than max_part_bytes,
    automatically creates the destination directory if missing, and generates valid part manifests.
    """
    from backend.compression.splitter import split_file_if_needed

    src_file = tmp_path / "large_source.bin"
    # Create 250 KB file
    data = os.urandom(250 * 1024)
    src_file.write_bytes(data)

    out_dir = tmp_path / "nested" / "chunks"  # doesn't exist yet
    part_size = 100 * 1024  # 100 KB chunks -> 3 parts: 100 KB, 100 KB, 50 KB

    parts = split_file_if_needed(src_file, max_part_bytes=part_size, output_dir=out_dir)

    assert out_dir.exists()
    assert len(parts) == 3

    assert parts[0]["part_index"] == 0
    assert parts[0]["byte_offset"] == 0
    assert parts[0]["part_size_bytes"] == 100 * 1024

    assert parts[1]["part_index"] == 1
    assert parts[1]["byte_offset"] == 100 * 1024
    assert parts[1]["part_size_bytes"] == 100 * 1024

    assert parts[2]["part_index"] == 2
    assert parts[2]["byte_offset"] == 200 * 1024
    assert parts[2]["part_size_bytes"] == 50 * 1024

    # Reconstruct and verify bytes match exactly
    reconstructed = b"".join(Path(p["file_path"]).read_bytes() for p in parts)
    assert reconstructed == data


def test_splitter_truncation_abort_and_orphan_cleanup(tmp_path):
    """
    I-3: Verifies that if a source file is truncated mid-split:
    1. It raises IOError immediately with a clear message (no infinite loop).
    2. All created part files from previous chunks are cleaned up (no disk leaks).
    """
    from backend.compression.splitter import split_file_if_needed

    src_file = tmp_path / "shrinking_file.bin"
    src_file.write_bytes(os.urandom(300 * 1024))

    out_dir = tmp_path / "out_parts"
    out_dir.mkdir(parents=True, exist_ok=True)
    part_size = 100 * 1024  # Expected 3 parts: 100K, 100K, 100K

    # Simulate truncation mid-split:
    # We patch open or wrap src.read to truncate after reading the first part
    original_open = open
    read_counter = {"bytes_read": 0}

    class MockTruncatingSrc:
        def __init__(self, real_file):
            self._real = real_file

        def read(self, size=-1):
            # Read first part normally (up to 100 KB), then simulate writer truncation (empty read)
            if read_counter["bytes_read"] < 100 * 1024:
                chunk = self._real.read(size)
                read_counter["bytes_read"] += len(chunk)
                return chunk
            # Writer truncated file mid-split
            return b""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self._real.close()

    def mock_open_fn(path, mode="r", *args, **kwargs):
        if str(path) == str(src_file) and "rb" in mode:
            real_f = original_open(path, mode, *args, **kwargs)
            return MockTruncatingSrc(real_f)
        return original_open(path, mode, *args, **kwargs)

    with patch("builtins.open", side_effect=mock_open_fn):
        with pytest.raises(IOError) as exc_info:
            split_file_if_needed(src_file, max_part_bytes=part_size, output_dir=out_dir)

        assert "Unexpected EOF while splitting" in str(exc_info.value)
        assert "source truncated?" in str(exc_info.value)

    # Invariant: all created part files must have been unlinked (zero orphan files in out_dir)
    remaining_parts = list(out_dir.glob("*.part_*"))
    assert remaining_parts == [], f"Expected 0 orphaned part files, found: {remaining_parts}"



