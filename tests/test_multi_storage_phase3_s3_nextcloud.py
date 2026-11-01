import pytest
import asyncio
import hashlib
import uuid
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx

from backend.db.connection import init_db, get_db
from backend.storage.adapters.s3 import S3StorageAdapter, sign_v4_headers
from backend.storage.adapters.nextcloud import NextcloudWebDAVAdapter


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


def test_s3_sigv4_signing():
    """Validates AWS SigV4 authorization header computation."""
    headers = {"Content-Type": "text/plain"}
    signed = sign_v4_headers(
        method="GET",
        url="https://s3.us-east-1.amazonaws.com/my-bucket/vayom_vault/test.txt",
        headers=headers,
        payload_hash="UNSIGNED-PAYLOAD",
        access_key="AKIAEXAMPLE12345678",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        service="s3",
    )
    assert "authorization" in signed
    auth = signed["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE12345678/")
    assert "SignedHeaders=" in auth
    assert "Signature=" in auth
    assert signed["x-amz-content-sha256"] == "UNSIGNED-PAYLOAD"


@pytest.mark.asyncio
async def test_s3_upload_native_unsplit(temp_dir):
    """Validates S3 adapter stores single continuous unsplit file (Constraint 1)."""
    await init_db()

    adapter = S3StorageAdapter(
        endpoint_url="https://s3.us-east-1.amazonaws.com",
        access_key_id="AKIA1111222233334444",
        secret_access_key="secret123",
        bucket_name="my-vault-bucket",
    )

    dummy_file = temp_dir / "sample.mp4"
    dummy_file.write_bytes(b"sample video bytes 1234567890")

    # Mock httpx PUT response
    async def mock_put(*args, **kwargs):
        return httpx.Response(200, text="")

    with patch.object(httpx.AsyncClient, "put", new=mock_put):
        res = await adapter.upload(
            local_path=dummy_file,
            file_id="s3-file-123",
            filename="sample.mp4",
            folder_name="Vacation_2026",
            mime_type="video/mp4",
        )

    # Constraint 1 assertions
    assert res["storage_backend"] == "s3"
    assert res["part_index"] == 0
    assert res["total_parts"] == 1
    assert res["byte_offset"] == 0
    assert res["part_size_bytes"] == len(b"sample video bytes 1234567890")
    assert res["storage_uri"] == "vayom_vault/Vacation_2026/sample.mp4"


@pytest.mark.asyncio
async def test_s3_secret_redaction_and_health_check():
    """Validates S3 health check redacts secret key (Constraint 6)."""
    adapter = S3StorageAdapter(
        endpoint_url="https://s3.amazonaws.com",
        access_key_id="AKIAVISIBLEKEY123",
        secret_access_key="SUPER_SECRET_KEY_NEVER_EXPOSE",
        bucket_name="vault-bucket",
    )

    async def mock_get(*args, **kwargs):
        return httpx.Response(200, text="<ListBucketResult></ListBucketResult>")

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        health = await adapter.health_check()

    assert health["status"] == "online"
    assert health["backend"] == "s3"
    assert "SUPER_SECRET_KEY_NEVER_EXPOSE" not in str(health)
    assert health["access_key_id"] == "AKIA***"


@pytest.mark.asyncio
async def test_nextcloud_webdav_mkcol_and_upload(temp_dir):
    """Validates Nextcloud WebDAV recursive MKCOL and async streaming upload (Constraint 1 & 3)."""
    await init_db()

    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="app_password_secret_123",
    )

    dummy_file = temp_dir / "document.pdf"
    dummy_file.write_bytes(b"Nextcloud PDF document bytes.")

    mkcol_calls = []
    put_calls = []

    async def mock_request(self, method, url, *args, **kwargs):
        if method == "MKCOL":
            mkcol_calls.append(url)
            return httpx.Response(201, text="")
        return httpx.Response(200, text="")

    async def mock_put(self, url, *args, **kwargs):
        put_calls.append(url)
        return httpx.Response(201, text="")

    with patch.object(httpx.AsyncClient, "request", new=mock_request), \
         patch.object(httpx.AsyncClient, "put", new=mock_put):
        res = await adapter.upload(
            local_path=dummy_file,
            file_id="nc-file-123",
            filename="document.pdf",
            folder_name="Finance/2026",
            mime_type="application/pdf",
        )

    # Constraint 3: MKCOL executed on parent paths
    assert len(mkcol_calls) >= 1
    assert any("Finance" in call for call in mkcol_calls)

    # Constraint 1: Unsplit native single file
    assert res["storage_backend"] == "nextcloud"
    assert res["part_index"] == 0
    assert res["total_parts"] == 1
    assert res["byte_offset"] == 0
    assert res["part_size_bytes"] == len(b"Nextcloud PDF document bytes.")
    assert res["storage_uri"] == "vayom_vault/Finance/2026/document.pdf"


