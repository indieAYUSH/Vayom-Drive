"""Regression tests for the isolated security/video fixes.

Covers:
- C-01: chunk uploads must use the nonce/encryption persisted by /upload/init
  even when the client omits those fields.
- H-02: share-link expiry, password verification, and view counting.
- H-04: upload chunk file_id path traversal.
- M-04: malformed nonce rejection.
"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import Response

from backend.main import app
from backend.api import routes
from backend.config import settings
from backend.crypto.cipher import derive_key_pbkdf2, generate_file_nonce, vault_manager
from backend.db.connection import get_db


@pytest.fixture(autouse=True)
def reset_vault():
    vault_manager.clear_key()
    yield
    vault_manager.clear_key()


@pytest.mark.asyncio
async def test_chunk_upload_without_client_nonce_is_still_encrypted():
    """C-01: init decides CTR; the chunk endpoint must not downgrade to plaintext."""
    key = derive_key_pbkdf2("chunk_fix_pass", b"chunk_fix_salt_123")
    vault_manager.set_key(key)

    plaintext = b"%PDF-1.7 chunked upload without client nonce fields\n" * 20

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        init_res = await client.post(
            "/api/v1/upload/init",
            data={
                "filename": "silent_encrypt.pdf",
                "total_size": str(len(plaintext)),
                "mime_type": "application/pdf",
                "total_parts": "1",
            },
        )
        assert init_res.status_code == 200
        init_data = init_res.json()
        assert init_data["encryption"] == "ctr"
        assert init_data["nonce"]

        # Deliberately omit nonce and encryption, exactly like useUploadStore.ts.
        chunk_res = await client.post(
            "/api/v1/upload/chunk",
            data={
                "file_id": init_data["file_id"],
                "part_index": "0",
                "total_parts": "1",
                "byte_offset": "0",
                "part_sha256": "",
                "filename": "silent_encrypt.pdf",
                "total_size": str(len(plaintext)),
            },
            files={"chunk": ("part_0.bin", plaintext, "application/octet-stream")},
        )
        assert chunk_res.status_code == 200

        async with get_db() as db:
            cur = await db.execute(
                "SELECT encryption, nonce FROM files WHERE id = ?",
                (init_data["file_id"],),
            )
            row = await cur.fetchone()
            assert row[0] == "ctr"
            assert row[1] == init_data["nonce"]

        download_res = await client.get(f"/api/v1/files/{init_data['file_id']}/download")
        assert download_res.status_code == 200
        assert download_res.content == plaintext


@pytest.mark.asyncio
async def test_chunk_upload_rejects_traversal_file_id():
    """H-04: file_id is used in a path join and must be strictly validated."""
    payload = b"x" * 32
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        for bad_id in ("../escape", "v_" + "a" * 31 + "/", "/tmp/absolute"):
            res = await client.post(
                "/api/v1/upload/chunk",
                data={
                    "file_id": bad_id,
                    "part_index": "0",
                    "total_parts": "1",
                    "byte_offset": "0",
                    "part_sha256": "",
                    "filename": "payload.bin",
                    "total_size": str(len(payload)),
                },
                files={"chunk": ("part_0.bin", payload, "application/octet-stream")},
            )
            assert res.status_code == 400


@pytest.mark.asyncio
async def test_init_rejects_malformed_nonce():
    """M-04: nonces must be exactly 8 bytes of hexadecimal."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        res = await client.post(
            "/api/v1/upload/init",
            data={
                "filename": "doc.txt",
                "total_size": "10",
                "mime_type": "text/plain",
                "total_parts": "1",
                "nonce": "not-a-real-nonce",
                "encryption": "none",
            },
        )
        assert res.status_code == 400


