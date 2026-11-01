import asyncio
import ipaddress
import struct
import time
import pytest
from typing import AsyncIterator, Dict, Any, List
from fastapi import Request, HTTPException, status
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.db.connection import get_db
from backend.streaming.router import verify_loopback_security
from backend.streaming.engine import (
    StreamingEngine,
    MP4MoovResolver,
    LRUChunkCache,
    RangeSpec,
    parse_and_clamp_range,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_206_BYTES,
)


# =========================================================================
# Mock Storage Client for In-Memory Stress Testing
# =========================================================================

class AdversarialStorageClient:
    def __init__(self, data_by_id: Dict[Any, bytes]):
        self.data_by_id = data_by_id
        self.call_log: List[Dict[str, Any]] = []
        self.active_calls = 0
        self.max_concurrent_calls = 0
        self.call_count = 0
        self.delay = 0.0

    async def stream_part_bytes(
        self,
        message_id: int = 0,
        channel_id: Any = None,
        file_id: Any = None,
        access_hash: Any = None,
        offset: int = 0,
        limit: int = None,
        chunk_size: int = 64 * 1024,
        **kwargs
    ) -> AsyncIterator[bytes]:
        self.call_count += 1
        self.active_calls += 1
        if self.active_calls > self.max_concurrent_calls:
            self.max_concurrent_calls = self.active_calls

        lookup_key = message_id if message_id else (file_id if file_id is not None else kwargs.get("storage_uri"))
        if lookup_key in self.data_by_id:
            data = self.data_by_id[lookup_key]
        elif len(self.data_by_id) == 1:
            data = next(iter(self.data_by_id.values()))
        else:
            data = b""
        total_len = len(data)

        if self.delay > 0:
            await asyncio.sleep(self.delay)

        self.call_log.append({"key": lookup_key, "offset": offset, "limit": limit})

        try:
            if offset >= total_len:
                return

            end = total_len if limit is None else min(total_len, offset + limit)
            curr = offset
            while curr < end:
                block_len = min(chunk_size, end - curr)
                yield data[curr:curr + block_len]
                curr += block_len
        finally:
            self.active_calls -= 1


def build_mp4_box(atom_type: str, payload: bytes) -> bytes:
    """Builds a standard 32-bit MP4 box."""
    return struct.pack(">I", 8 + len(payload)) + atom_type.encode("ascii") + payload


# =========================================================================
# 1. Loopback Spoofing & Network Security Challenges
# =========================================================================

def test_loopback_remote_client_ip_with_spoofed_host_rejected():
    """Remote IP sending Host: 127.0.0.1 must be rejected with 403 Forbidden."""
    for remote_ip in ["192.168.1.100", "10.0.0.5", "172.16.0.1", "8.8.8.8", "169.254.1.1"]:
        req = Request({
            "type": "http",
            "client": (remote_ip, 5000),
            "headers": [(b"host", b"127.0.0.1:8000")]
        })
        with pytest.raises(HTTPException) as exc_info:
            verify_loopback_security(req)
        assert exc_info.value.status_code == 403
        assert "non-loopback" in exc_info.value.detail.lower()


def test_loopback_spoofed_forwarded_headers_ignored():
    """X-Forwarded-For and X-Real-IP spoofed headers must be ignored when client IP is remote."""
    req = Request({
        "type": "http",
        "client": ("192.168.1.200", 5000),
        "headers": [
            (b"host", b"127.0.0.1"),
            (b"x-forwarded-for", b"127.0.0.1"),
            (b"x-real-ip", b"127.0.0.1"),
            (b"forwarded", b"for=127.0.0.1")
        ]
    })
    with pytest.raises(HTTPException) as exc_info:
        verify_loopback_security(req)
    assert exc_info.value.status_code == 403


