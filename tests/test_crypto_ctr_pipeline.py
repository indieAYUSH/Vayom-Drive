import os
import io
import json
import pytest
import tempfile
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.config import settings
from backend.db.connection import get_db
from backend.crypto.cipher import (
    derive_key_pbkdf2,
    generate_file_nonce,
    build_ctr_counter,
    aes_ctr_encrypt,
    aes_ctr_decrypt,
    EncryptedFileSlice,
    compute_slice_sha256,
    vault_manager,
)
from backend.compression.splitter import BoundedFileSlice


@pytest.fixture(autouse=True)
def reset_vault():
    vault_manager.clear_key()
    yield
    vault_manager.clear_key()


@pytest.mark.asyncio
async def test_aes_ctr_length_preservation_and_arbitrary_offsets():
    """
    Verifies AES-256-CTR Invariant:
    1. len(ciphertext) == len(plaintext)
    2. Decryption at arbitrary unaligned byte offsets returns exact matching bytes
    """
    key = derive_key_pbkdf2("secure_master_password", b"test_salt_123456")
    nonce = generate_file_nonce()
    plaintext = b"Vayom Drive Zero-Knowledge Encryption for Photos & Docs! " * 30

    ciphertext = aes_ctr_encrypt(plaintext, key, nonce, aligned_byte_offset=0)
    assert len(ciphertext) == len(plaintext)
    assert ciphertext != plaintext

    decrypted = aes_ctr_decrypt(ciphertext, key, nonce, aligned_byte_offset=0)
    assert decrypted == plaintext

    # Test random arbitrary unaligned offset seeks
    for offset in [1, 7, 16, 25, 48, 99, 137]:
        sub_len = 35
        sub_plain = plaintext[offset:offset + sub_len]
        sub_cipher = ciphertext[offset:offset + sub_len]

        decrypted_slice = aes_ctr_decrypt(sub_cipher, key, nonce, aligned_byte_offset=offset)
        assert decrypted_slice == sub_plain, f"Decryption failed at offset {offset}"


@pytest.mark.asyncio
async def test_encrypted_file_slice_stream_wrapper():
    """
    Verifies EncryptedFileSlice wraps local disk files and BoundedFileSlice
    without allocating duplicate files on disk.
    """
    key = derive_key_pbkdf2("test_passphrase", b"vault_salt_test")
    nonce = generate_file_nonce()

    with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
        data = b"0123456789ABCDEF" * 200  # 3200 bytes
        tf.write(data)
        file_path = tf.name

    try:
        # Wrap the whole file
        enc_slice = EncryptedFileSlice(file_path, key, nonce)
        assert len(enc_slice) == len(data)
        assert enc_slice.size == len(data)
        assert enc_slice.seekable()
        assert enc_slice.readable()
        assert not enc_slice.writable()

        # Compute ciphertext SHA-256
        sha = compute_slice_sha256(enc_slice)
        assert len(sha) == 64
        assert enc_slice.tell() == 0

        # Read in 512-byte chunks and verify
        chunks = []
        while chunk := enc_slice.read(512):
            chunks.append(chunk)
        full_ciphertext = b"".join(chunks)
        assert len(full_ciphertext) == len(data)

        # Decrypt full ciphertext
        decrypted = aes_ctr_decrypt(full_ciphertext, key, nonce, 0)
        assert decrypted == data

        # Test wrapping a BoundedFileSlice
        bounded = BoundedFileSlice(file_path, byte_offset=128, slice_size=256)
        enc_bounded = EncryptedFileSlice(bounded, key, nonce)
        bounded_cipher = enc_bounded.read()
        assert len(bounded_cipher) == 256

        decrypted_bounded = aes_ctr_decrypt(bounded_cipher, key, nonce, aligned_byte_offset=128)
        assert decrypted_bounded == data[128:128 + 256]
    finally:
        os.unlink(file_path)


