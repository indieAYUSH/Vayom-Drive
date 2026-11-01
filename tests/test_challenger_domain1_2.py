import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from backend.db.connection import init_db
from backend.storage.adapters.nextcloud import NextcloudWebDAVAdapter
from backend.storage.adapters.telegram import TelegramStorageAdapter
from backend.storage.adapters.nas import NASStorageAdapter
from backend.storage.adapters.s3 import S3StorageAdapter
from backend.storage.client import TelegramStorageClient
from backend.storage.manager import (
    get_storage_adapter,
    stream_part_bytes_universal,
)


# Helper fake streaming context for httpx client.stream
class FakeStreamContext:
    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


# =========================================================================
# 1. Nextcloud Error Propagation (HTTP 404, 500, 403, 502, Range 200, etc.)
# =========================================================================

@pytest.mark.asyncio
async def test_nextcloud_stream_http_404_error_propagation():
    """Verify that HTTP 404 from Nextcloud WebDAV raises RuntimeError with status 404."""
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    def mock_stream(self, method, url, **kwargs):
        resp = httpx.Response(404, content=b"File not found on WebDAV")
        return FakeStreamContext(resp)

    with patch.object(httpx.AsyncClient, "stream", new=mock_stream):
        with pytest.raises(RuntimeError) as exc_info:
            async for _ in adapter.stream_bytes("vayom_vault/missing.mp4", offset=0, limit=1024):
                pass
        assert "404" in str(exc_info.value)
        assert "Nextcloud stream failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_nextcloud_stream_http_500_error_propagation():
    """Verify that HTTP 500 from Nextcloud WebDAV raises RuntimeError with status 500."""
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    def mock_stream(self, method, url, **kwargs):
        resp = httpx.Response(500, content=b"Internal WebDAV error")
        return FakeStreamContext(resp)

    with patch.object(httpx.AsyncClient, "stream", new=mock_stream):
        with pytest.raises(RuntimeError) as exc_info:
            async for _ in adapter.stream_bytes("vayom_vault/corrupt.bin", offset=0, limit=2048):
                pass
        assert "500" in str(exc_info.value)
        assert "Nextcloud stream failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_nextcloud_stream_http_403_and_502_error_propagation():
    """Verify that HTTP 403 Forbidden and 502 Bad Gateway raise RuntimeError."""
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    for code in (401, 403, 502, 503):
        def mock_stream(self, method, url, code=code, **kwargs):
            return FakeStreamContext(httpx.Response(code, content=b"Error payload"))

        with patch.object(httpx.AsyncClient, "stream", new=mock_stream):
            with pytest.raises(RuntimeError) as exc_info:
                async for _ in adapter.stream_bytes("vayom_vault/file.bin", offset=0, limit=100):
                    pass
            assert str(code) in str(exc_info.value)


@pytest.mark.asyncio
async def test_nextcloud_stream_range_200_rejection():
    """Verify that if Range is requested but Nextcloud returns HTTP 200, RuntimeError is raised."""
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    def mock_stream(self, method, url, **kwargs):
        # 200 returned instead of 206 Partial Content
        return FakeStreamContext(httpx.Response(200, content=b"full-file-content-not-requested-slice"))

    with patch.object(httpx.AsyncClient, "stream", new=mock_stream):
        # Case A: limit is specified
        with pytest.raises(RuntimeError) as exc_info_limit:
            async for _ in adapter.stream_bytes("vayom_vault/video.mp4", offset=0, limit=100):
                pass
        assert "ignored Range request" in str(exc_info_limit.value)

        # Case B: offset > 0 specified
        with pytest.raises(RuntimeError) as exc_info_offset:
            async for _ in adapter.stream_bytes("vayom_vault/video.mp4", offset=500, limit=None):
                pass
        assert "ignored Range request" in str(exc_info_offset.value)


