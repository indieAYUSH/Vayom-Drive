import asyncio
import struct
import pytest
from typing import AsyncIterator, List, Dict, Any
from fastapi.testclient import TestClient

from backend.streaming.engine import (
    RangeSpec,
    parse_and_clamp_range,
    LRUChunkCache,
    StreamingEngine,
    MP4MoovResolver,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_206_BYTES,
)
from backend.main import app
from backend.db.connection import get_db


# --- Mock Storage Client for In-Memory Testing ---
class MockStorageClient:
    def __init__(self, data_by_message_id: Dict[int, bytes]):
        self.data_by_message_id = data_by_message_id
        self.fetch_call_count = 0

    async def stream_part_bytes(
        self,
        message_id: int,
        channel_id: Any = None,
        file_id: Any = None,
        access_hash: Any = None,
        offset: int = 0,
        limit: int = None,
        chunk_size: int = 64 * 1024
    ) -> AsyncIterator[bytes]:
        self.fetch_call_count += 1
        data = self.data_by_message_id.get(message_id, b"")
        total_len = len(data)
        if offset >= total_len:
            return

        end = total_len if limit is None else min(total_len, offset + limit)
        curr = offset
        while curr < end:
            block_len = min(chunk_size, end - curr)
            yield data[curr:curr + block_len]
            curr += block_len


# =========================================================================
# 1. Range Parsing & Clamping Tests (RFC 7233 / 9110 & update.md Section 0)
# =========================================================================

def test_range_clamping_standard_range():
    total_size = 1000
    spec = parse_and_clamp_range("bytes=0-99", total_size)
    assert spec.is_range is True
    assert spec.is_satisfiable is True
    assert spec.start == 0
    assert spec.end == 99
    assert spec.content_length == 100
    assert spec.content_range_header == "bytes 0-99/1000"


def test_range_clamping_open_ended():
    total_size = 1000
    spec = parse_and_clamp_range("bytes=500-", total_size)
    assert spec.is_range is True
    assert spec.is_satisfiable is True
    assert spec.start == 500
    assert spec.end == 999
    assert spec.content_length == 500
    assert spec.content_range_header == "bytes 500-999/1000"


def test_range_clamping_suffix():
    total_size = 1000
    # Last 200 bytes
    spec = parse_and_clamp_range("bytes=-200", total_size)
    assert spec.is_range is True
    assert spec.is_satisfiable is True
    assert spec.start == 800
    assert spec.end == 999
    assert spec.content_length == 200
    assert spec.content_range_header == "bytes 800-999/1000"


def test_range_clamping_suffix_larger_than_file():
    total_size = 1000
    # Suffix larger than entire file clamps to whole file
    spec = parse_and_clamp_range("bytes=-5000", total_size)
    assert spec.is_range is True
    assert spec.is_satisfiable is True
    assert spec.start == 0
    assert spec.end == 999
    assert spec.content_length == 1000


def test_range_out_of_bounds_416():
    total_size = 1000
    # Start at or past total_size
    spec = parse_and_clamp_range("bytes=1000-", total_size)
    assert spec.is_satisfiable is False
    assert spec.content_range_header == "bytes */1000"

    # Start > End
    spec_reversed = parse_and_clamp_range("bytes=500-400", total_size)
    assert spec_reversed.is_satisfiable is False
    assert spec_reversed.content_range_header == "bytes */1000"

    # Suffix of 0 bytes
    spec_zero_suffix = parse_and_clamp_range("bytes=-0", total_size)
    assert spec_zero_suffix.is_satisfiable is False


def test_range_malformed_and_multirange_rejection():
    total_size = 1000
    # Multi-range header must be rejected with 416 per C-5 to prevent 206 cap bypass
    spec_multi = parse_and_clamp_range("bytes=0-10,20-30", total_size)
    assert spec_multi.is_satisfiable is False
    assert spec_multi.is_range is True

    # Malformed unit rejected with 416
    spec_unit = parse_and_clamp_range("seconds=0-10", total_size)
    assert spec_unit.is_satisfiable is False
    assert spec_unit.is_range is True

    # Empty string or garbage rejected with 416
    spec_garbage = parse_and_clamp_range("bytes=abc-def", total_size)
    assert spec_garbage.is_satisfiable is False
    assert spec_garbage.is_range is True

    # Empty range `bytes=-` rejected with 416
    spec_dash = parse_and_clamp_range("bytes=-", total_size)
    assert spec_dash.is_satisfiable is False
    assert spec_dash.is_range is True