@pytest.mark.asyncio
async def test_targeted_encryption_rules():
    """
    Verifies:
    1. Videos (file_category == 'video') remain unencrypted (encryption == 'none')
    2. Photos and documents (category != 'video') are encrypted (encryption == 'ctr')
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Unlock vault
        unlock_res = await client.post("/api/v1/vault/unlock", json={"passphrase": "my_master_password"})
        assert unlock_res.status_code == 200
        assert unlock_res.json()["unlocked"] is True

        # Check vault status
        status_res = await client.get("/api/v1/vault/status")
        assert status_res.status_code == 200
        assert status_res.json()["unlocked"] is True

        # 1. Initialize video upload -> MUST remain 'none'
        video_init = await client.post(
            "/api/v1/upload/init",
            data={
                "filename": "camera_clip.mp4",
                "total_size": 1024 * 1024,
                "mime_type": "video/mp4",
                "total_parts": 1
            }
        )
        assert video_init.status_code == 200
        v_data = video_init.json()
        assert v_data["encryption"] == "none", "Videos must be unencrypted for native streaming"
        assert v_data["nonce"] is None

        # 2. Initialize photo upload -> MUST be encrypted 'ctr'
        photo_init = await client.post(
            "/api/v1/upload/init",
            data={
                "filename": "family_vacation.jpg",
                "total_size": 2048,
                "mime_type": "image/jpeg",
                "total_parts": 1
            }
        )
        assert photo_init.status_code == 200
        p_data = photo_init.json()
        assert p_data["encryption"] == "ctr", "Photos must be encrypted with AES-CTR"
        assert p_data["nonce"] is not None
        assert len(p_data["nonce"]) == 16

        # 3. Initialize document upload -> MUST be encrypted 'ctr'
        doc_init = await client.post(
            "/api/v1/upload/init",
            data={
                "filename": "tax_returns_2026.pdf",
                "total_size": 5000,
                "mime_type": "application/pdf",
                "total_parts": 1
            }
        )
        assert doc_init.status_code == 200
        d_data = doc_init.json()
        assert d_data["encryption"] == "ctr", "Documents must be encrypted with AES-CTR"
        assert d_data["nonce"] is not None


@pytest.mark.asyncio
async def test_end_to_end_encrypted_doc_upload_and_decrypted_download():
    """
    Tests full roundtrip:
    1. Upload encrypted document via /upload/chunk
    2. Download document via /files/{id}/download
    3. Verify downloaded bytes match original plaintext perfectly
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        passphrase = "super_secret_vault_pass"
        key = derive_key_pbkdf2(passphrase, getattr(settings, "VAULT_SALT", b"vayom_vault_salt_default"))
        vault_manager.set_key(key)

        original_pdf_bytes = b"%PDF-1.4 Confidential Contract Agreement Content \n" * 40
        file_size = len(original_pdf_bytes)
        file_nonce = generate_file_nonce()

        # Init upload
        init_res = await client.post(
            "/api/v1/upload/init",
            data={
                "filename": "confidential_agreement.pdf",
                "total_size": file_size,
                "mime_type": "application/pdf",
                "total_parts": 1,
                "nonce": file_nonce,
                "encryption": "ctr"
            }
        )
        assert init_res.status_code == 200
        file_id = init_res.json()["file_id"]

        # Upload chunk
        chunk_res = await client.post(
            "/api/v1/upload/chunk",
            data={
                "file_id": file_id,
                "part_index": 0,
                "total_parts": 1,
                "byte_offset": 0,
                "part_sha256": "",
                "filename": "confidential_agreement.pdf",
                "total_size": file_size,
                "nonce": file_nonce,
                "encryption": "ctr"
            },
            files={"chunk": ("part_0.bin", original_pdf_bytes, "application/octet-stream")}
        )
        assert chunk_res.status_code == 200

        # Verify DB has record with encryption == 'ctr' and matching nonce
        async with get_db() as db:
            cur = await db.execute("SELECT encryption, nonce FROM files WHERE id = ?", (file_id,))
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == "ctr"
            assert row[1] == file_nonce

        # Download file using active vault session key
        download_res = await client.get(f"/api/v1/files/{file_id}/download")
        assert download_res.status_code == 200
        assert download_res.content == original_pdf_bytes, "Downloaded content must match original plaintext"

        # Lock vault: downloading without key MUST return 423 Locked (C-9)
        vault_manager.clear_key()
        locked_download = await client.get(f"/api/v1/files/{file_id}/download")
        assert locked_download.status_code == 423
        assert "Vault is locked" in locked_download.json()["detail"]

        # Now supply key via x-decryption-key header -> MUST succeed
        header_download = await client.get(
            f"/api/v1/files/{file_id}/download",
            headers={"x-decryption-key": key.hex()}
        )
        assert header_download.status_code == 200
        assert header_download.content == original_pdf_bytes, "Header x-decryption-key must decrypt"