@pytest.mark.asyncio
async def test_nextcloud_stream_range_206_success():
    """Verify that HTTP 206 Partial Content correctly streams requested bytes."""
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    def mock_stream(self, method, url, headers=None, **kwargs):
        assert headers is not None and "Range" in headers
        assert headers["Range"] == "bytes=10-29"
        return FakeStreamContext(httpx.Response(206, content=b"01234567890123456789"))

    with patch.object(httpx.AsyncClient, "stream", new=mock_stream):
        chunks = []
        async for chunk in adapter.stream_bytes("vayom_vault/video.mp4", offset=10, limit=20, chunk_size=10):
            chunks.append(chunk)
        assert b"".join(chunks) == b"01234567890123456789"


@pytest.mark.asyncio
async def test_nextcloud_stream_boundary_offsets_and_limits():
    """Verify offset < 0 raises ValueError and limit <= 0 returns empty generator without HTTP calls."""
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    # Negative offset must raise ValueError immediately
    with pytest.raises(ValueError, match="Offset cannot be negative"):
        async for _ in adapter.stream_bytes("test.bin", offset=-1):
            pass

    with pytest.raises(ValueError, match="Offset cannot be negative"):
        async for _ in adapter.stream_bytes("test.bin", offset=-500):
            pass

    # Zero limit must return empty immediately without calling HTTP client
    called = []
    def mock_stream_never(self, *args, **kwargs):
        called.append(True)
        return FakeStreamContext(httpx.Response(200, content=b"should not happen"))

    with patch.object(httpx.AsyncClient, "stream", new=mock_stream_never):
        chunks_zero = [c async for c in adapter.stream_bytes("test.bin", offset=0, limit=0)]
        assert len(chunks_zero) == 0
        chunks_neg = [c async for c in adapter.stream_bytes("test.bin", offset=10, limit=-5)]
        assert len(chunks_neg) == 0
        assert len(called) == 0, "HTTP client was called for limit <= 0!"


@pytest.mark.asyncio
async def test_nextcloud_upload_error_propagation_and_key_cleanup():
    """Verify Nextcloud upload raises on HTTP error and always purges active upload keys."""
    await init_db()
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    with tempfile.TemporaryDirectory() as td:
        dummy_file = Path(td) / "error_test.bin"
        dummy_file.write_bytes(b"test data 12345")

        async def mock_request(self, method, url, *args, **kwargs):
            return httpx.Response(201)

        async def mock_put_500(self, url, *args, **kwargs):
            return httpx.Response(500, text="Internal WebDAV failure")

        with patch.object(httpx.AsyncClient, "request", new=mock_request), \
             patch.object(httpx.AsyncClient, "put", new=mock_put_500):
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.upload(
                    local_path=dummy_file,
                    file_id="nc-err-1",
                    filename="error_test.bin",
                )
            assert "Nextcloud WebDAV PUT failed (500)" in str(exc_info.value)
            # Crucial check: verify in-flight upload key was cleaned up despite exception
            assert len(adapter._active_upload_keys) == 0


# =========================================================================
# 2. Nextcloud Non-Blocking Chunk Generator & Resource Lifecycle
# =========================================================================