def test_loopback_zero_ip_rejected():
    """0.0.0.0 is non-routable / wildcard, NOT loopback."""
    # Client IP 0.0.0.0
    req_client = Request({
        "type": "http",
        "client": ("0.0.0.0", 5000),
        "headers": [(b"host", b"127.0.0.1")]
    })
    with pytest.raises(HTTPException) as exc:
        verify_loopback_security(req_client)
    assert exc.value.status_code == 403

    # Host 0.0.0.0
    req_host = Request({
        "type": "http",
        "client": ("127.0.0.1", 5000),
        "headers": [(b"host", b"0.0.0.0:8000")]
    })
    with pytest.raises(HTTPException) as exc:
        verify_loopback_security(req_host)
    assert exc.value.status_code == 403


def test_loopback_token_auth_matrix():
    """When STREAM_LOOPBACK_TOKEN is set, requests without or with wrong token must fail with 403."""
    orig = getattr(settings, "STREAM_LOOPBACK_TOKEN", "")
    settings.STREAM_LOOPBACK_TOKEN = "super_secret_session_token_xyz"
    try:
        req = Request({
            "type": "http",
            "client": ("127.0.0.1", 5000),
            "headers": [(b"host", b"127.0.0.1:8000")]
        })
        # Missing token
        with pytest.raises(HTTPException) as exc:
            verify_loopback_security(req, token=None)
        assert exc.value.status_code == 403

        # Wrong token
        with pytest.raises(HTTPException) as exc:
            verify_loopback_security(req, token="wrong_token")
        assert exc.value.status_code == 403

        # Correct token
        verify_loopback_security(req, token="super_secret_session_token_xyz")
    finally:
        settings.STREAM_LOOPBACK_TOKEN = orig


# Finding A fixed: loopback hosts are validated by IP parse, so DNS-rebinding
# names like 127.evil.com are rejected.
def test_loopback_dns_rebinding_127_subdomain_rejected():
    """
    Adversarial Challenge: Host header '127.evil.com' resolves to 127.0.0.1.
    verify_loopback_security must reject '127.evil.com' instead of accepting it via .startswith('127.').
    """
    req = Request({
        "type": "http",
        "client": ("127.0.0.1", 5000),
        "headers": [(b"host", b"127.evil.com")]
    })
    with pytest.raises(HTTPException) as exc:
        verify_loopback_security(req)
    assert exc.value.status_code == 403


# Finding B fixed (B16): bracket-aware Host parsing recognizes [::1]:8000.
def test_loopback_ipv6_bracketed_host_allowed():
    """
    Adversarial Challenge: RFC 7230 bracketed IPv6 loopback Host: [::1]:8000 or [::1]
    must be recognized as loopback and allowed.
    """
    req = Request({
        "type": "http",
        "client": ("::1", 5000),
        "headers": [(b"host", b"[::1]:8000")]
    })
    # Should not raise 403
    verify_loopback_security(req)


# Finding C fixed: faststart moov boxes with impossible sizes (< 8 or
# overflowing total_size) are no longer classified as valid.
@pytest.mark.asyncio
async def test_mp4_faststart_corrupted_box_size_rejected():
    """
    Adversarial Challenge: A file starting with a fake moov box claiming size < 8 or size > total_size
    must NOT be classified as valid faststart MP4.
    """
    # 1. Fake moov with size = 4
    corrupt_4 = b"\x00\x00\x00\x04moov" + b"\x00" * 100
    storage = AdversarialStorageClient({1: corrupt_4})
    parts = [{"byte_offset": 0, "part_size_bytes": len(corrupt_4), "telegram_message_id": 1}]
    res = await MP4MoovResolver.inspect_mp4(parts, len(corrupt_4), storage)
    assert res.get("has_moov") is False

    # 2. Fake moov with size = 100,000,000 on 100-byte file
    corrupt_huge = struct.pack(">I", 100000000) + b"moov" + b"\x00" * 92
    storage_huge = AdversarialStorageClient({2: corrupt_huge})
    parts_huge = [{"byte_offset": 0, "part_size_bytes": len(corrupt_huge), "telegram_message_id": 2}]
    res_huge = await MP4MoovResolver.inspect_mp4(parts_huge, len(corrupt_huge), storage_huge)
    assert res_huge.get("has_moov") is False