@pytest.mark.asyncio
async def test_vault_lock_unlock_api():
    """
    Verifies /vault/unlock, /vault/status, and /vault/lock endpoints.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Initially locked
        res1 = await client.get("/api/v1/vault/status")
        assert res1.json()["unlocked"] is False

        # Unlock with passphrase
        res2 = await client.post("/api/v1/vault/unlock", json={"passphrase": "vault_pass_123"})
        assert res2.status_code == 200
        assert res2.json()["unlocked"] is True

        # Status is now unlocked
        res3 = await client.get("/api/v1/vault/status")
        assert res3.json()["unlocked"] is True

        # Lock vault
        res4 = await client.post("/api/v1/vault/lock")
        assert res4.status_code == 200
        assert res4.json()["unlocked"] is False

        # Status is locked again
        res5 = await client.get("/api/v1/vault/status")
        assert res5.json()["unlocked"] is False


@pytest.mark.asyncio
async def test_watcher_ingestion_with_encryption():
    """
    Verifies that files ingested through workstation_watcher:
    - Encrypt non-video documents/photos with AES-256-CTR when vault is unlocked
    - Keep video files strictly unencrypted ('none')
    """
    import uuid
    from backend.workstation.watcher import workstation_watcher

    # Set active vault key
    key = derive_key_pbkdf2("watcher_vault_password", b"vault_salt_watcher_123")
    vault_manager.set_key(key)

    with tempfile.TemporaryDirectory() as tmpdir:
        folder_id = f"wf_{uuid.uuid4().hex[:12]}"
        async with get_db() as db:
            await db.execute(
                "INSERT INTO watch_folders (id, path, label, auto_delete_local, is_active) VALUES (?, ?, ?, 0, 1)",
                (folder_id, tmpdir, "Crypto Watcher Test")
            )
            await db.commit()

        # 1. Non-video file (Text/Doc)
        doc_file = Path(tmpdir) / "secret_doc.txt"
        doc_content = b"Top secret workstation document content here!"
        doc_file.write_bytes(doc_content)

        await workstation_watcher.process_file(folder_id, doc_file, auto_delete_local=False)

        # 2. Video file (Video clip)
        video_file = Path(tmpdir) / "sample_clip.mp4"
        video_content = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + (b"\x00" * 100)
        video_file.write_bytes(video_content)

        await workstation_watcher.process_file(folder_id, video_file, auto_delete_local=False)

        # Verify DB records
        async with get_db() as db:
            cur1 = await db.execute("SELECT id, encryption, nonce, file_category FROM files WHERE filename = ?", ("secret_doc.txt",))
            doc_row = await cur1.fetchone()
            assert doc_row is not None
            doc_id, doc_enc, doc_nonce, doc_cat = doc_row
            assert doc_enc == "ctr", "Document must be encrypted with CTR"
            assert doc_nonce is not None
            assert len(doc_nonce) == 16

            cur2 = await db.execute("SELECT id, encryption, nonce, file_category FROM files WHERE filename = ?", ("sample_clip.mp4",))
            vid_row = await cur2.fetchone()
            assert vid_row is not None
            vid_id, vid_enc, vid_nonce, vid_cat = vid_row
            assert vid_enc == "none", "Video must remain unencrypted"
            assert vid_nonce is None

        # Verify download decryption of the doc
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            dl_res = await client.get(f"/api/v1/files/{doc_id}/download")
            assert dl_res.status_code == 200
            assert dl_res.content == doc_content, "Downloaded doc must decrypt to original content"


@pytest.mark.asyncio
async def test_vault_setup_and_recovery_key():
    """
    Verifies:
    1. /vault/setup initializes vault and returns exportable 64-hex recovery key
    2. Writes data/.vault_key with 0600 permissions
    3. Reloads automatically after simulate restart via load_stored_key()
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Setup vault with master passphrase
        setup_res = await client.post("/api/v1/vault/setup", json={"passphrase": "master_backup_password", "remember_device": True})
        assert setup_res.status_code == 200
        data = setup_res.json()
        assert data["unlocked"] is True
        recovery_key = data["recovery_key"]
        assert len(recovery_key) == 64

        # Check status shows initialized and remembered
        status_res = await client.get("/api/v1/vault/status")
        assert status_res.status_code == 200
        s_data = status_res.json()
        assert s_data["is_initialized"] is True
        assert s_data["unlocked"] is True
        assert s_data["remember_device"] is True

        # Simulate node reboot: clear in-memory key
        vault_manager._active_key = None
        assert vault_manager._active_key is None

        # Watcher / node startup loads stored key
        assert vault_manager.load_stored_key() is True
        assert vault_manager.is_unlocked() is True

        # Lock vault: must purge .vault_key
        lock_res = await client.post("/api/v1/vault/lock")
        assert lock_res.status_code == 200
        assert vault_manager.is_unlocked() is False
        assert vault_manager._key_path().exists() is False