@pytest.mark.asyncio
async def test_share_link_password_expiry_and_view_count(monkeypatch):
    """H-02: passwords are verified, expiry is enforced, and seeks do not count."""
    monkeypatch.setattr(settings, "ENABLE_SHARE_LINKS", True)

    file_id = f"v_{uuid.uuid4().hex}"
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (file_id, "shared.bin", "application/octet-stream", "other", 1024, "0" * 64),
        )
        await db.commit()

    async def fake_download(file_id, request, range_header=None, inline=False):
        return Response(content=b"shared", media_type="application/octet-stream")

    monkeypatch.setattr(routes, "download_file", fake_download)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        created = await client.post(
            "/api/v1/shares",
            json={
                "file_id": file_id,
                "hours": 1,
                "has_password": True,
                "password": "correct horse battery staple",
                "max_views": 3,
            },
        )
        assert created.status_code == 200
        token = created.json()["token"]

        unauthorized = await client.get(f"/api/v1/s/{token}")
        assert unauthorized.status_code == 401

        wrong = await client.get(f"/api/v1/s/{token}", params={"password": "nope"})
        assert wrong.status_code == 401

        # Two initial requests consume two views; the range seek consumes none.
        first = await client.get(f"/api/v1/s/{token}", params={"password": "correct horse battery staple"})
        assert first.status_code == 200
        second = await client.get(
            f"/api/v1/s/{token}",
            headers={"X-Share-Password": "correct horse battery staple"},
        )
        assert second.status_code == 200

        # A seek/range request must not consume another view.
        seek = await client.get(
            f"/api/v1/s/{token}",
            params={"password": "correct horse battery staple"},
            headers={"Range": "bytes=10-20"},
        )
        assert seek.status_code == 200

        # Third initial request consumes the final view.
        third = await client.get(
            f"/api/v1/s/{token}",
            params={"password": "correct horse battery staple"},
        )
        assert third.status_code == 200

        exhausted = await client.get(
            f"/api/v1/s/{token}",
            params={"password": "correct horse battery staple"},
        )
        assert exhausted.status_code == 410

        # Expired links are rejected even with the right password.
        async with get_db() as db:
            await db.execute(
                "UPDATE share_links SET expires_at = '2000-01-01 00:00:00', max_views = NULL WHERE token = ?",
                (token,),
            )
            await db.commit()

        expired = await client.get(
            f"/api/v1/s/{token}",
            params={"password": "correct horse battery staple"},
        )
        assert expired.status_code == 410


@pytest.mark.asyncio
async def test_system_version_and_optional_upgrade_policy():
    """Verify version endpoint returns required optional upgrade metadata."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        res = await client.get("/api/v1/system/version")
        assert res.status_code == 200
        data = res.json()
        assert data["version"] == "1.0.0"
        assert data["app"] == "Vayom Drive"
        assert data["optional_update_available"] is False
        assert data["force_upgrade"] is False
        assert data["message"] == "All updates are optional. You have full lifetime access."


@pytest.mark.asyncio
async def test_pro_license_endpoints_remain_unlocked():
    """Verify GET and POST license endpoints return unlocked Pro status with all 9 features."""
    transport = ASGITransport(app=app)
    expected_features = [
        "nas", "s3", "nextcloud", "multi_storage",
        "unlimited_vault", "transcoding", "proxies",
        "automation", "watch_folders"
    ]
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # GET /api/v1/license
        get_res = await client.get("/api/v1/license")
        assert get_res.status_code == 200
        status_data = get_res.json()
        assert status_data["is_pro"] is True
        assert status_data["tier"] == "pro"
        assert set(status_data["features"]) == set(expected_features)

        # POST /api/v1/license/activate
        act_res = await client.post("/api/v1/license/activate", json={"license_key": "VPRO-LIFETIME-ACCESS"})
        assert act_res.status_code == 200
        act_data = act_res.json()
        assert act_data["status"] == "activated"
        lic = act_data["license"]
        assert lic["is_pro"] is True
        assert lic["tier"] == "pro"
        assert set(lic["features"]) == set(expected_features)


@pytest.mark.asyncio
async def test_vault_unlock_error_sanitization():
    """Verify vault unlock error responses are sanitized without leaking paths or traces."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Invalid / missing both passphrase and recovery key
        res_empty = await client.post("/api/v1/vault/unlock", json={})
        assert res_empty.status_code == 400
        assert "passphrase or recovery key" in res_empty.json()["detail"].lower()

        # Malformed recovery key (bad hex / bad length)
        res_bad_key = await client.post("/api/v1/vault/unlock", json={"key": "not-valid-hex-64-chars"})
        assert res_bad_key.status_code == 400
        detail = res_bad_key.json()["detail"]
        assert "Recovery key must be 64 hexadecimal characters" in detail or "expected 64" in detail
        assert "Traceback" not in detail
        assert "fromhex" not in detail
        assert "/" not in detail