# =========================================================================
# 2. MP4 Moov Resolver False Positive & Attack Challenges
# =========================================================================

@pytest.mark.asyncio
async def test_mp4_resolver_trailing_with_mdat_fake_moov_flood():
    """
    Stress-test trailing moov detection when mdat is packed with multiple
    fake moov sequences having invalid sizes, truncated lengths, and impossible offsets.
    The resolver must cleanly bypass them and locate the real trailing moov atom.
    """
    ftyp = build_mp4_box("ftyp", b"isom\x00\x00\x02\x00mp41")

    # Construct mdat with multiple fake moov sequences:
    # 1. fake moov with size = 4 (illegal size)
    fake_1 = b"\x00\x00\x00\x04moov"
    # 2. fake moov with size = 0xFFFFFFFF (exceeds file)
    fake_2 = b"\xFF\xFF\xFF\xFFmoov" + b"\x00" * 32
    # 3. fake moov with size extending past EOF
    fake_3 = struct.pack(">I", 999999) + b"moov" + b"\x11" * 16

    # 80KB mdat payload to exceed 64KB head probe
    mdat_payload = b"\xAA" * 20000 + fake_1 + b"\xBB" * 20000 + fake_2 + b"\xCC" * 20000 + fake_3 + b"\xDD" * 20000
    mdat = build_mp4_box("mdat", mdat_payload)

    real_moov_payload = b"mvhd\x00\x00\x00\x00" + b"REAL_METADATA_TRAK_INDEX_999"
    real_moov = build_mp4_box("moov", real_moov_payload)

    file_bytes = ftyp + mdat + real_moov
    total_sz = len(file_bytes)

    storage = AdversarialStorageClient({1: file_bytes})
    parts = [{"byte_offset": 0, "part_size_bytes": total_sz, "telegram_message_id": 1}]

    res = await MP4MoovResolver.inspect_mp4(parts, total_sz, storage)

    assert res["is_mp4"] is True
    assert res["has_moov"] is True
    assert res["trailing_moov"] is True
    assert res["moov_offset"] == len(ftyp) + len(mdat)
    assert res["moov_size"] == len(real_moov)


@pytest.mark.asyncio
async def test_mp4_resolver_no_moov_file_with_random_moov_bytes():
    """A file with no genuine moov atom (only noise with 'moov' pattern) must report has_moov=False."""
    ftyp = build_mp4_box("ftyp", b"isom")
    fake_payload = (b"\x00\x00\x00\x02moov" + b"\x55" * 100) * 100
    mdat = build_mp4_box("mdat", fake_payload)
    file_bytes = ftyp + mdat
    total_sz = len(file_bytes)

    storage = AdversarialStorageClient({1: file_bytes})
    parts = [{"byte_offset": 0, "part_size_bytes": total_sz, "telegram_message_id": 1}]

    res = await MP4MoovResolver.inspect_mp4(parts, total_sz, storage)
    assert res["is_mp4"] is True
    assert res["has_moov"] is False
    assert res["faststart"] is False


@pytest.mark.asyncio
async def test_mp4_resolver_empty_and_corrupt_parts():
    """Test resilience against empty parts list and near-zero file sizes."""
    storage = AdversarialStorageClient({})

    # Empty parts
    res_empty = await MP4MoovResolver.inspect_mp4([], 1000, storage)
    assert res_empty["has_moov"] is False
    assert res_empty["is_mp4"] is False

    # Sub-16 bytes file
    res_small = await MP4MoovResolver.inspect_mp4([{"byte_offset": 0, "part_size_bytes": 8}], 8, storage)
    assert res_small["has_moov"] is False
    assert res_small["is_mp4"] is False


# =========================================================================
# 3. Prefetch Concurrency Limits & Seek Cancellation
# =========================================================================