def test_206_capping_invariant():
    """Verify that single 206 responses are capped to max_chunk_bytes (e.g. 16 MiB)."""
    file_size = 100 * 1024 * 1024  # 100 MiB
    cap_bytes = 16 * 1024 * 1024   # 16 MiB

    spec = parse_and_clamp_range("bytes=0-", file_size, max_chunk_bytes=cap_bytes)
    assert spec.is_range is True
    assert spec.is_capped is True
    assert spec.start == 0
    assert spec.end == cap_bytes - 1
    assert spec.content_length == cap_bytes
    assert spec.content_range_header == f"bytes 0-{cap_bytes - 1}/{file_size}"

    # Verify a small request within cap is NOT capped
    small_spec = parse_and_clamp_range("bytes=0-1000", file_size, max_chunk_bytes=cap_bytes)
    assert small_spec.is_capped is False
    assert small_spec.content_length == 1001


# =========================================================================
# 2. Multi-Part File Spanning & Chunk Alignment Tests
# =========================================================================

@pytest.mark.asyncio
async def test_multi_part_file_spanning_stream():
    """
    Tests streaming when a file is split across multiple parts (e.g. >1.9 GB files).
    Range requests spanning the boundary between Part 0 and Part 1 must stream seamlessly.
    """
    part0_data = bytes(i % 256 for i in range(1000))
    part1_data = bytes((i + 1000) % 256 for i in range(1000))
    full_data = part0_data + part1_data

    storage = MockStorageClient({
        101: part0_data,
        102: part1_data
    })

    parts = [
        {"part_index": 0, "byte_offset": 0, "part_size_bytes": 1000, "telegram_message_id": 101},
        {"part_index": 1, "byte_offset": 1000, "part_size_bytes": 1000, "telegram_message_id": 102}
    ]

    engine = StreamingEngine(chunk_size=256)

    # Range spanning Part 0 and Part 1: from byte 800 to 1200 (401 bytes)
    range_spec = parse_and_clamp_range("bytes=800-1200", 2000)
    assert range_spec.content_length == 401

    streamed_bytes = bytearray()
    async for chunk in engine.stream_range(parts, 2000, range_spec, storage):
        streamed_bytes.extend(chunk)

    assert len(streamed_bytes) == 401
    assert bytes(streamed_bytes) == full_data[800:1201]


@pytest.mark.asyncio
async def test_chunk_alignment_unaligned_offsets():
    """
    Tests that offsets not aligned to chunk boundaries (e.g. starting at byte 17 on 512-byte chunks)
    correctly align the fetch and slice out exact requested sub-ranges.
    """
    test_data = bytes(i % 256 for i in range(2048))
    storage = MockStorageClient({201: test_data})
    parts = [{"part_index": 0, "byte_offset": 0, "part_size_bytes": 2048, "telegram_message_id": 201}]

    engine = StreamingEngine(chunk_size=512)

    # Range from byte 17 to byte 1234
    range_spec = parse_and_clamp_range("bytes=17-1234", 2048)
    assert range_spec.content_length == 1234 - 17 + 1

    streamed_bytes = bytearray()
    async for chunk in engine.stream_range(parts, 2048, range_spec, storage):
        streamed_bytes.extend(chunk)

    assert len(streamed_bytes) == 1218
    assert bytes(streamed_bytes) == test_data[17:1235]


# =========================================================================
# 3. LRU Chunk Cache & Single-Flight Coalescing Tests
# =========================================================================

