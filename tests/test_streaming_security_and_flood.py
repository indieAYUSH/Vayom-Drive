import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from telethon.errors import FloodWaitError
from backend.main import app
from backend.config import settings
from backend.db.connection import get_db
from backend.storage.client import TelegramStorageClient


@pytest.mark.asyncio
async def test_iter_download_floodwait_recovery():
    """
    Validates C-2:
    - iter_download catches FloodWaitError mid-stream, applies global pause (X + 2 rule),
      and resumes from curr_offset seamlessly instead of aborting the stream.
    """
    tc = TelegramStorageClient()
    tc._is_connected = True
    settings.TELEGRAM_CHANNEL_ID = "-100123456789"

    mock_client = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.media = MagicMock()
    mock_client.get_messages = AsyncMock(return_value=mock_msg)

    # First attempt yields chunk 1, then raises FloodWaitError(seconds=1),
    # then second attempt yields chunk 2
    call_count = 0

    async def mock_iter_download(media, offset=0, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield b"AAAA"  # 4 bytes
            err = FloodWaitError(request=None)
            err.seconds = 1
            raise err
        else:
            assert offset == 4, f"Expected resumed offset to be 4, got {offset}"
            yield b"BBBB"

    mock_client.iter_download = mock_iter_download
    tc.client = mock_client

    start_time = time.time()
    chunks = []
    async for chunk in tc.stream_part_bytes(message_id=999, offset=0, limit=8, chunk_size=4):
        chunks.append(chunk)

    elapsed = time.time() - start_time
    # X + 2 rule: 1 + 2 = 3 seconds sleep
    assert elapsed >= 2.9, f"Expected sleep of at least 3s (X + 2 rule), got {elapsed:.2f}s"
    assert b"".join(chunks) == b"AAAABBBB"
    assert call_count == 2
    assert tc.flood_wait_seconds <= 1


@pytest.mark.asyncio
async def test_streaming_loopback_security_enforcement():
    """
    Validates C-3:
    - Rejects non-loopback Host header (e.g., external IP, evil.com, 0.0.0.0) with 403 Forbidden.
    - Allows loopback origins (127.0.0.1, localhost, ::1, testserver).
    - When STREAM_LOOPBACK_TOKEN is set, enforces token check on virtual-stream.
    """
    test_file_id = "test-loopback-sec-001"
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_deleted) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (test_file_id, "video.mp4", "video/mp4", "video", 1024, "fake_sha")
        )
        await db.execute(
            "INSERT OR REPLACE INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("part-loopback-1", test_file_id, 0, 0, 1024, "fake_sha", 9999)
        )
        await db.commit()

    # F-5: mock streaming now requires the blob to exist — seed it for message 9999
    mock_dir = settings.DATA_DIR / "mock_blobs"
    mock_dir.mkdir(parents=True, exist_ok=True)
    (mock_dir / "9999.bin").write_bytes(b"\x00" * 1024)

    client = TestClient(app)

    # 1. Non-loopback Host header -> 403 Forbidden
    res_external = client.get(
        f"/api/v1/virtual-stream/{test_file_id}",
        headers={"Host": "192.168.1.50"}
    )
    assert res_external.status_code == 403
    assert "non-loopback" in res_external.json()["detail"].lower()

    # 2. 0.0.0.0 Host header -> 403 Forbidden
    res_zero = client.get(
        f"/api/v1/virtual-stream/{test_file_id}",
        headers={"Host": "0.0.0.0"}
    )
    assert res_zero.status_code == 403

    # 3. Valid loopback Host without STREAM_LOOPBACK_TOKEN configured -> succeeds
    res_loopback = client.get(
        f"/api/v1/virtual-stream/{test_file_id}",
        headers={"Host": "127.0.0.1"}
    )
    assert res_loopback.status_code == 200

    # 4. Configure STREAM_LOOPBACK_TOKEN
    original_token = settings.STREAM_LOOPBACK_TOKEN
    settings.STREAM_LOOPBACK_TOKEN = "secret_loopback_tok_123"
    try:
        # Request with missing token -> 403
        res_no_tok = client.get(
            f"/api/v1/virtual-stream/{test_file_id}",
            headers={"Host": "127.0.0.1"}
        )
        assert res_no_tok.status_code == 403

        # Request with wrong token -> 403
        res_bad_tok = client.get(
            f"/api/v1/virtual-stream/{test_file_id}?token=wrong_tok",
            headers={"Host": "127.0.0.1"}
        )
        assert res_bad_tok.status_code == 403

        # 5. Verify moov endpoints also enforce loopback and token
        res_moov_bad = client.get(
            f"/api/v1/files/{test_file_id}/moov",
            headers={"Host": "192.168.1.50"}
        )
        assert res_moov_bad.status_code == 403

        res_moov_no_tok = client.get(
            f"/api/v1/files/{test_file_id}/moov",
            headers={"Host": "127.0.0.1"}
        )
        assert res_moov_no_tok.status_code == 403

        res_moov_bytes_bad = client.get(
            f"/api/v1/files/{test_file_id}/moov/bytes",
            headers={"Host": "192.168.1.50"}
        )
        assert res_moov_bytes_bad.status_code == 403
    finally:
        settings.STREAM_LOOPBACK_TOKEN = original_token