@pytest.mark.asyncio
async def test_prefetch_concurrency_bounded_to_max():
    """
    Streaming through a multi-chunk file must never spawn more than max_prefetch_tasks (4).
    """
    chunk_sz = 1024
    total_chunks = 16
    total_sz = chunk_sz * total_chunks
    file_bytes = bytes(i % 256 for i in range(total_sz))

    storage = AdversarialStorageClient({1001: file_bytes})
    storage.delay = 0.05  # Add latency to let prefetch queue build up

    parts = [{
        "file_id": "file-prefetch-test",
        "part_index": 0,
        "byte_offset": 0,
        "part_size_bytes": total_sz,
        "telegram_message_id": 1001
    }]

    engine = StreamingEngine(chunk_size=chunk_sz)
    engine.max_prefetch_tasks = 4

    spec = RangeSpec(0, total_sz - 1, total_sz, total_sz, True, True)

    observed_active_tasks = []
    async for chunk in engine.stream_range(parts, total_sz, spec, storage):
        active_count = len(engine._active_prefetch_tasks)
        observed_active_tasks.append(active_count)
        # Yield to let prefetch tasks schedule
        await asyncio.sleep(0.01)

    # Invariant: active prefetch tasks must NEVER exceed max_prefetch_tasks (4)
    assert max(observed_active_tasks) <= 4, f"Observed peak prefetch tasks {max(observed_active_tasks)} > 4"


@pytest.mark.asyncio
async def test_seek_cancels_prior_prefetch_tasks():
    """
    When a seek occurs (new stream_range call on same file), prior active prefetch tasks
    for that file must be immediately cancelled.
    """
    chunk_sz = 1024
    total_sz = chunk_sz * 20
    file_bytes = b"X" * total_sz

    storage = AdversarialStorageClient({2001: file_bytes})
    storage.delay = 0.2  # Keep prefetch tasks busy

    parts = [{
        "file_id": "file-seek-test",
        "part_index": 0,
        "byte_offset": 0,
        "part_size_bytes": total_sz,
        "telegram_message_id": 2001
    }]

    engine = StreamingEngine(chunk_size=chunk_sz)

    # 1. Start Stream 1: read first chunk to trigger lookahead prefetch
    spec1 = RangeSpec(0, chunk_sz - 1, total_sz, chunk_sz, True, True)
    stream1 = engine.stream_range(parts, total_sz, spec1, storage)
    chunk1 = await anext(stream1)
    assert len(chunk1) == chunk_sz

    # Yield control so lookahead tasks are created
    await asyncio.sleep(0.02)
    assert len(engine._active_prefetch_by_file.get("file-seek-test", set())) > 0
    stream1_tasks = list(engine._active_prefetch_by_file["file-seek-test"])

    # 2. Seek occurs: Stream 2 initiated for new offset on the same file
    spec2 = RangeSpec(chunk_sz * 10, chunk_sz * 11 - 1, total_sz, chunk_sz, True, True)
    stream2 = engine.stream_range(parts, total_sz, spec2, storage)

    # Calling anext on stream2 enters stream_range and triggers seek cleanup
    chunk2 = await anext(stream2)
    assert len(chunk2) == chunk_sz

    # Prior stream1 tasks must now be cancelled
    for old_task in stream1_tasks:
        assert old_task.done() or old_task.cancelled()


@pytest.mark.asyncio
async def test_stream_disconnect_cleans_up_prefetch_tasks():
    """
    When client disconnects (generator aclose / break), all active prefetch tasks
    spawned by that stream must be cancelled and discarded.
    """
    chunk_sz = 1024
    total_sz = chunk_sz * 10
    file_bytes = b"Y" * total_sz

    storage = AdversarialStorageClient({3001: file_bytes})
    storage.delay = 0.1

    parts = [{
        "file_id": "file-disconnect-test",
        "part_index": 0,
        "byte_offset": 0,
        "part_size_bytes": total_sz,
        "telegram_message_id": 3001
    }]

    engine = StreamingEngine(chunk_size=chunk_sz)
    spec = RangeSpec(0, total_sz - 1, total_sz, total_sz, True, True)

    stream = engine.stream_range(parts, total_sz, spec, storage)
    # Read 1 chunk
    await anext(stream)
    await asyncio.sleep(0.02)

    # Simulate client disconnect
    await stream.aclose()

    # Active prefetch tasks should be empty
    await asyncio.sleep(0.05)
    assert len(engine._active_prefetch_tasks) == 0
    assert len(engine._active_prefetch_by_file.get("file-disconnect-test", set())) == 0


