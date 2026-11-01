import os
import errno
import shutil
import asyncio
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest

from backend.db.connection import init_db
from backend.storage.adapters.nas import NASStorageAdapter
from backend.storage.adapters.s3 import S3StorageAdapter


@pytest.fixture
def temp_nas():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def s3_adapter():
    return S3StorageAdapter(
        endpoint_url="https://s3.us-east-1.amazonaws.com",
        access_key_id="TEST_KEY",
        secret_access_key="TEST_SECRET",
        bucket_name="test-bucket",
    )


@pytest.mark.asyncio
async def test_challenger_nas_stream_negative_offset_and_zero_limit(temp_nas):
    """Verifies NAS stream_bytes rejects negative offsets and safely exits on zero limits."""
    adapter = NASStorageAdapter(nas_root=temp_nas)
    test_file = temp_nas / "test.bin"
    test_file.write_bytes(b"0123456789" * 100)

    # 1. Negative offset must raise ValueError
    for neg_off in [-1, -50, -1000]:
        with pytest.raises(ValueError, match="Offset cannot be negative"):
            async for _ in adapter.stream_bytes("test.bin", offset=neg_off):
                pass

    # 2. Zero and negative limits must return 0 chunks without opening file
    for lim in [0, -1, -100]:
        chunks = [c async for c in adapter.stream_bytes("test.bin", offset=0, limit=lim)]
        assert len(chunks) == 0

        chunks = [c async for c in adapter.stream_bytes("test.bin", offset=50, limit=lim)]
        assert len(chunks) == 0


@pytest.mark.asyncio
async def test_challenger_s3_stream_negative_offset_and_zero_limit(s3_adapter):
    """Verifies S3 stream_bytes rejects negative offsets and handles zero limits without HTTP calls."""
    # 1. Negative offset must raise ValueError
    for neg_off in [-1, -100]:
        with pytest.raises(ValueError, match="Offset cannot be negative"):
            async for _ in s3_adapter.stream_bytes("key.bin", offset=neg_off):
                pass

    # 2. Zero/negative limit must yield empty generator without making HTTP requests
    network_called = False
    async def trap_client(*args, **kwargs):
        nonlocal network_called
        network_called = True
        return httpx.Response(200)

    with patch.object(httpx.AsyncClient, "stream", new=trap_client):
        for lim in [0, -1, -50]:
            chunks = [c async for c in s3_adapter.stream_bytes("key.bin", offset=0, limit=lim)]
            assert len(chunks) == 0
            assert not network_called, "S3 stream_bytes made network request on zero/negative limit"


@pytest.mark.asyncio
async def test_challenger_nas_stream_no_fd_leak_on_early_break_or_error(temp_nas):
    """Verifies NAS stream_bytes closes file handles properly on early exit or client exception."""
    adapter = NASStorageAdapter(nas_root=temp_nas)
    stream_file = temp_nas / "stream_target.bin"
    stream_file.write_bytes(b"A" * (1024 * 1024))

    def get_open_fds():
        fds = []
        fd_dir = "/proc/self/fd"
        for fd_name in os.listdir(fd_dir):
            try:
                target = os.readlink(os.path.join(fd_dir, fd_name))
                if str(temp_nas) in target:
                    fds.append((fd_name, target))
            except OSError:
                pass
        return fds

    # 20 early breaks
    for _ in range(20):
        async for _ in adapter.stream_bytes("stream_target.bin", chunk_size=64 * 1024):
            break
        await asyncio.sleep(0.005)
    assert len(get_open_fds()) == 0, f"Leaked FDs after early break: {get_open_fds()}"

    # 20 consumer exceptions
    for _ in range(20):
        try:
            async for chunk in adapter.stream_bytes("stream_target.bin", chunk_size=64 * 1024):
                raise RuntimeError("Consumer error")
        except RuntimeError:
            pass
        await asyncio.sleep(0.005)
    assert len(get_open_fds()) == 0, f"Leaked FDs after exception: {get_open_fds()}"


@pytest.mark.asyncio
async def test_challenger_nas_upload_tmp_cleanup_on_failure(temp_nas):
    """Verifies NAS upload unlinks temporary files when atomic write or replace fails."""
    adapter = NASStorageAdapter(nas_root=temp_nas)
    src = temp_nas / "source.bin"
    src.write_bytes(b"D" * (512 * 1024))

    # 1. Simulate ENOSPC on write
    real_open = open
    def faulty_open_write(path, mode="r", *args, **kwargs):
        if "w" in mode and ".tmp_" in str(path):
            f = real_open(path, mode, *args, **kwargs)
            orig_write = f.write
            def faulty_write(b):
                orig_write(b[:1024])
                raise OSError(errno.ENOSPC, "No space left on device")
            f.write = faulty_write
            return f
        return real_open(path, mode, *args, **kwargs)

    with patch("builtins.open", side_effect=faulty_open_write):
        with pytest.raises(OSError) as exc_info:
            await adapter.upload(src, file_id="err_enospc", filename="dest_enospc.bin")
        assert exc_info.value.errno == errno.ENOSPC

    assert len(list(temp_nas.glob(".tmp_*"))) == 0, "Orphaned tmp file remained after ENOSPC"
    assert not (temp_nas / "dest_enospc.bin").exists(), "Partial file remained after ENOSPC"

    # 2. Simulate OSError on replace
    with patch("os.replace", side_effect=OSError("Simulated rename error")):
        with pytest.raises(OSError):
            await adapter.upload(src, file_id="err_replace", filename="dest_replace.bin")

    assert len(list(temp_nas.glob(".tmp_*"))) == 0, "Orphaned tmp file remained after replace error"