@pytest.mark.asyncio
async def test_nextcloud_chunk_generator_non_blocking_event_loop():
    """
    Empirically verify that the chunk generator reads file data in worker threads
    (via asyncio.to_thread) and does not block the main event loop.
    A concurrent background task measures event loop tick latency.
    """
    await init_db()
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    with tempfile.TemporaryDirectory() as td:
        # Create a 4 MB test file (8 chunks of 512 KB)
        large_file = Path(td) / "large.bin"
        chunk_data = b"X" * (512 * 1024)
        large_file.write_bytes(chunk_data * 8)

        # Background heart-beat task on event loop measuring responsiveness
        ticks = 0
        running = True

        async def heartbeat():
            nonlocal ticks, running
            while running:
                ticks += 1
                await asyncio.sleep(0.005)

        heartbeat_task = asyncio.create_task(heartbeat())

        # Intercept PUT: consume the async generator chunk by chunk with brief async yields
        chunks_received = 0
        total_bytes = 0

        async def mock_put(self, url, content=None, **kwargs):
            nonlocal chunks_received, total_bytes
            async for chunk in content:
                chunks_received += 1
                total_bytes += len(chunk)
                # Yield control to event loop to simulate network transmit time
                await asyncio.sleep(0.01)
            return httpx.Response(201)

        async def mock_request(self, method, url, *args, **kwargs):
            return httpx.Response(201)

        try:
            with patch.object(httpx.AsyncClient, "request", new=mock_request), \
                 patch.object(httpx.AsyncClient, "put", new=mock_put):
                res = await adapter.upload(
                    local_path=large_file,
                    file_id="large-1",
                    filename="large.bin",
                )
            assert res["storage_backend"] == "nextcloud"
            assert chunks_received == 8
            assert total_bytes == 4 * 1024 * 1024
            # If the generator were blocking, heartbeat task would be starved
            assert ticks >= 5, f"Heartbeat was starved! Ticks recorded: {ticks}"
        finally:
            running = False
            await heartbeat_task


@pytest.mark.asyncio
async def test_nextcloud_chunk_generator_file_handle_closure_on_abort():
    """
    Empirically verify that if an upload is aborted prematurely (e.g. client disconnect
    or network error during PUT), the file handle opened by _chunk_generator is safely closed
    in the finally: block.
    """
    await init_db()
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    with tempfile.TemporaryDirectory() as td:
        test_file = Path(td) / "abort_test.bin"
        test_file.write_bytes(b"A" * (1024 * 1024))

        opened_files = []
        real_open = open

        def tracking_open(*args, **kwargs):
            f = real_open(*args, **kwargs)
            opened_files.append(f)
            return f

        async def mock_request(self, method, url, *args, **kwargs):
            return httpx.Response(201)

        # PUT consumes 1 chunk and then raises an unexpected connection exception
        async def mock_put_abort(self, url, content=None, **kwargs):
            async for _ in content:
                # Abort after first chunk
                raise ConnectionResetError("Simulated network drop midway")

        with patch("builtins.open", side_effect=tracking_open), \
             patch.object(httpx.AsyncClient, "request", new=mock_request), \
             patch.object(httpx.AsyncClient, "put", new=mock_put_abort):
            with pytest.raises(ConnectionResetError):
                await adapter.upload(
                    local_path=test_file,
                    file_id="abort-1",
                    filename="abort_test.bin",
                )

        # Check all opened file handles were cleanly closed
        assert len(opened_files) >= 1
        for f in opened_files:
            assert f.closed, "File handle was not closed on aborted upload!"
        # Check active key popped
        assert len(adapter._active_upload_keys) == 0


@pytest.mark.asyncio
async def test_nextcloud_chunk_generator_progress_callback_resilience():
    """Verify that exceptions inside progress_callback do not break the upload generator."""
    await init_db()
    adapter = NextcloudWebDAVAdapter(
        webdav_url="https://cloud.example.com/remote.php/dav/files/user",
        username="admin",
        app_password="secret_password",
    )

    with tempfile.TemporaryDirectory() as td:
        test_file = Path(td) / "prog.bin"
        test_file.write_bytes(b"Progress test content 12345")

        def failing_progress(sent, total):
            raise RuntimeError("Progress reporting crashed")

        async def mock_request(self, method, url, *args, **kwargs):
            return httpx.Response(201)

        async def mock_put(self, url, content=None, **kwargs):
            chunks = []
            async for c in content:
                chunks.append(c)
            assert b"".join(chunks) == b"Progress test content 12345"
            return httpx.Response(201)

        with patch.object(httpx.AsyncClient, "request", new=mock_request), \
             patch.object(httpx.AsyncClient, "put", new=mock_put):
            res = await adapter.upload(
                local_path=test_file,
                file_id="prog-1",
                filename="prog.bin",
                progress_callback=failing_progress,
            )
        assert res["part_size_bytes"] == len(b"Progress test content 12345")