# =========================================================================
# 4. LRU Cache Isolation & Exception Drainage Challenges
# =========================================================================

@pytest.mark.asyncio
async def test_lru_cache_isolation_across_non_telegram_backends():
    """
    Non-Telegram files (NAS/S3/Nextcloud) share telegram_message_id = 0.
    The cache keys must include the file identifier to prevent File A returning File B's cached chunks.
    """
    chunk_sz = 512
    engine = StreamingEngine(chunk_size=chunk_sz)

    file_a_data = b"A" * 1024
    file_b_data = b"B" * 1024

    parts_a = [{
        "file_id": "nas-file-alpha",
        "part_index": 0,
        "byte_offset": 0,
        "part_size_bytes": 1024,
        "telegram_message_id": 0
    }]
    parts_b = [{
        "file_id": "nas-file-beta",
        "part_index": 0,
        "byte_offset": 0,
        "part_size_bytes": 1024,
        "telegram_message_id": 0
    }]

    storage_a = AdversarialStorageClient({"nas-file-alpha": file_a_data})
    storage_b = AdversarialStorageClient({"nas-file-beta": file_b_data})

    spec = RangeSpec(0, 100, 1024, 101, True, True)

    # Fetch File A
    res_a = [c async for c in engine.stream_range(parts_a, 1024, spec, storage_a)]
    # Fetch File B
    res_b = [c async for c in engine.stream_range(parts_b, 1024, spec, storage_b)]

    assert b"".join(res_a) == b"A" * 101
    assert b"".join(res_b) == b"B" * 101


@pytest.mark.asyncio
async def test_lru_cache_failed_fetch_recovery():
    """
    If an upstream fetch fails with an exception, the in-flight key must be deregistered
    and subsequent requests must not hang or deadlock.
    """
    cache = LRUChunkCache(max_chunks=4)
    key = ("test-file", "p0", 0)

    attempts = 0

    async def flaky_fetcher():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionResetError("Telegram upstream connection reset")
        return b"SUCCESS_ON_RETRY"

    # Attempt 1: fails
    with pytest.raises(ConnectionResetError):
        await cache.get_or_fetch(key, flaky_fetcher)

    # Invariant: key must not remain in _in_flight
    assert not cache.contains(key)
    assert key not in cache._in_flight

    # Attempt 2: retry succeeds
    result = await cache.get_or_fetch(key, flaky_fetcher)
    assert result == b"SUCCESS_ON_RETRY"
    assert cache.contains(key)


@pytest.mark.asyncio
async def test_lru_cache_future_exception_drainage():
    """
    When a waiter detaches from an in-flight fetch and the background task fails,
    done-callback exception drainage must prevent 'Future exception was never retrieved'.
    """
    cache = LRUChunkCache(max_chunks=4)
    key = ("orphan-file", "p0", 0)
    fetch_started = asyncio.Event()

    async def slow_failing_fetcher():
        fetch_started.set()
        await asyncio.sleep(0.05)
        raise RuntimeError("Background fetcher failed after waiter detached")

    # Start task
    task = asyncio.create_task(cache.get_or_fetch(key, slow_failing_fetcher))
    await fetch_started.wait()

    # Cancel waiter before fetcher finishes
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Wait for fetcher to complete and fail
    await asyncio.sleep(0.1)

    # Verification: cache should be clean and not holding broken in-flight state
    assert not cache.contains(key)
