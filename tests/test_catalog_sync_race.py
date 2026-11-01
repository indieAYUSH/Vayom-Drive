import asyncio
import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from backend.config import settings
from backend.storage.client import TelegramStorageClient, storage_client
from backend.api.routes import sync_master_catalog
from backend.db.connection import get_db

@pytest.mark.asyncio
async def test_save_and_pin_catalog_deletes_old_pinned_message(tmp_path):
    """
    Validates E-1 fix:
    - save_and_pin_catalog automatically tracks the last pinned message ID
    - When a new catalog is saved & pinned, the old message is deleted from the channel
    """
    tc = TelegramStorageClient()
    tc._is_connected = True
    settings.TELEGRAM_CHANNEL_ID = "-100987654321"

    deleted_messages = []
    current_msg_id = 100

    mock_client = AsyncMock()

    async def mock_send_file(entity, file_path, **kwargs):
        nonlocal current_msg_id
        current_msg_id += 1
        msg = MagicMock()
        msg.id = current_msg_id
        return msg

    async def mock_delete_messages(entity, msg_ids):
        deleted_messages.extend(msg_ids)

    mock_client.send_file = AsyncMock(side_effect=mock_send_file)
    mock_client.pin_message = AsyncMock()
    mock_client.delete_messages = AsyncMock(side_effect=mock_delete_messages)
    tc.client = mock_client

    catalog_data_1 = {"version": "1.0", "files": [{"id": "f1"}]}
    catalog_data_2 = {"version": "1.0", "files": [{"id": "f1"}, {"id": "f2"}]}
    catalog_data_3 = {"version": "1.0", "files": [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]}

    # 1. First catalog upload (msg_id = 101)
    msg1 = await tc.save_and_pin_catalog(catalog_data_1)
    assert msg1 == 101
    assert tc.last_pinned_catalog_msg_id == 101
    assert len(deleted_messages) == 0

    # 2. Second catalog upload (msg_id = 102) -> Should delete msg 101
    msg2 = await tc.save_and_pin_catalog(catalog_data_2)
    assert msg2 == 102
    assert tc.last_pinned_catalog_msg_id == 102
    assert 101 in deleted_messages

    # 3. Third catalog upload (msg_id = 103) -> Should delete msg 102
    msg3 = await tc.save_and_pin_catalog(catalog_data_3)
    assert msg3 == 103
    assert tc.last_pinned_catalog_msg_id == 103
    assert 102 in deleted_messages
    assert deleted_messages == [101, 102]


@pytest.mark.asyncio
async def test_save_and_pin_catalog_explicit_old_pinned_id():
    """
    Validates that callers explicitly passing old_pinned_id triggers deletion of that specific message.
    """
    tc = TelegramStorageClient()
    tc._is_connected = True
    settings.TELEGRAM_CHANNEL_ID = "-100987654321"

    deleted_messages = []
    mock_client = AsyncMock()
    msg = MagicMock()
    msg.id = 500
    mock_client.send_file = AsyncMock(return_value=msg)
    mock_client.pin_message = AsyncMock()
    mock_client.delete_messages = AsyncMock(side_effect=lambda entity, ids: deleted_messages.extend(ids))
    tc.client = mock_client

    await tc.save_and_pin_catalog({"version": "1.0"}, old_pinned_id=450)
    assert 450 in deleted_messages


@pytest.mark.asyncio
async def test_atomic_snapshot_and_cleanup_temp_dir():
    """
    Validates E-1 fix:
    - vault_catalog_backup.json is written atomically via temporary files
    - Temporary catalog directories are strictly cleaned up
    - No temporary .tmp files or catalog_tmp directories leak
    """
    tc = TelegramStorageClient()
    tc._is_connected = True
    settings.TELEGRAM_CHANNEL_ID = "-100987654321"

    mock_client = AsyncMock()
    msg = MagicMock()
    msg.id = 201
    mock_client.send_file = AsyncMock(return_value=msg)
    mock_client.pin_message = AsyncMock()
    mock_client.delete_messages = AsyncMock()
    tc.client = mock_client

    catalog_data = {
        "version": "1.0",
        "updated_at": "2026-10-18T10:00:00Z",
        "files": [{"id": "test_atomicity", "filename": "atom.txt"}]
    }

    await tc.save_and_pin_catalog(catalog_data)

    backup_file = settings.DATA_DIR / "vault_catalog_backup.json"
    assert backup_file.exists()
    loaded = json.loads(backup_file.read_text(encoding="utf-8"))
    assert loaded["files"][0]["id"] == "test_atomicity"

    # Verify no tmp files or tmp dirs remain in DATA_DIR
    tmp_files = list(settings.DATA_DIR.glob("*.tmp"))
    assert len(tmp_files) == 0

    catalog_tmp_dirs = list(settings.DATA_DIR.glob("catalog_tmp_*"))
    assert len(catalog_tmp_dirs) == 0