# =========================================================================
# 3. Telegram Mock Flag Propagation
# =========================================================================

@pytest.mark.asyncio
async def test_telegram_mock_flag_propagation_matrix():
    """
    Verify TelegramStorageAdapter accurately propagates the 'mock' flag across all variants:
    - client returns mock=True -> adapter returns mock=True
    - client returns mock=False -> adapter returns mock=False
    - client returns no mock key -> adapter returns mock=False
    - client returns legacy int message_id -> adapter returns mock=False
    - client returns truthy/falsy values -> adapter returns strict bool
    """
    test_cases = [
        ({"message_id": 101, "channel_id": "-1001", "mock": True}, True),
        ({"message_id": 102, "channel_id": "-1001", "mock": False}, False),
        ({"message_id": 103, "channel_id": "-1001"}, False),
        (55555, False),
        ({"message_id": 104, "channel_id": "-1001", "mock": 1}, True),
        ({"message_id": 105, "channel_id": "-1001", "mock": 0}, False),
        ({"message_id": 106, "channel_id": "-1001", "mock": "true"}, True),
    ]

    with tempfile.TemporaryDirectory() as td:
        dummy = Path(td) / "tg.bin"
        dummy.write_bytes(b"telegram test data")

        for client_ret, expected_mock in test_cases:
            mock_client = MagicMock()
            mock_client.upload_part = AsyncMock(return_value=client_ret)
            adapter = TelegramStorageAdapter(client=mock_client)

            res = await adapter.upload(
                local_path=dummy,
                file_id="tg-f1",
                filename="tg.bin",
            )
            assert "mock" in res, f"Result missing 'mock' key for input {client_ret}"
            assert res["mock"] is expected_mock, (
                f"Expected mock={expected_mock} for client output {client_ret}, got {res['mock']}"
            )
            assert isinstance(res["mock"], bool)


@pytest.mark.asyncio
async def test_telegram_offline_mock_end_to_end():
    """
    Empirically verify that an unauthenticated / disconnected TelegramStorageClient
    enters offline mock mode and produces mock=True through TelegramStorageAdapter.
    """
    client = TelegramStorageClient()
    # Ensure disconnected
    client._is_connected = False
    client.client = None

    adapter = TelegramStorageAdapter(client=client)

    with tempfile.TemporaryDirectory() as td:
        dummy = Path(td) / "offline.bin"
        dummy.write_bytes(b"offline mock payload")

        res = await adapter.upload(
            local_path=dummy,
            file_id="off-1",
            filename="offline.bin",
        )

        assert res["storage_backend"] == "telegram"
        assert res["mock"] is True
        assert res["telegram_message_id"] > 0
        assert res["storage_uri"].startswith("tg://")


# =========================================================================
# 4. Universal Storage Manager Offset/Limit Validation & Dispatch
# =========================================================================

@pytest.mark.asyncio
async def test_storage_manager_negative_offset_validation():
    """Verify stream_part_bytes_universal raises ValueError on negative offset."""
    part = {
        "storage_backend": "telegram",
        "storage_uri": "tg://-1001/123",
        "telegram_message_id": 123,
    }

    with pytest.raises(ValueError, match="Offset cannot be negative: -1"):
        async for _ in stream_part_bytes_universal(part, offset=-1):
            pass

    with pytest.raises(ValueError, match="Offset cannot be negative: -9999"):
        async for _ in stream_part_bytes_universal(part, offset=-9999):
            pass


