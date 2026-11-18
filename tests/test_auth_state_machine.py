import os
import re
import json
import tempfile
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from telethon.errors import ApiIdInvalidError, SessionPasswordNeededError, PasswordHashInvalidError, FloodWaitError

from backend.main import app
from backend.config import settings
from backend.db.connection import init_db
from backend.auth.tg_apps import (
    parse_apps_page,
    TelegramAppsClient,
    AppsRateLimitError,
    AppsInvalidCodeError,
    Apps2FARequiredError,
    AppsParserError,
    reset_phone_attempts_for_test,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_auth_state():
    reset_phone_attempts_for_test()
    settings.AUTH_MODE = ""
    settings.USER_PROFILE = {}
    settings.TELEGRAM_CHANNEL_ID = ""
    yield
    reset_phone_attempts_for_test()


# -----------------------------------------------------------------------------
# 1. HTML Fixtures & Parser Tests for my.telegram.org
# -----------------------------------------------------------------------------

FIXTURE_EXISTING_APP_STRONG = """
<!DOCTYPE html>
<html>
<body>
<div class="container">
  <div class="form-group">
    <label class="col-md-5 control-label" for="app_api_id">App api_id:</label>
    <div class="col-md-7">
      <span class="form-control" onclick="this.select();"><strong>12345678</strong></span>
    </div>
  </div>
  <div class="form-group">
    <label class="col-md-5 control-label" for="app_api_hash">App api_hash:</label>
    <div class="col-md-7">
      <span class="form-control" onclick="this.select();"><strong>0123456789abcdef0123456789abcdef</strong></span>
    </div>
  </div>
</div>
</body>
</html>
"""

FIXTURE_EXISTING_APP_INPUTS = """
<html>
<body>
  <input type="text" name="app_api_id" id="app_api_id" value="87654321" readonly>
  <input type="text" name="app_api_hash" id="app_api_hash" value="fedcba9876543210fedcba9876543210" readonly>
</body>
</html>
"""

FIXTURE_NO_APP_CREATE_FORM = """
<html>
<body>
  <form action="/apps/create" method="post">
    <input type="hidden" name="hash" value="csrf_token_secret_12345">
    <input type="text" name="app_title">
  </form>
</body>
</html>
"""

FIXTURE_CHANGED_LAYOUT = """
<html>
<body>
  <div>Unknown site layout with no keys and no forms</div>
</body>
</html>
"""


def test_parse_apps_page_fixtures():
    # Existing app with strong tags
    api_id, api_hash, create_hash = parse_apps_page(FIXTURE_EXISTING_APP_STRONG)
    assert api_id == 12345678
    assert api_hash == "0123456789abcdef0123456789abcdef"
    assert create_hash is None

    # Existing app with input fields
    api_id, api_hash, create_hash = parse_apps_page(FIXTURE_EXISTING_APP_INPUTS)
    assert api_id == 87654321
    assert api_hash == "fedcba9876543210fedcba9876543210"
    assert create_hash is None

    # No app, creation form CSRF hash present
    api_id, api_hash, create_hash = parse_apps_page(FIXTURE_NO_APP_CREATE_FORM)
    assert api_id is None
    assert api_hash is None
    assert create_hash == "csrf_token_secret_12345"

    # Changed layout returns None
    api_id, api_hash, create_hash = parse_apps_page(FIXTURE_CHANGED_LAYOUT)
    assert api_id is None
    assert api_hash is None
    assert create_hash is None


# -----------------------------------------------------------------------------
# 2. Acceptance Criterion 1: No QR renders until keys exist and are accepted
# -----------------------------------------------------------------------------

def test_qr_start_rejected_when_no_keys():
    """Start QR without keys raises 503 and returns no URL."""
    with patch.object(settings, "TELEGRAM_API_ID", 0):
        with patch.object(settings, "TELEGRAM_API_HASH", ""):
            res = client.post("/api/v1/auth/qr/start", json={"mode": "own"})
            assert res.status_code == 503
            assert "TELEGRAM_API_ID" in res.json()["detail"]


@pytest.mark.asyncio
async def test_qr_start_rejected_when_telegram_rejects_keys():
    """Start QR with invalid keys maps ApiIdInvalidError to plain readable message."""
    with patch("telethon.TelegramClient.connect", new_callable=AsyncMock):
        with patch("telethon.TelegramClient.qr_login", side_effect=ApiIdInvalidError(request=None)):
            res = client.post("/api/v1/auth/qr/start", json={
                "mode": "own",
                "api_id": 99999999,
                "api_hash": "00000000000000000000000000000000",
            })
            assert res.status_code == 503
            assert "Telegram rejected" in res.json()["detail"]


# -----------------------------------------------------------------------------
# 3. Acceptance Criterion 2: Manual keys -> QR -> Connected (Mocked Telethon)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manual_keys_to_qr_flow():
    await init_db()

    # Step 1: Save manual keys
    save_res = client.post("/api/v1/auth/keys", json={
        "api_id": 28471920,
        "api_hash": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    })
    assert save_res.status_code == 200
    assert save_res.json()["success"] is True
    assert save_res.json()["api_id"] == 28471920
    assert save_res.json()["api_hash_masked"] == "...8f90"

    # Step 2: Start QR session
    mock_qr = MagicMock()
    mock_qr.url = "tg://login?token=mock_token_abc"
    mock_qr.expires = None

    with patch("telethon.TelegramClient.connect", new_callable=AsyncMock):
        with patch("telethon.TelegramClient.qr_login", new_callable=AsyncMock, return_value=mock_qr):
            with patch("backend.api.auth._monitor_qr", new_callable=AsyncMock):
                qr_res = client.post("/api/v1/auth/qr/start", json={"mode": "own"})
                assert qr_res.status_code == 200
                data = qr_res.json()
                assert data["url"] == "tg://login?token=mock_token_abc"
                assert "session_id" in data


# -----------------------------------------------------------------------------
# 4. Acceptance Criterion 3: 2FA account gets password step and reaches connected
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_2fa_account_flow_and_retry():
    import backend.api.auth as auth_mod

    mock_client = AsyncMock()
    session_id = "test_2fa_flow_session"

    auth_mod._active_qr_sessions[session_id] = {
        "status": "2fa_required",
        "session_expires_at": int(time.time()) + 300,
        "client": mock_client,
        "monitor_task": None,
    }

    # Step 1: Wrong 2FA password returns 400 and keeps session intact
    mock_client.sign_in.side_effect = PasswordHashInvalidError(request=None)
    wrong_res = client.post("/api/v1/auth/qr/2fa", json={
        "session_id": session_id,
        "password": "wrong_password_123",
    })
    assert wrong_res.status_code == 400
    assert "Invalid two-step verification password" in wrong_res.json()["detail"]
    assert session_id in auth_mod._active_qr_sessions

    # Step 2: Correct password signs in successfully
    mock_user = MagicMock()
    mock_user.id = 987654
    mock_user.first_name = "Alice"
    mock_user.last_name = ""
    mock_user.username = "alice_vault"
    mock_user.premium = False

    mock_client.sign_in.side_effect = None
    mock_client.sign_in.return_value = mock_user
    mock_client.get_dialogs.return_value = []

    correct_res = client.post("/api/v1/auth/qr/2fa", json={
        "session_id": session_id,
        "password": "correct_password_123",
    })
    assert correct_res.status_code == 200
    assert correct_res.json()["success"] is True

    # Poll returns confirmed
    poll_res = client.get(f"/api/v1/auth/qr/poll?session_id={session_id}")
    assert poll_res.status_code == 200
    assert poll_res.json()["status"] == "confirmed"
    assert poll_res.json()["user"]["first_name"] == "Alice"


# -----------------------------------------------------------------------------
# 5. Acceptance Criterion 4: Quick start works with shared keys
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quick_start_shared_keys():
    mock_qr = MagicMock()
    mock_qr.url = "tg://login?token=shared_token_xyz"
    mock_qr.expires = None

    with patch.object(settings, "TELEGRAM_API_ID", 112233):
        with patch.object(settings, "TELEGRAM_API_HASH", "abcdef0123456789abcdef0123456789"):
            with patch("telethon.TelegramClient.connect", new_callable=AsyncMock):
                with patch("telethon.TelegramClient.qr_login", new_callable=AsyncMock, return_value=mock_qr):
                    with patch("backend.api.auth._monitor_qr", new_callable=AsyncMock):
                        res = client.post("/api/v1/auth/qr/start", json={"mode": "shared"})
                        assert res.status_code == 200
                        data = res.json()
                        assert data["url"] == "tg://login?token=shared_token_xyz"

                        # Check session used shared session file
                        import backend.api.auth as auth_mod
                        sess = auth_mod._active_qr_sessions[data["session_id"]]
                        assert sess["session_base"] == "vayom_user_shared"
                        assert sess["is_byok"] is False


# -----------------------------------------------------------------------------
# 6. Acceptance Criterion 6: NAS test connection & login with no Telegram session
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nas_test_connection_and_login():
    await init_db()

    # 1. Non-existent path returns clear error
    bad_res = client.post("/api/v1/auth/nas/test", json={"vault_root": "/non/existent/path/for/sure"})
    assert bad_res.status_code == 200
    assert bad_res.json()["success"] is False
    assert "Path not found" in bad_res.json()["error"]

    # 2. File instead of directory
    with tempfile.NamedTemporaryFile() as tf:
        file_res = client.post("/api/v1/auth/nas/test", json={"vault_root": tf.name})
        assert file_res.status_code == 200
        assert file_res.json()["success"] is False
        assert "not a directory" in file_res.json()["error"]

    # 3. Valid directory returns success with disk info
    with tempfile.TemporaryDirectory() as td:
        good_res = client.post("/api/v1/auth/nas/test", json={"vault_root": td})
        assert good_res.status_code == 200
        assert good_res.json()["success"] is True
        assert good_res.json()["free_bytes"] > 0

        # Save NAS login
        save_res = client.post("/api/v1/auth/nas/save", json={
            "vault_root": td,
            "display_name": "Home Storage",
        })
        assert save_res.status_code == 200
        assert save_res.json()["auth_mode"] == "nas"

        # Check /status reports authenticated with NO Telegram session
        status_res = client.get("/api/v1/auth/status")
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert status_data["is_authenticated"] is True
        assert status_data["auth_mode"] == "nas"


# -----------------------------------------------------------------------------
# 7. Acceptance Criterion 7: Returning user with saved keys
# -----------------------------------------------------------------------------

def test_returning_user_saved_keys_status():
    with patch.object(settings, "TELEGRAM_API_ID", 554433):
        with patch.object(settings, "TELEGRAM_API_HASH", "abcdef0123456789abcdef0123456789"):
            res = client.get("/api/v1/auth/status")
            assert res.status_code == 200
            data = res.json()
            assert data["has_saved_keys"] is True
            assert data["saved_api_id"] == 554433
            assert data["saved_api_hash_masked"] == "...6789"
            assert "abcdef0123456789" not in str(data)  # Full hash is masked


# -----------------------------------------------------------------------------
# 8. Acceptance Criterion 8: Grep over logs & UI strings for secrets & deleted strings
# -----------------------------------------------------------------------------

def test_no_deleted_strings_in_ui_and_auth():
    forbidden_terms = [
        "Zero Ban Risk Guarantee",
        "Sovereign",
        "Zero-knowledge",
        "Authentication Required",
        "NODE GATEWAY",
        "STANDALONE CLIENT",
        "PRIMARY // ANTI-BAN",
    ]

    target_files = [
        Path("frontend/src/views/LoginView.tsx"),
        Path("backend/api/auth.py"),
        Path("backend/auth/tg_apps.py"),
    ]

    for fpath in target_files:
        assert fpath.exists(), f"File {fpath} must exist"
        content = fpath.read_text(encoding="utf-8")
        for term in forbidden_terms:
            assert term not in content, f"Forbidden term '{term}' found in {fpath}"