@pytest.mark.asyncio
async def test_soft_deleted_file_blocked_from_streaming_and_moov():
    """
    Validates C-4:
    - virtual-stream, /files/{id}/moov, and /files/{id}/moov/bytes reject soft-deleted (is_deleted=1) files with 404.
    """
    deleted_file_id = "test-soft-deleted-stream-001"
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_deleted) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (deleted_file_id, "deleted_vid.mp4", "video/mp4", "video", 2048, "sha_del")
        )
        await db.commit()

    client = TestClient(app)

    # 1. virtual-stream
    res_stream = client.get(f"/api/v1/virtual-stream/{deleted_file_id}")
    assert res_stream.status_code == 404
    assert res_stream.json()["detail"] == "File not found"

    # 2. moov info
    res_moov = client.get(f"/api/v1/files/{deleted_file_id}/moov")
    assert res_moov.status_code == 404
    assert res_moov.json()["detail"] == "File not found"

    # 3. moov bytes
    res_moov_bytes = client.get(f"/api/v1/files/{deleted_file_id}/moov/bytes")
    assert res_moov_bytes.status_code == 404
    assert res_moov_bytes.json()["detail"] == "File not found"


@pytest.mark.asyncio
async def test_malformed_and_multirange_headers_rejected_with_416():
    """
    Validates C-5:
    - Malformed or multi-range Range headers return HTTP 416 (Range Not Satisfiable)
      instead of falling back to full-content 200, strictly preventing 16 MiB 206 cap bypass.
    """
    test_file_id = "test-c5-range-cap-001"
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_deleted) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (test_file_id, "big_movie.mp4", "video/mp4", "video", 50000000, "sha_movie")
        )
        await db.execute(
            "INSERT OR REPLACE INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("part-c5-1", test_file_id, 0, 0, 50000000, "sha_movie", 9999)
        )
        await db.commit()

    client = TestClient(app)

    # 1. Invalid empty range `bytes=-` -> 416
    res_dash = client.get(f"/api/v1/virtual-stream/{test_file_id}", headers={"Range": "bytes=-"})
    assert res_dash.status_code == 416
    assert res_dash.headers["Content-Range"] == "bytes */50000000"

    # 2. Multi-range header `bytes=0-10,20-30` -> 416
    res_multi = client.get(f"/api/v1/virtual-stream/{test_file_id}", headers={"Range": "bytes=0-10,20-30"})
    assert res_multi.status_code == 416
    assert res_multi.headers["Content-Range"] == "bytes */50000000"

    # 3. Malformed garbage unit -> 416
    res_unit = client.get(f"/api/v1/virtual-stream/{test_file_id}", headers={"Range": "seconds=0-10"})
    assert res_unit.status_code == 416
    assert res_unit.headers["Content-Range"] == "bytes */50000000"

    # 4. Malformed values `bytes=abc-def` -> 416
    res_garbage = client.get(f"/api/v1/virtual-stream/{test_file_id}", headers={"Range": "bytes=abc-def"})
    assert res_garbage.status_code == 416
    assert res_garbage.headers["Content-Range"] == "bytes */50000000"

    # 5. Normal open-ended range `bytes=0-` -> 206 Partial Content capped to DEFAULT_MAX_206_BYTES (4 MiB)
    from backend.streaming.engine import DEFAULT_MAX_206_BYTES
    res_valid = client.head(f"/api/v1/virtual-stream/{test_file_id}", headers={"Range": "bytes=0-"})
    assert res_valid.status_code == 206
    assert res_valid.headers["Content-Length"] == str(DEFAULT_MAX_206_BYTES)
    assert res_valid.headers["Content-Range"] == f"bytes 0-{DEFAULT_MAX_206_BYTES - 1}/50000000"