@pytest.mark.asyncio
async def test_storage_manager_non_positive_limit_validation():
    """Verify stream_part_bytes_universal returns immediately without calling adapter when limit <= 0."""
    called = []

    class SentinelAdapter:
        async def stream_bytes(self, *args, **kwargs):
            called.append(True)
            yield b"sentinel"

    part = {"storage_backend": "nas", "storage_uri": "test.txt"}

    with patch("backend.storage.manager.get_storage_adapter", return_value=SentinelAdapter()):
        # Limit 0
        chunks_zero = [c async for c in stream_part_bytes_universal(part, offset=0, limit=0)]
        assert len(chunks_zero) == 0

        # Limit negative
        chunks_neg = [c async for c in stream_part_bytes_universal(part, offset=0, limit=-10)]
        assert len(chunks_neg) == 0

        # Ensure adapter was never called
        assert len(called) == 0, "Adapter stream_bytes was invoked despite limit <= 0"


@pytest.mark.asyncio
async def test_storage_manager_universal_dispatch():
    """Verify stream_part_bytes_universal properly routes calls based on part['storage_backend']."""
    dispatched_to = []

    class MockAdapter:
        def __init__(self, name):
            self.name = name

        async def stream_bytes(self, storage_uri, offset=0, limit=None, **kwargs):
            dispatched_to.append((self.name, storage_uri, offset, limit))
            yield f"from_{self.name}".encode()

    with patch("backend.storage.manager.get_storage_adapter", side_effect=lambda b: MockAdapter(b)):
        # 1. Nextcloud part
        nc_part = {"storage_backend": "nextcloud", "storage_uri": "vayom_vault/doc.pdf"}
        chunks = [c async for c in stream_part_bytes_universal(nc_part, offset=10, limit=50)]
        assert chunks == [b"from_nextcloud"]

        # 2. S3 part
        s3_part = {"storage_backend": "s3", "storage_uri": "vayom_vault/video.mp4"}
        chunks = [c async for c in stream_part_bytes_universal(s3_part, offset=0, limit=100)]
        assert chunks == [b"from_s3"]

        # 3. Default fallback (missing storage_backend -> telegram)
        tg_part = {"storage_uri": "tg://1/2", "telegram_message_id": 2}
        chunks = [c async for c in stream_part_bytes_universal(tg_part, offset=5, limit=20)]
        assert chunks == [b"from_telegram"]

    assert dispatched_to == [
        ("nextcloud", "vayom_vault/doc.pdf", 10, 50),
        ("s3", "vayom_vault/video.mp4", 0, 100),
        ("telegram", "tg://1/2", 5, 20),
    ]


@pytest.mark.asyncio
async def test_cross_adapter_boundary_consistency():
    """
    Adversarial cross-check: verify that all four adapters (NAS, S3, Nextcloud, Telegram)
    consistently enforce the exact same boundary constraints on offset < 0 and limit <= 0.
    """
    adapters = [
        ("nas", NASStorageAdapter(nas_root="/tmp")),
        ("s3", S3StorageAdapter(endpoint_url="https://s3.example.com", access_key_id="K", secret_access_key="S", bucket_name="B")),
        ("nextcloud", NextcloudWebDAVAdapter(webdav_url="https://dav.example.com", username="U", app_password="P")),
        ("telegram", TelegramStorageAdapter(client=MagicMock())),
    ]

    for name, adapter in adapters:
        # Negative offset must raise ValueError
        with pytest.raises(ValueError, match="cannot be negative"):
            async for _ in adapter.stream_bytes("dummy_uri", offset=-1):
                pass

        # Zero limit must yield 0 chunks
        chunks_zero = [c async for c in adapter.stream_bytes("dummy_uri", offset=0, limit=0)]
        assert len(chunks_zero) == 0, f"Adapter {name} did not yield 0 chunks on limit=0"

        # Negative limit must yield 0 chunks
        chunks_neg = [c async for c in adapter.stream_bytes("dummy_uri", offset=0, limit=-5)]
        assert len(chunks_neg) == 0, f"Adapter {name} did not yield 0 chunks on limit=-5"