@pytest.mark.asyncio
async def test_nextcloud_password_redaction_and_health():
    """Validates Nextcloud health check redacts password (Constraint 6)."""
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin_user",
        app_password="my_super_secret_app_password",
    )

    async def mock_request(self, method, url, *args, **kwargs):
        return httpx.Response(207, text="<d:multistatus></d:multistatus>")

    with patch.object(httpx.AsyncClient, "request", new=mock_request):
        health = await adapter.health_check()

    assert health["status"] == "online"
    assert health["backend"] == "nextcloud"
    assert "my_super_secret_app_password" not in str(health)
    assert health["app_password"] == "my_***"


@pytest.mark.asyncio
async def test_storage_config_endpoints():
    """Validates GET /storage/config, POST /storage/s3/config, POST /storage/nextcloud/config, and persistent loader."""
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.config import settings
    from backend.api.routes import load_persistent_storage_configs
    await init_db()

    client = TestClient(app)

    # 1. GET /api/v1/storage/config
    res_get = client.get("/api/v1/storage/config")
    assert res_get.status_code == 200
    cfg = res_get.json()
    assert "s3" in cfg and "nextcloud" in cfg and "nas" in cfg
    assert "secret_access_key" not in str(cfg)
    assert "app_password" not in str(cfg)

    # 2. POST /api/v1/storage/s3/config
    async def mock_s3_head(self, *args, **kwargs):
        return httpx.Response(200)

    with patch.object(httpx.AsyncClient, "head", new=mock_s3_head):
        res_s3 = client.post("/api/v1/storage/s3/config", json={
            "endpoint_url": "https://s3.us-west-2.amazonaws.com",
            "bucket_name": "test-auto-bucket",
            "access_key_id": "AKIA_TEST_KEY",
            "secret_access_key": "top_secret_s3_key_123",
            "region_name": "us-west-2",
            "vault_prefix": "vayom_vault"
        })
    assert res_s3.status_code == 200
    assert res_s3.json()["configured"] is True
    assert settings.S3_BUCKET_NAME == "test-auto-bucket"
    assert settings.S3_ACCESS_KEY_ID == "AKIA_TEST_KEY"

    # Verify secret is NOT returned in GET /api/v1/storage/config
    res_get2 = client.get("/api/v1/storage/config")
    cfg2 = res_get2.json()
    assert cfg2["s3"]["configured"] is True
    assert cfg2["s3"]["has_secret_key"] is True
    assert "top_secret_s3_key_123" not in str(cfg2)

    # 3. POST /api/v1/storage/nextcloud/config
    async def mock_nc_req(self, method, url, *args, **kwargs):
        return httpx.Response(207, text="<d:multistatus></d:multistatus>")

    with patch.object(httpx.AsyncClient, "request", new=mock_nc_req):
        res_nc = client.post("/api/v1/storage/nextcloud/config", json={
            "webdav_url": "https://cloud.example.org/remote.php/dav/files/tester/",
            "username": "tester",
            "app_password": "my_nc_app_password_999",
            "vault_prefix": "vayom_vault"
        })
    assert res_nc.status_code == 200
    assert res_nc.json()["configured"] is True
    assert settings.NEXTCLOUD_USERNAME == "tester"

    # Verify password is NOT returned in GET /api/v1/storage/config
    res_get3 = client.get("/api/v1/storage/config")
    cfg3 = res_get3.json()
    assert cfg3["nextcloud"]["configured"] is True
    assert cfg3["nextcloud"]["has_password"] is True
    assert "my_nc_app_password_999" not in str(cfg3)

    # 4. Verify persistent reload from SQLite
    settings.S3_BUCKET_NAME = ""
    settings.NEXTCLOUD_USERNAME = ""
    await load_persistent_storage_configs()
    assert settings.S3_BUCKET_NAME == "test-auto-bucket"
    assert settings.NEXTCLOUD_USERNAME == "tester"


def test_s3_object_key_encoding_and_traversal():
    """B-10: special characters are encoded and `..` cannot escape the prefix."""
    adapter = S3StorageAdapter(
        endpoint_url="https://s3.us-east-1.amazonaws.com",
        access_key_id="AKIA1111222233334444",
        secret_access_key="secret123",
        bucket_name="my-vault-bucket",
    )

    url = adapter._build_url("vayom_vault/a#b?.mp4")
    assert url == (
        "https://s3.us-east-1.amazonaws.com/my-vault-bucket/vayom_vault/a%23b%3F.mp4"
    )
    assert adapter._build_url("vayom_vault/a b.mp4").endswith("/vayom_vault/a%20b.mp4")

    with pytest.raises(ValueError):
        adapter._build_url("vayom_vault/../outside.mp4")


def test_nextcloud_object_key_encoding_and_traversal():
    """B-10: WebDAV URLs encode special characters and reject `..` escapes."""
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="app_password_secret_123",
    )

    assert adapter._build_url("vayom_vault/a#b?.pdf") == (
        "https://cloud.example.com/remote.php/dav/files/user/vayom_vault/a%23b%3F.pdf"
    )
    assert adapter._build_url("vayom_vault/a b.pdf").endswith("/vayom_vault/a%20b.pdf")

    with pytest.raises(ValueError):
        adapter._build_url("vayom_vault/../outside.pdf")