@pytest.mark.asyncio
async def test_lru_cache_and_single_flight():
    """
    Tests that concurrent requests for the exact same chunk coalesce into
    a SINGLE upstream fetch via asyncio.Future single-flight pattern.
    """
    cache = LRUChunkCache(max_chunks=4)
    fetch_count = 0

    async def mock_fetcher():
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.05)  # Simulate network latency
        return b"CHUNK_DATA_ABC"

    # Fire 10 concurrent tasks requesting the same chunk key
    tasks = [cache.get_or_fetch((101, 0), mock_fetcher) for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # Verification: Upstream fetch called EXACTLY ONCE!
    assert fetch_count == 1
    assert all(r == b"CHUNK_DATA_ABC" for r in results)

    # Next fetch is instant from cache
    res = await cache.get_or_fetch((101, 0), mock_fetcher)
    assert res == b"CHUNK_DATA_ABC"
    assert fetch_count == 1


@pytest.mark.asyncio
async def test_single_flight_cancellation_cleans_up_and_recovers():
    """C-6: cancelling an in-flight fetch must clear _in_flight and let later requests refetch."""
    cache = LRUChunkCache(max_chunks=4)
    key = (777, 0)
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def slow_fetcher():
        fetch_started.set()
        await release_fetch.wait()  # Hold the fetch open until we cancel it
        return b"NEVER_RETURNED"

    async def recovered_fetcher():
        return b"RECOVERED_DATA"

    waiter = asyncio.create_task(cache.get_or_fetch(key, slow_fetcher))
    await asyncio.wait_for(fetch_started.wait(), timeout=1.0)
    assert cache.contains(key)

    # Simulate a disconnecting client / shutdown: cancel the in-flight fetch task
    fetch_tasks = [
        t for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and t is not waiter
        and "execute_fetch" in getattr(t.get_coro(), "__qualname__", "")
    ]
    assert len(fetch_tasks) == 1
    fetch_tasks[0].cancel()

    # The waiter must not hang forever on an abandoned future
    results = await asyncio.wait_for(asyncio.gather(waiter, return_exceptions=True), timeout=1.0)
    assert isinstance(results[0], asyncio.CancelledError)

    # C-6: single-flight bookkeeping must be fully cleaned up
    assert cache._in_flight == {}
    assert not cache.contains(key)

    # Subsequent request must not deadlock — it refetches and succeeds
    res = await asyncio.wait_for(cache.get_or_fetch(key, recovered_fetcher), timeout=1.0)
    assert res == b"RECOVERED_DATA"
    assert cache._in_flight == {}


@pytest.mark.asyncio
async def test_cache_clear_cancels_inflight_futures():
    """C-6: clear() must resolve outstanding in-flight waiters, not just drop the dict."""
    cache = LRUChunkCache(max_chunks=4)
    key = (888, 0)
    fetch_started = asyncio.Event()

    async def slow_fetcher():
        fetch_started.set()
        await asyncio.sleep(30)
        return b"NEVER_RETURNED"

    waiter = asyncio.create_task(cache.get_or_fetch(key, slow_fetcher))
    await asyncio.wait_for(fetch_started.wait(), timeout=1.0)

    await cache.clear()

    results = await asyncio.wait_for(asyncio.gather(waiter, return_exceptions=True), timeout=1.0)
    assert isinstance(results[0], asyncio.CancelledError)
    assert cache._in_flight == {}
    assert cache.size() == 0

    # Clean up the still-running fetch task so the event loop closes cleanly
    pending = [
        t for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and "execute_fetch" in getattr(t.get_coro(), "__qualname__", "")
    ]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# =========================================================================
# 4. MP4 Moov Atom Resolver Tests (FastStart vs Trailing Moov)
# =========================================================================

def build_mp4_box(atom_type: str, payload: bytes) -> bytes:
    """Builds standard 32-bit ISO-BMFF box."""
    box_size = 8 + len(payload)
    return struct.pack(">I", box_size) + atom_type.encode("ascii") + payload


@pytest.mark.asyncio
async def test_mp4_moov_resolver_faststart():
    """FastStart MP4 has ftyp, then moov near front."""
    ftyp = build_mp4_box("ftyp", b"isommp42")
    moov_payload = b"SAMPLE_INDEX_TABLE_METADATA_HEADER"
    moov = build_mp4_box("moov", moov_payload)
    mdat = build_mp4_box("mdat", b"\x00" * 1000)

    faststart_mp4 = ftyp + moov + mdat
    storage = MockStorageClient({301: faststart_mp4})
    parts = [{"part_index": 0, "byte_offset": 0, "part_size_bytes": len(faststart_mp4), "telegram_message_id": 301}]

    res = await MP4MoovResolver.inspect_mp4(parts, len(faststart_mp4), storage)
    assert res["is_mp4"] is True
    assert res["has_moov"] is True
    assert res["faststart"] is True
    assert res["moov_offset"] == len(ftyp)
    assert res["moov_size"] == len(moov)


@pytest.mark.asyncio
async def test_mp4_moov_resolver_trailing_non_faststart():
    """
    Non-FastStart MP4 has ftyp, large mdat (video frames), then moov at the tail.
    The resolver must find the moov atom at the tail without loading the mdat frames into RAM.
    """
    ftyp = build_mp4_box("ftyp", b"isommp42")
    # Simulate 5 MB of mdat frames
    mdat_size = 5 * 1024 * 1024
    mdat_header = struct.pack(">I", mdat_size) + b"mdat"
    mdat_body = b"\xAA" * (mdat_size - 8)
    mdat = mdat_header + mdat_body

    moov_payload = b"TRAILING_INDEX_TABLE_CODEC_CONFIG"
    moov = build_mp4_box("moov", moov_payload)

    trailing_mp4 = ftyp + mdat + moov
    storage = MockStorageClient({401: trailing_mp4})
    parts = [{"part_index": 0, "byte_offset": 0, "part_size_bytes": len(trailing_mp4), "telegram_message_id": 401}]

    res = await MP4MoovResolver.inspect_mp4(parts, len(trailing_mp4), storage)
    assert res["is_mp4"] is True
    assert res["has_moov"] is True
    assert res["faststart"] is False
    assert res.get("trailing_moov") is True
    assert res["moov_offset"] == len(ftyp) + len(mdat)
    assert res["moov_size"] == len(moov)


# =========================================================================
# 5. Client Disconnect & Short Body Error Handling Tests
# =========================================================================

@pytest.mark.asyncio
async def test_client_disconnect_cancellation():
    """Verifies that client disconnect (task cancellation) terminates upstream iteration cleanly."""
    data = b"\x00" * 100000
    storage = MockStorageClient({501: data})
    parts = [{"part_index": 0, "byte_offset": 0, "part_size_bytes": len(data), "telegram_message_id": 501}]

    engine = StreamingEngine(chunk_size=1024)
    range_spec = parse_and_clamp_range("bytes=0-", len(data))

    gen = engine.stream_range(parts, len(data), range_spec, storage)

    # Consume 1 chunk
    chunk1 = await anext(gen)
    assert len(chunk1) > 0

    # Simulate client disconnect by closing/cancelling the generator
    await gen.aclose()


@pytest.mark.asyncio
async def test_short_body_detection():
    """If storage yields fewer bytes than promised, engine must raise ConnectionResetError to drop stream."""
    class TruncatedStorageClient:
        async def stream_part_bytes(self, *args, **kwargs):
            # Promised 1000 bytes, but only yield 100 bytes and stop
            yield b"X" * 100

    storage = TruncatedStorageClient()
    parts = [{"part_index": 0, "byte_offset": 0, "part_size_bytes": 1000, "telegram_message_id": 601}]
    engine = StreamingEngine()
    range_spec = parse_and_clamp_range("bytes=0-999", 1000)

    with pytest.raises(ConnectionResetError, match="Stream truncated mid-body"):
        async for _ in engine.stream_range(parts, 1000, range_spec, storage):
            pass


# =========================================================================
# 6. HTTP API Endpoint Tests (Virtual Stream & Moov Info)
# =========================================================================

@pytest.mark.asyncio
async def test_virtual_stream_api_route():
    """Tests the /api/v1/virtual-stream/{file_id} endpoint with TestClient."""
    # Seed mock file in DB
    test_file_id = "test-stream-file-uuid-001"
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO files (id, filename, mime_type, file_category, size_bytes, sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (test_file_id, "trailer.mp4", "video/mp4", "video", 5000, "mock_sha")
        )
        await db.execute(
            """
            INSERT OR REPLACE INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("part-1", test_file_id, 0, 0, 5000, "mock_sha", 9999)
        )
        await db.commit()

    # F-5: mock streaming now requires the blob to exist — seed it for message 9999
    from backend.config import settings
    mock_dir = settings.DATA_DIR / "mock_blobs"
    mock_dir.mkdir(parents=True, exist_ok=True)
    (mock_dir / "9999.bin").write_bytes(b"\x00" * 5000)

    client = TestClient(app)

    # 1. Test 200 OK without range
    resp_full = client.get(f"/api/v1/virtual-stream/{test_file_id}")
    assert resp_full.status_code == 200
    assert resp_full.headers["Accept-Ranges"] == "bytes"
    assert resp_full.headers["Content-Length"] == "5000"

    # 2. Test 206 Partial Content with Range
    resp_range = client.get(f"/api/v1/virtual-stream/{test_file_id}", headers={"Range": "bytes=100-299"})
    assert resp_range.status_code == 206
    assert resp_range.headers["Accept-Ranges"] == "bytes"
    assert resp_range.headers["Content-Range"] == "bytes 100-299/5000"
    assert resp_range.headers["Content-Length"] == "200"

    # 3. Test 416 Range Not Satisfiable when start past EOF
    resp_416 = client.get(f"/api/v1/virtual-stream/{test_file_id}", headers={"Range": "bytes=99999-"})
    assert resp_416.status_code == 416
    assert resp_416.headers["Content-Range"] == "bytes */5000"

    # 4. Test MP4 moov endpoint
    resp_moov = client.get(f"/api/v1/files/{test_file_id}/moov")
    assert resp_moov.status_code == 200
    json_data = resp_moov.json()
    assert json_data["file_id"] == test_file_id
    assert json_data["total_size"] == 5000

    # 5. Test HEAD request on virtual-stream (must return headers with ZERO body bytes)
    resp_head = client.head(f"/api/v1/virtual-stream/{test_file_id}")
    assert resp_head.status_code == 200
    assert resp_head.headers["Content-Length"] == "5000"
    assert resp_head.headers["Accept-Ranges"] == "bytes"
    assert len(resp_head.content) == 0

    # 6. Test HEAD request on download endpoint
    resp_dl_head = client.head(f"/api/v1/files/{test_file_id}/download")
    assert resp_dl_head.status_code == 200
    assert resp_dl_head.headers["Content-Length"] == "5000"
    assert len(resp_dl_head.content) == 0

    # 7. Test download endpoint with Range (206) and out-of-bounds Range (416)
    resp_dl_206 = client.get(f"/api/v1/files/{test_file_id}/download", headers={"Range": "bytes=0-49"})
    assert resp_dl_206.status_code == 206
    assert resp_dl_206.headers["Content-Range"] == "bytes 0-49/5000"
    assert resp_dl_206.headers["Content-Length"] == "50"

    resp_dl_416 = client.get(f"/api/v1/files/{test_file_id}/download", headers={"Range": "bytes=999999-"})
    assert resp_dl_416.status_code == 416
    assert resp_dl_416.headers["Content-Range"] == "bytes */5000"

    # 8. Test root-level /virtual-stream/{file_id} path with Range
    resp_root_206 = client.get(f"/virtual-stream/{test_file_id}", headers={"Range": "bytes=50-99"})
    assert resp_root_206.status_code == 206
    assert resp_root_206.headers["Content-Range"] == "bytes 50-99/5000"
    assert resp_root_206.headers["Content-Length"] == "50"


def test_range_zero_byte_file_flaw():
    """Flaw test: Range request on a 0-byte file must be unsatisfiable (416), while non-range is 200 OK."""
    spec_range = parse_and_clamp_range("bytes=0-", 0)
    assert spec_range.is_satisfiable is False
    assert spec_range.content_range_header == "bytes */0"

    spec_no_range = parse_and_clamp_range(None, 0)
    assert spec_no_range.is_satisfiable is True
    assert spec_no_range.content_length == 0


@pytest.mark.asyncio
async def test_out_of_order_parts_stream_flaw():
    """Flaw test: If database returns file parts out of order, engine must sort by byte_offset."""
    part0 = bytes(b"A" * 500)
    part1 = bytes(b"B" * 500)

    storage = MockStorageClient({701: part0, 702: part1})
    # Deliberately out-of-order parts list
    unsorted_parts = [
        {"part_index": 1, "byte_offset": 500, "part_size_bytes": 500, "telegram_message_id": 702},
        {"part_index": 0, "byte_offset": 0, "part_size_bytes": 500, "telegram_message_id": 701}
    ]

    engine = StreamingEngine(chunk_size=256)
    range_spec = parse_and_clamp_range("bytes=450-550", 1000)

    streamed = bytearray()
    async for chunk in engine.stream_range(unsorted_parts, 1000, range_spec, storage):
        streamed.extend(chunk)

    expected = (b"A" * 50) + (b"B" * 51)
    assert bytes(streamed) == expected


@pytest.mark.asyncio
async def test_lru_cache_reuse_in_stream_range():
    """Flaw test: Verify that second range request within the same chunk is served from cache."""
    test_data = bytes(i % 256 for i in range(1024))
    storage = MockStorageClient({801: test_data})
    parts = [{"part_index": 0, "byte_offset": 0, "part_size_bytes": 1024, "telegram_message_id": 801}]

    engine = StreamingEngine(chunk_size=512)

    # First stream request: bytes 0-200 (fetches chunk 0)
    range1 = parse_and_clamp_range("bytes=0-200", 1024)
    async for _ in engine.stream_range(parts, 1024, range1, storage):
        pass
    assert storage.fetch_call_count == 1

    # Second stream request: bytes 100-300 (same chunk 0)
    range2 = parse_and_clamp_range("bytes=100-300", 1024)
    async for _ in engine.stream_range(parts, 1024, range2, storage):
        pass
    # Must NOT call storage again!
    assert storage.fetch_call_count == 1


@pytest.mark.asyncio
async def test_aes_ctr_decrypted_streaming():
    """
    Tests AES-256-CTR zero-overhead encryption & decryption during streaming.
    Adheres strictly to Invariant 1: 1 byte plaintext === 1 byte ciphertext,
    block counter derived from aligned offset.
    """
    from backend.crypto.cipher import aes_ctr_encrypt, generate_file_nonce

    key = b"12345678901234567890123456789012"  # 32 bytes
    nonce = generate_file_nonce()  # 16 hex chars (8 bytes)

    # 4096 bytes of known plaintext
    plaintext = bytes((i * 7) % 256 for i in range(4096))
    ciphertext = aes_ctr_encrypt(plaintext, key, nonce, aligned_byte_offset=0)
    assert len(ciphertext) == len(plaintext)
    assert ciphertext != plaintext

    storage = MockStorageClient({901: ciphertext})
    parts = [{"part_index": 0, "byte_offset": 0, "part_size_bytes": len(ciphertext), "telegram_message_id": 901}]

    engine = StreamingEngine(chunk_size=512)

    # Request range from byte 33 to byte 899 (unaligned start and end)
    range_spec = parse_and_clamp_range("bytes=33-899", len(plaintext))
    assert range_spec.content_length == 899 - 33 + 1

    streamed = bytearray()
    async for chunk in engine.stream_range(
        parts,
        len(plaintext),
        range_spec,
        storage,
        encryption="ctr",
        nonce=nonce,
        key=key
    ):
        streamed.extend(chunk)

    assert len(streamed) == 899 - 33 + 1
    assert bytes(streamed) == plaintext[33:900]


@pytest.mark.asyncio
async def test_moov_bytes_api_endpoint():
    """Tests the /api/v1/files/{file_id}/moov/bytes endpoint."""
    ftyp = build_mp4_box("ftyp", b"isommp42")
    moov_payload = b"DIRECT_MOOV_SAMPLE_TABLE_DATA"
    moov = build_mp4_box("moov", moov_payload)
    mdat = build_mp4_box("mdat", b"\x00" * 500)
    mp4_data = ftyp + moov + mdat

    file_id = "test-moov-bytes-uuid-002"
    mock_msg_id = 9988

    async with get_db() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO files (id, filename, mime_type, file_category, size_bytes, sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (file_id, "moov_test.mp4", "video/mp4", "video", len(mp4_data), "sha")
        )
        await db.execute(
            """
            INSERT OR REPLACE INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("p-moov-1", file_id, 0, 0, len(mp4_data), "sha", mock_msg_id)
        )
        await db.commit()

    # Create mock blob file in settings.DATA_DIR / "mock_blobs" / f"{mock_msg_id}.bin"
    from backend.config import settings
    mock_dir = settings.DATA_DIR / "mock_blobs"
    mock_dir.mkdir(parents=True, exist_ok=True)
    blob_path = mock_dir / f"{mock_msg_id}.bin"
    blob_path.write_bytes(mp4_data)

    client = TestClient(app)
    resp = client.get(f"/api/v1/files/{file_id}/moov/bytes")
    assert resp.status_code == 206
    assert resp.headers["Content-Length"] == str(len(moov))
    assert resp.content == moov


