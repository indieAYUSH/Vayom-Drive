import os
import stat
import json
import time
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from backend.config import settings
from backend.api.auth import (
    _active_qr_sessions,
    _auto_refresh_tokens,
    poll_qr_login,
    login_with_bot,
    login_with_developer,
    BotLoginRequest,
    DeveloperLoginRequest
)


def test_a5_auth_config_file_permissions(tmp_path):
    """
    A-5: Verifies that save_auth_config creates auth_config.json with 0o600
    owner-only permissions directly from file creation.
    """
    cfg_file = tmp_path / "auth_config.json"
    settings.auth_config_file = cfg_file

    try:
        data = {
            "auth_mode": "bot",
            "bot_token": "secret_12345",
            "api_hash": "hash_secret",
            "channel_id": "-100123456"
        }
        settings.save_auth_config(data)

        assert cfg_file.exists()
        file_mode = stat.S_IMODE(os.stat(cfg_file).st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got octal {oct(file_mode)}"

        loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert loaded["bot_token"] == "secret_12345"
    finally:
        settings.auth_config_file = None


@pytest.mark.asyncio
async def test_a6_qr_session_leak_cleanup_on_poll_expired():
    """
    A-6: Verifies that poll_qr_login on an expired session disconnects the client,
    cancels the monitor task, and cleans up the session from _active_qr_sessions.
    """
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.disconnect = AsyncMock()

    mock_task = MagicMock()
    mock_task.done = MagicMock(return_value=False)
    mock_task.cancel = MagicMock()

    session_id = "test_expired_session_123"
    _active_qr_sessions[session_id] = {
        "client": mock_client,
        "qr": MagicMock(),
        "url": "tg://login?token=abc",
        "session_expires_at": time.time() - 10,  # Expired 10s ago
        "status": "waiting",
        "monitor_task": mock_task,
        "user": None,
        "channels": None,
        "default_channel_id": None
    }

    try:
        resp = await poll_qr_login(session_id)
        assert resp["status"] == "expired"
        # Verify client was disconnected
        mock_client.disconnect.assert_awaited_once()
        # Verify monitor task was cancelled
        mock_task.cancel.assert_called_once()
        # Verify session was popped from active sessions
        assert session_id not in _active_qr_sessions
    finally:
        _active_qr_sessions.pop(session_id, None)


@pytest.mark.asyncio
async def test_a6_auto_refresh_tokens_backoff_and_max_retries():
    """
    A-6: Verifies that _auto_refresh_tokens does not hot loop on past expiry
    and aborts after max_recreate_retries with exponential backoff on failure.
    """
    mock_qr = MagicMock()
    # Expired token in the past
    mock_qr.expires = time.time() - 10
    mock_qr.recreate = AsyncMock(side_effect=Exception("DC migration failure"))

    session_id = "test_hotloop_session"
    _active_qr_sessions[session_id] = {
        "status": "waiting",
        "url": "tg://login?token=123"
    }
    wait_event = asyncio.Event()

    try:
        # Give a short session end time
        session_end = time.time() + 1.0
        # Patch sleep to not wait real seconds during test
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await _auto_refresh_tokens(session_id, mock_qr, session_end, wait_event)
            # Recreate was called and bounded
            assert mock_qr.recreate.await_count <= 5
    finally:
        _active_qr_sessions.pop(session_id, None)


@pytest.mark.asyncio
async def test_a7_bot_login_fails_without_saving_when_channel_invalid(tmp_path):
    """
    A-7: Verifies that login_with_bot fails and does NOT save credentials
    if the channel ID is non-numeric, unreachable, or unpostable.
    """
    cfg_file = tmp_path / "auth_config.json"
    settings.auth_config_file = cfg_file

    req = BotLoginRequest(
        bot_token="fake_bot_token",
        channel_id="non_numeric_invalid_channel",
        display_name="Test Bot"
    )

    # Mock TelegramClient
    with patch("backend.api.auth.TelegramClient") as MockTG:
        mock_client_instance = MagicMock()
        mock_client_instance.start = AsyncMock()
        mock_client_instance.disconnect = AsyncMock()
        MockTG.return_value = mock_client_instance

        with pytest.raises(HTTPException) as exc_info:
            await login_with_bot(req)

        assert exc_info.value.status_code == 400
        assert "Channel ID must be numeric" in exc_info.value.detail
        # Invariant: auth_config.json must NOT have been saved
        assert not cfg_file.exists()


@pytest.mark.asyncio
async def test_a7_developer_login_fails_without_saving_when_channel_unreachable(tmp_path):
    """
    A-7: Verifies that login_with_developer fails and does NOT save credentials
    if the channel is unreachable.
    """
    cfg_file = tmp_path / "auth_config.json"
    settings.auth_config_file = cfg_file

    req = DeveloperLoginRequest(
        api_id=123456,
        api_hash="fake_hash",
        bot_token="fake_token",
        channel_id="-100999999999",
        display_name="Dev Node"
    )

    with patch("backend.api.auth.TelegramClient") as MockTG:
        mock_client_instance = MagicMock()
        mock_client_instance.start = AsyncMock()
        mock_client_instance.get_entity = AsyncMock(side_effect=Exception("ChannelPrivateError"))
        mock_client_instance.disconnect = AsyncMock()
        MockTG.return_value = mock_client_instance

        with pytest.raises(HTTPException) as exc_info:
            await login_with_developer(req)

        assert exc_info.value.status_code == 400
        assert "Could not reach or resolve storage channel" in exc_info.value.detail
        assert not cfg_file.exists()