@pytest.mark.asyncio
async def test_sync_master_catalog_coalescing_and_serialization():
    """
    Validates E-1 fix:
    - When 10 concurrent sync_master_catalog() calls occur rapidly,
      they are serialized behind an asyncio.Lock and coalesced into minimal executions.
    - Prevents concurrent temp file overwriting and channel RPC flooding.
    """
    call_count = 0
    original_save = storage_client.save_and_pin_catalog

    async def mock_save_and_pin(catalog_data, old_pinned_id=None):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)  # Simulate network / IO latency
        return 999000 + call_count

    storage_client.save_and_pin_catalog = mock_save_and_pin

    try:
        # Launch 10 sync_master_catalog calls simultaneously
        tasks = [sync_master_catalog() for _ in range(10)]
        await asyncio.gather(*tasks)

        # Because tasks arrive while another sync is in progress, they must coalesce.
        # Call count should be at most 2 (1 running + 1 final coalesced pass for trailing changes),
        # definitely much less than 10.
        assert 1 <= call_count <= 2, f"Expected 1-2 coalesced syncs, got {call_count}"
    finally:
        storage_client.save_and_pin_catalog = original_save


@pytest.mark.asyncio
async def test_catalog_state_persistence():
    """
    Validates that TelegramStorageClient persists its state to catalog_state.json
    and successfully reloads last_pinned_catalog_msg_id on new client initialization.
    """
    tc1 = TelegramStorageClient()
    tc1.last_pinned_catalog_msg_id = 888777
    tc1.last_backup_catalog_msg_id = 666555
    tc1.last_saved_catalog_msg_id = 444333
    tc1._save_catalog_state()

    state_file = settings.DATA_DIR / "catalog_state.json"
    assert state_file.exists()

    tc2 = TelegramStorageClient()
    assert tc2.last_pinned_catalog_msg_id == 888777
    assert tc2.last_backup_catalog_msg_id == 666555
    assert tc2.last_saved_catalog_msg_id == 444333


@pytest.mark.asyncio
async def test_backup_and_saved_messages_mirror_cleanup():
    """
    Validates that secondary backup channel and Saved Messages ('me') copies
    are also cleaned up upon subsequent catalog updates to prevent channel clutter.
    """
    tc = TelegramStorageClient()
    tc._is_connected = True
    settings.TELEGRAM_CHANNEL_ID = "-1001111"
    settings.TELEGRAM_BACKUP_CHANNEL_ID = "-1002222"
    settings.AUTH_MODE = "qr"
    settings.AUTO_MIRROR_SAVED_MESSAGES = True

    deleted_calls = []
    msg_counter = 1000

    mock_client = AsyncMock()

    async def mock_send(entity, file_path, **kwargs):
        nonlocal msg_counter
        msg_counter += 1
        msg = MagicMock()
        msg.id = msg_counter
        return msg

    async def mock_del(entity, ids):
        deleted_calls.append((str(entity), list(ids)))

    mock_client.send_file = AsyncMock(side_effect=mock_send)
    mock_client.pin_message = AsyncMock()
    mock_client.delete_messages = AsyncMock(side_effect=mock_del)
    tc.client = mock_client

    # First upload: generates 3 messages (primary: 1001, backup: 1002, saved: 1003)
    await tc.save_and_pin_catalog({"version": "1.0", "files": []})
    assert len(deleted_calls) == 0
    assert tc.last_pinned_catalog_msg_id == 1001
    assert tc.last_backup_catalog_msg_id == 1002
    assert tc.last_saved_catalog_msg_id == 1003

    # Second upload: generates 3 messages (primary: 1004, backup: 1005, saved: 1006)
    # Should delete 1001 from primary, 1002 from backup, and 1003 from 'me'
    await tc.save_and_pin_catalog({"version": "1.0", "files": [{"id": "f1"}]})
    assert ("-1001111", [1001]) in deleted_calls
    assert ("-1002222", [1002]) in deleted_calls
    assert ("me", [1003]) in deleted_calls


@pytest.mark.asyncio
async def test_concurrent_save_and_pin_no_temp_file_collision():
    """
    Validates that concurrent save_and_pin_catalog calls do not delete or overwrite
    each other's temp files or corrupt vault_catalog_backup.json.
    """
    tc = TelegramStorageClient()
    tc._is_connected = True
    settings.TELEGRAM_CHANNEL_ID = "-1001111"

    mock_client = AsyncMock()
    sent_contents = []

    async def mock_send(entity, file_path, **kwargs):
        with open(file_path, "r", encoding="utf-8") as f:
            sent_contents.append(json.load(f))
        await asyncio.sleep(0.02)
        msg = MagicMock()
        msg.id = uuid.uuid4().int % 1000000
        return msg

    mock_client.send_file = AsyncMock(side_effect=mock_send)
    mock_client.pin_message = AsyncMock()
    mock_client.delete_messages = AsyncMock()
    tc.client = mock_client

    # Run 5 concurrent save_and_pin_catalog with distinct payloads
    tasks = [
        tc.save_and_pin_catalog({"version": "1.0", "worker": i})
        for i in range(5)
    ]
    results = await asyncio.gather(*tasks)
    assert len(results) == 5
    assert len(sent_contents) == 5

    # Check that each sent content was valid and uncorrupted
    workers = {c["worker"] for c in sent_contents}
    assert workers == {0, 1, 2, 3, 4}

    # Verify no leaked tmp files
    assert len(list(settings.DATA_DIR.glob("*.tmp"))) == 0
    assert len(list(settings.DATA_DIR.glob("catalog_tmp_*"))) == 0