@pytest.mark.asyncio
async def test_simple_upload_encryption_choke_point():
    """
    Verifies simple XHR /upload:
    1. Documents/photos are encrypted when vault is unlocked
    2. Videos remain strictly unencrypted
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        key = derive_key_pbkdf2("simple_upload_pass", b"salt_simple_upload_123")
        vault_manager.set_key(key)

        # 1. Upload photo via simple /upload
        photo_bytes = b"EXIF_JPEG_IMAGE_CONTENT_SAMPLE" * 10
        up_photo = await client.post(
            "/api/v1/upload",
            files={"file": ("photo.jpg", photo_bytes, "image/jpeg")}
        )
        assert up_photo.status_code == 200
        p_id = up_photo.json()["id"]

        # 2. Upload video via simple /upload
        video_bytes = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + (b"\x00" * 50)
        up_video = await client.post(
            "/api/v1/upload",
            files={"file": ("video.mp4", video_bytes, "video/mp4")}
        )
        assert up_video.status_code == 200
        v_id = up_video.json()["id"]

        # Check DB records
        async with get_db() as db:
            cur1 = await db.execute("SELECT encryption, nonce FROM files WHERE id = ?", (p_id,))
            p_row = await cur1.fetchone()
            assert p_row[0] == "ctr", "Photo via simple /upload must be encrypted"
            assert p_row[1] is not None

            cur2 = await db.execute("SELECT encryption, nonce FROM files WHERE id = ?", (v_id,))
            v_row = await cur2.fetchone()
            assert v_row[0] == "none", "Video via simple /upload must remain unencrypted"
            assert v_row[1] is None

        # Download photo and verify clean decryption
        dl_photo = await client.get(f"/api/v1/files/{p_id}/download")
        assert dl_photo.status_code == 200
        assert dl_photo.content == photo_bytes, "Decrypted photo bytes must match original"


@pytest.mark.asyncio
async def test_multipart_encrypted_file_cross_boundary_decryption():
    """
    Verifies C-8 fix:
    Multi-part encrypted files (part index >= 1, byte_offset > 0)
    must decrypt using absolute file offset (p_start + chunk_aligned_offset).
    Verifies full file download, seeking within Part 1, and cross-boundary range read.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        key = derive_key_pbkdf2("multipart_crypto_pass", b"salt_multipart_test")
        vault_manager.set_key(key)

        part0_len = 524288  # 512 KiB aligned
        part1_len = 2048    # 2 KiB
        total_len = part0_len + part1_len

        # Deterministic plaintext pattern
        part0_plain = b"A" * part0_len
        part1_plain = b"B" * part1_len
        full_plain = part0_plain + part1_plain
        file_nonce = generate_file_nonce()

        # Part 0 encrypted from offset 0
        part0_cipher = aes_ctr_encrypt(part0_plain, key, file_nonce, aligned_byte_offset=0)
        # Part 1 encrypted from absolute offset part0_len
        part1_cipher = aes_ctr_encrypt(part1_plain, key, file_nonce, aligned_byte_offset=part0_len)

        # 1. Initialize upload for 2 parts
        init_res = await client.post(
            "/api/v1/upload/init",
            data={
                "filename": "big_archive.bin",
                "total_size": total_len,
                "mime_type": "application/octet-stream",
                "total_parts": 2,
                "nonce": file_nonce,
                "encryption": "ctr"
            }
        )
        assert init_res.status_code == 200
        file_id = init_res.json()["file_id"]

        # 2. Upload part 0 (plaintext chunk encrypted on-the-fly at offset 0)
        res_p0 = await client.post(
            "/api/v1/upload/chunk",
            data={
                "file_id": file_id,
                "part_index": 0,
                "total_parts": 2,
                "byte_offset": 0,
                "part_sha256": "",
                "filename": "big_archive.bin",
                "total_size": total_len,
                "nonce": file_nonce,
                "encryption": "ctr"
            },
            files={"chunk": ("part_0.bin", part0_plain, "application/octet-stream")}
        )
        assert res_p0.status_code == 200

        # 3. Upload part 1 (plaintext chunk encrypted on-the-fly at offset part0_len)
        res_p1 = await client.post(
            "/api/v1/upload/chunk",
            data={
                "file_id": file_id,
                "part_index": 1,
                "total_parts": 2,
                "byte_offset": part0_len,
                "part_sha256": "",
                "filename": "big_archive.bin",
                "total_size": total_len,
                "nonce": file_nonce,
                "encryption": "ctr"
            },
            files={"chunk": ("part_1.bin", part1_plain, "application/octet-stream")}
        )
        assert res_p1.status_code == 200

        # 4. Download full file -> verify all parts decrypt cleanly
        dl_full = await client.get(f"/api/v1/files/{file_id}/download")
        assert dl_full.status_code == 200
        assert len(dl_full.content) == total_len
        assert dl_full.content == full_plain, "Full multi-part file must decrypt to exact plaintext"

        # 5. Range seek squarely in Part 1 (offset 524288 to 524288 + 100)
        dl_part1_range = await client.get(
            f"/api/v1/files/{file_id}/download",
            headers={"Range": f"bytes={part0_len}-{part0_len + 99}"}
        )
        assert dl_part1_range.status_code == 206
        assert dl_part1_range.content == b"B" * 100, "Seeking into Part 1 must use absolute counter offset"

        # 6. Straddle range read crossing boundary (last 50 bytes of Part 0 + first 50 bytes of Part 1)
        straddle_start = part0_len - 50
        straddle_end = part0_len + 49
        dl_straddle = await client.get(
            f"/api/v1/files/{file_id}/download",
            headers={"Range": f"bytes={straddle_start}-{straddle_end}"}
        )
        assert dl_straddle.status_code == 206
        assert dl_straddle.content == (b"A" * 50) + (b"B" * 50), "Cross-boundary range read must decrypt both parts"