@pytest.mark.asyncio
async def test_s3_upload_encodes_special_filename(temp_dir):
    """B-10: the PUT URL is encoded while the stored logical URI stays readable."""
    await init_db()

    adapter = S3StorageAdapter(
        endpoint_url="https://s3.us-east-1.amazonaws.com",
        access_key_id="AKIA1111222233334444",
        secret_access_key="secret123",
        bucket_name="my-vault-bucket",
    )

    dummy_file = temp_dir / "sample.mp4"
    dummy_file.write_bytes(b"sample bytes")
    put_urls = []

    async def mock_put(self, url, *args, **kwargs):
        put_urls.append(str(url))
        return httpx.Response(200, text="")

    with patch.object(httpx.AsyncClient, "put", new=mock_put):
        res = await adapter.upload(
            local_path=dummy_file,
            file_id="s3-file-encoded",
            filename="a#b?.mp4",
            folder_name="Vacation_2026",
            mime_type="video/mp4",
        )

    assert put_urls[-1].endswith("/vayom_vault/Vacation_2026/a%23b%3F.mp4")
    assert res["storage_uri"] == "vayom_vault/Vacation_2026/a#b?.mp4"


@pytest.mark.asyncio
async def test_nextcloud_upload_encodes_special_filename(temp_dir):
    """B-10: WebDAV PUT/MKCOL URLs are encoded, stored URI stays logical."""
    await init_db()

    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="app_password_secret_123",
    )

    dummy_file = temp_dir / "document.pdf"
    dummy_file.write_bytes(b"Nextcloud PDF document bytes.")
    put_urls = []

    async def mock_request(self, method, url, *args, **kwargs):
        return httpx.Response(201, text="")

    async def mock_put(self, url, *args, **kwargs):
        put_urls.append(str(url))
        return httpx.Response(201, text="")

    with patch.object(httpx.AsyncClient, "request", new=mock_request), \
         patch.object(httpx.AsyncClient, "put", new=mock_put):
        res = await adapter.upload(
            local_path=dummy_file,
            file_id="nc-file-encoded",
            filename="a#b?.pdf",
            folder_name="Finance/2026",
            mime_type="application/pdf",
        )

    assert put_urls[-1].endswith("/vayom_vault/Finance/2026/a%23b%3F.pdf")
    assert res["storage_uri"] == "vayom_vault/Finance/2026/a#b?.pdf"


@pytest.mark.asyncio
async def test_s3_single_put_reads_payload_once(temp_dir, monkeypatch):
    """B-12: single-part PUT hashes and uploads one in-memory read."""
    adapter = S3StorageAdapter(
        endpoint_url="https://s3.us-east-1.amazonaws.com",
        access_key_id="AKIA1111222233334444",
        secret_access_key="secret123",
        bucket_name="my-vault-bucket",
    )

    payload = b"read-once-payload"
    dummy_file = temp_dir / "payload.bin"
    dummy_file.write_bytes(payload)

    import builtins
    real_open = builtins.open
    opens = []

    def counting_open(file, mode="r", *args, **kwargs):
        if str(file) == str(dummy_file):
            opens.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting_open)

    sent = {}

    async def mock_put(self, url, **kwargs):
        sent["content"] = kwargs.get("content")
        sent["headers"] = kwargs.get("headers")
        return httpx.Response(200, text="")

    with patch.object(httpx.AsyncClient, "put", new=mock_put):
        await adapter._upload_single(
            dummy_file, "vayom_vault/payload.bin", len(payload), "application/octet-stream"
        )

    assert len(opens) == 1
    assert sent["content"] == payload
    assert sent["headers"]["x-amz-content-sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_s3_range_request_requires_206():
    """B-14: a 200 response to a Range request must not be served as the range."""
    adapter = S3StorageAdapter(
        endpoint_url="https://s3.us-east-1.amazonaws.com",
        access_key_id="AKIA1111222233334444",
        secret_access_key="secret123",
        bucket_name="my-vault-bucket",
    )

    class FakeStream:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, *exc_info):
            return False

    def fake_stream(self, method, url, **kwargs):
        return FakeStream(httpx.Response(200, content=b"0123456789"))

    with patch.object(httpx.AsyncClient, "stream", new=fake_stream):
        with pytest.raises(RuntimeError, match="ignored Range"):
            async for _ in adapter.stream_bytes(
                "vayom_vault/file.bin", offset=5, limit=3
            ):
                pass

    def fake_stream_partial(self, method, url, **kwargs):
        return FakeStream(httpx.Response(206, content=b"567"))

    with patch.object(httpx.AsyncClient, "stream", new=fake_stream_partial):
        chunks = [
            chunk
            async for chunk in adapter.stream_bytes(
                "vayom_vault/file.bin", offset=5, limit=3
            )
        ]
    assert b"".join(chunks) == b"567"