@pytest.mark.asyncio
async def test_challenger_s3_upload_key_cleanup_on_failure(s3_adapter, temp_nas):
    """Verifies S3 active upload keys are cleaned up in finally block on upload failure."""
    src = temp_nas / "s3_sample.bin"
    src.write_bytes(b"sample")

    async def mock_put_fail(*args, **kwargs):
        raise httpx.ConnectError("Simulated S3 failure")

    with patch.object(httpx.AsyncClient, "put", new=mock_put_fail):
        with pytest.raises(httpx.ConnectError):
            await s3_adapter.upload(src, file_id="fail_f1", filename="doc.pdf")
        assert len(s3_adapter._active_upload_keys) == 0, f"Leaked key: {s3_adapter._active_upload_keys}"


@pytest.mark.asyncio
async def test_challenger_s3_stream_error_propagation_and_range_check(s3_adapter):
    """Verifies S3 stream_bytes raises RuntimeError on 404/500 and when server ignores Range (200)."""
    class MockStreamResp:
        def __init__(self, status):
            self.status_code = status
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def aiter_bytes(self, chunk_size=512*1024):
            yield b"data"

    with patch.object(httpx.AsyncClient, "stream", side_effect=lambda *a, **k: MockStreamResp(404)):
        with pytest.raises(RuntimeError, match="HTTP 404"):
            async for _ in s3_adapter.stream_bytes("doc.bin", offset=0, limit=10):
                pass

    with patch.object(httpx.AsyncClient, "stream", side_effect=lambda *a, **k: MockStreamResp(200)):
        with pytest.raises(RuntimeError, match="refusing to stream wrong bytes"):
            async for _ in s3_adapter.stream_bytes("doc.bin", offset=10, limit=10):
                pass


@pytest.mark.asyncio
async def test_challenger_nas_sequential_collision_loop(temp_nas):
    """Verifies NAS upload correctly loops through existing candidate filenames."""
    adapter = NASStorageAdapter(nas_root=temp_nas)
    src = temp_nas / "src.txt"
    src.write_bytes(b"content")

    # Pre-seed existing files
    (temp_nas / "item.txt").write_bytes(b"v0")
    (temp_nas / "item_f1.txt").write_bytes(b"v1")
    for i in range(1, 10):
        (temp_nas / f"item_f1_{i}.txt").write_bytes(f"v{i+1}".encode())

    res = await adapter.upload(src, file_id="f1", filename="item.txt")
    assert res["storage_uri"] == "item_f1_10.txt"


# =========================================================================
# HARDENING VERIFICATION TESTS (Remediated & Verified)
# =========================================================================

@pytest.mark.asyncio
async def test_challenger_nas_concurrent_upload_collision_clobber(temp_nas):
    """Reproduces silent data loss when multiple uploads of the same filename occur concurrently."""
    adapter = NASStorageAdapter(nas_root=temp_nas)
    src = temp_nas / "clash_src.txt"
    src.write_bytes(b"unique_data")

    # Launch 20 concurrent uploads of identical filename 'clash.txt' with distinct file_ids
    tasks = [
        adapter.upload(src, file_id=f"file_{i}", filename="clash.txt")
        for i in range(20)
    ]
    results = await asyncio.gather(*tasks)
    uris = [r["storage_uri"] for r in results]

    # Every concurrent upload MUST receive a unique storage URI without clobbering!
    assert len(uris) == len(set(uris)), (
        f"Data Loss Race Condition! Expected 20 unique uris, but got {len(set(uris))} unique uris: {uris}"
    )


@pytest.mark.asyncio
async def test_challenger_s3_large_file_fd_leak_on_aborted_upload(s3_adapter, temp_nas):
    """Reproduces permanent OS file descriptor leak when S3 chunked upload fails midway."""
    large_file = temp_nas / "large_payload.bin"
    large_file.write_bytes(b"L" * (17 * 1024 * 1024)) # 17 MB triggers chunked upload

    async def mock_put_fail_midway(url, content=None, headers=None, **kwargs):
        if hasattr(content, "__aiter__"):
            count = 0
            async for chunk in content:
                count += 1
                if count == 2:
                    raise httpx.RequestError("Connection reset during transfer")
        return httpx.Response(200)

    with patch.object(httpx.AsyncClient, "put", side_effect=mock_put_fail_midway):
        for _ in range(5):
            try:
                await s3_adapter.upload(large_file, file_id="s3_abort", filename="large_payload.bin")
            except httpx.RequestError:
                pass
            await asyncio.sleep(0.01)

    # Check for leaked file descriptors in /proc/self/fd
    fd_dir = "/proc/self/fd"
    leaked = []
    for fd in os.listdir(fd_dir):
        try:
            target = os.readlink(os.path.join(fd_dir, fd))
            if "large_payload.bin" in target:
                leaked.append(fd)
        except OSError:
            pass

    assert len(leaked) == 0, f"Leaked {len(leaked)} file descriptors for large_payload.bin: {leaked}"


@pytest.mark.asyncio
async def test_challenger_nas_save_catalog_snapshot_tmp_leak_on_failure(temp_nas):
    """Reproduces orphaned tmp file leak when catalog snapshot serialization fails."""
    adapter = NASStorageAdapter(nas_root=temp_nas)
    await init_db()

    with patch("json.dump", side_effect=OSError(errno.ENOSPC, "No space left on device")):
        with pytest.raises(OSError):
            await adapter.save_catalog_snapshot()

    vayom_dir = temp_nas / ".vayom"
    leaked = list(vayom_dir.glob("index.json.tmp.*"))
    assert len(leaked) == 0, f"Orphaned snapshot tmp files leaked: {leaked}"
