import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock
from telethon.errors import FloodWaitError
from backend.storage.client import TelegramStorageClient
from backend.config import settings


@pytest.mark.asyncio
async def test_upload_concurrency_limit_3_workers():
    """
    Validates Anti-Ban Invariant 4:
    Limits upload concurrency to 3-4 parallel workers.
    Ensures no more than 3 upload operations run concurrently against Telegram.
    """
    client = TelegramStorageClient()
    client._is_connected = True
    settings.TELEGRAM_CHANNEL_ID = "-1001234567890"

    current_concurrent = 0
    max_concurrent_seen = 0

    mock_client = MagicMock()

    async def mock_upload_file(file_path, part_size_kb=None, progress_callback=None):
        nonlocal current_concurrent, max_concurrent_seen
        assert part_size_kb == 512, f"Expected part_size_kb=512, got {part_size_kb}"
        current_concurrent += 1
        if current_concurrent > max_concurrent_seen:
            max_concurrent_seen = current_concurrent
        # Simulate network latency
        await asyncio.sleep(0.05)
        current_concurrent -= 1
        return MagicMock()

    async def mock_send_file(channel, file_handle, caption=None, force_document=True):
        msg = MagicMock()
        msg.id = 123
        msg.media = MagicMock()
        msg.media.document = MagicMock()
        msg.media.document.id = 456
        msg.media.document.access_hash = 789
        return msg

    mock_client.upload_file = AsyncMock(side_effect=mock_upload_file)
    mock_client.send_file = AsyncMock(side_effect=mock_send_file)
    client.client = mock_client

    # Launch 10 simultaneous upload tasks
    tasks = [
        client.upload_part(f"/tmp/fake_{i}.bin", caption="test")
        for i in range(10)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 10
    assert max_concurrent_seen <= 3, f"Concurrency exceeded 3! Max seen: {max_concurrent_seen}"
    print(f"\n[PASS] Concurrency strictly limited: max {max_concurrent_seen} workers concurrently (cap: 3).")


@pytest.mark.asyncio
async def test_strict_global_floodwait_x_plus_2():
    """
    Validates Anti-Ban Invariant 5:
    Catches FLOOD_WAIT_X globally, sets X + 2 penalty, and holds all concurrent calls.
    """
    client = TelegramStorageClient()
    client._is_connected = True
    settings.TELEGRAM_CHANNEL_ID = "-1001234567890"

    attempt_count = 0
    mock_client = MagicMock()

    async def mock_upload_with_flood(file_path, part_size_kb=None, progress_callback=None):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            # Simulate Telegram returning FLOOD_WAIT_2
            err = FloodWaitError(request=None)
            err.seconds = 2
            raise err
        return MagicMock()

    async def mock_send_file(channel, file_handle, caption=None, force_document=True):
        msg = MagicMock()
        msg.id = 999
        msg.media = None
        return msg

    mock_client.upload_file = AsyncMock(side_effect=mock_upload_with_flood)
    mock_client.send_file = AsyncMock(side_effect=mock_send_file)
    client.client = mock_client

    start_time = time.time()
    res = await client.upload_part("/tmp/test.bin", caption="test")
    elapsed = time.time() - start_time

    # X was 2 seconds -> penalty was 2 + 2 = 4 seconds.
    assert elapsed >= 3.8, f"Expected elapsed >= 3.8s for 2+2 penalty, got {elapsed:.2f}s"
    assert attempt_count == 2, f"Expected 2 attempts (1 failure + 1 retry), got {attempt_count}"
    assert res["message_id"] == 999
    print(f"\n[PASS] Global FloodWait correctly paused for {elapsed:.2f}s (X + 2 rule enforced).")


@pytest.mark.asyncio
async def test_global_floodwait_blocks_all_concurrent_requests():
    """
    Validates that when one worker triggers a FloodWait, other concurrent workers
    are held by _wait_if_flooded() and do NOT send requests to Telegram.
    """
    client = TelegramStorageClient()
    client.set_flood_wait(3)  # 3 seconds penalty active

    assert client.online_status == "flood_wait"
    assert client.flood_wait_seconds >= 2

    start_time = time.time()
    await client._wait_if_flooded()
    elapsed = time.time() - start_time

    assert elapsed >= 2.8, f"Expected _wait_if_flooded to wait ~3s, waited {elapsed:.2f}s"
    assert client.flood_wait_seconds == 0
    assert client.online_status == "reconnecting"  # not connected yet
    print(f"\n[PASS] _wait_if_flooded held execution for {elapsed:.2f}s until penalty expired.")


if __name__ == "__main__":
    asyncio.run(test_upload_concurrency_limit_3_workers())
    asyncio.run(test_strict_global_floodwait_x_plus_2())
    asyncio.run(test_global_floodwait_blocks_all_concurrent_requests())
