import re
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from backend.main import app
from backend.api.auth import _extract_credentials_from_apps_html

client = TestClient(app)


def test_extract_credentials_regex_variations():
    """Verifies that _extract_credentials_from_apps_html correctly handles various HTML formats."""
    # Format 1: Modern Bootstrap strong tags
    html_strong = """
    <div class="form-group">
      <label class="col-md-5 control-label" for="app_api_id">App api_id:</label>
      <div class="col-md-7">
        <span class="form-control" onclick="this.select();"><strong>28471920</strong></span>
      </div>
    </div>
    <div class="form-group">
      <label class="col-md-5 control-label" for="app_api_hash">App api_hash:</label>
      <div class="col-md-7">
        <span class="form-control" onclick="this.select();"><strong>a1b2c3d4e5f60718293a4b5c6d7e8f90</strong></span>
      </div>
    </div>
    """
    api_id, api_hash = _extract_credentials_from_apps_html(html_strong)
    assert api_id == 28471920
    assert api_hash == "a1b2c3d4e5f60718293a4b5c6d7e8f90"

    # Format 2: Input field value attributes
    html_input = """
    <input type="text" name="app_api_id" id="app_api_id" value="11223344" readonly>
    <input type="text" name="app_api_hash" id="app_api_hash" value="99887766554433221100aabbccddeeff" readonly>
    """
    api_id, api_hash = _extract_credentials_from_apps_html(html_input)
    assert api_id == 11223344
    assert api_hash == "99887766554433221100aabbccddeeff"

    # Format 3: Empty / missing
    api_id, api_hash = _extract_credentials_from_apps_html("<html>No apps here</html>")
    assert api_id is None
    assert api_hash is None


def test_byok_request_code_validation():
    """Verifies phone number validation on /api/v1/auth/byok/request-code."""
    # Invalid short phone
    res = client.post("/api/v1/auth/byok/request-code", json={"phone": "123"})
    assert res.status_code == 400
    assert "Invalid phone number" in res.json()["detail"]


@pytest.mark.asyncio
async def test_byok_request_code_success():
    """Verifies successful phone submission returning random_hash."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"random_hash": "mock_hash_12345"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        res = client.post("/api/v1/auth/byok/request-code", json={"phone": "+12025550123"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["random_hash"] == "mock_hash_12345"
        assert data["phone"] == "+12025550123"


@pytest.mark.asyncio
async def test_byok_request_code_telegram_rejection():
    """Verifies Telegram error messages are cleanly surfaced."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "Flood wait! Please try again later."}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        res = client.post("/api/v1/auth/byok/request-code", json={"phone": "+12025550123"})
        assert res.status_code == 400
        assert "Flood wait" in res.json()["detail"]


@pytest.mark.asyncio
async def test_byok_extract_keys_existing_app():
    """Verifies key extraction when the user already has an existing Telegram app."""
    mock_login_resp = MagicMock()
    mock_login_resp.status_code = 200
    mock_login_resp.text = "true"

    mock_apps_resp = MagicMock()
    mock_apps_resp.status_code = 200
    mock_apps_resp.text = """
    <div>
      <input id="app_api_id" value="33445566">
      <input id="app_api_hash" value="abcdef0123456789abcdef0123456789">
    </div>
    """

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_login_resp):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_apps_resp):
            res = client.post(
                "/api/v1/auth/byok/extract-keys",
                json={
                    "phone": "+12025550123",
                    "random_hash": "mock_hash_12345",
                    "code": "my_tg_code_123"
                }
            )
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["api_id"] == 33445566
            assert data["api_hash"] == "abcdef0123456789abcdef0123456789"


@pytest.mark.asyncio
async def test_byok_extract_keys_create_app_if_none():
    """Verifies automatic app creation when the user has no existing app on my.telegram.org."""
    mock_login_resp = MagicMock()
    mock_login_resp.status_code = 200
    mock_login_resp.text = "true"

    # Step 1 /apps has creation form with CSRF hash
    mock_apps_resp = MagicMock()
    mock_apps_resp.status_code = 200
    mock_apps_resp.text = """
    <form action="/apps/create" method="POST">
      <input type="hidden" name="hash" value="csrf_token_abcdef">
      <input type="text" name="app_title">
    </form>
    """

    # Step 2 /apps/create response contains the new app
    mock_create_resp = MagicMock()
    mock_create_resp.status_code = 200
    mock_create_resp.text = """
    <div>
      <input id="app_api_id" value="77889900">
      <input id="app_api_hash" value="00112233445566778899aabbccddeeff">
    </div>
    """

    async def mock_post(url, *args, **kwargs):
        if "auth/login" in url:
            return mock_login_resp
        if "apps/create" in url:
            return mock_create_resp
        return mock_login_resp

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_apps_resp):
            res = client.post(
                "/api/v1/auth/byok/extract-keys",
                json={
                    "phone": "+12025550123",
                    "random_hash": "mock_hash_12345",
                    "code": "my_tg_code_123"
                }
            )
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["api_id"] == 77889900
            assert data["api_hash"] == "00112233445566778899aabbccddeeff"


@pytest.mark.asyncio
async def test_byok_extract_keys_invalid_code():
    """Verifies invalid confirmation code error handling."""
    mock_login_resp = MagicMock()
    mock_login_resp.status_code = 400
    mock_login_resp.text = '{"text": "Invalid confirmation code!", "field": "password"}'
    mock_login_resp.json.return_value = {"text": "Invalid confirmation code!", "field": "password"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_login_resp):
        res = client.post(
            "/api/v1/auth/byok/extract-keys",
            json={
                "phone": "+12025550123",
                "random_hash": "mock_hash_12345",
                "code": "wrong_code"
            }
        )
        assert res.status_code == 400
        assert "Invalid confirmation code" in res.json()["detail"]


@pytest.mark.asyncio
async def test_byok_extract_keys_2fa_password_detection():
    """Verifies detection of 2FA cloud password challenge on web login."""
    mock_login_resp = MagicMock()
    mock_login_resp.status_code = 200
    mock_login_resp.text = 'Two-step verification password required'
    mock_login_resp.json.side_effect = Exception("Not JSON")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_login_resp):
        res = client.post(
            "/api/v1/auth/byok/extract-keys",
            json={
                "phone": "+12025550123",
                "random_hash": "mock_hash_12345",
                "code": "valid_code_but_2fa_needed"
            }
        )
        assert res.status_code == 400
        assert "2FA Cloud Password" in res.json()["detail"]


@pytest.mark.asyncio
async def test_qr_session_2fa_expiry():
    """Verifies F-9: QR session in 2fa_required state expires when timeout passes."""
    import time
    from backend.api.auth import _active_qr_sessions

    mock_client = AsyncMock()
    mock_client.is_connected = MagicMock(return_value=True)

    session_id = "test_2fa_expiry_session"
    _active_qr_sessions[session_id] = {
        "status": "2fa_required",
        "session_expires_at": int(time.time() - 10),  # in the past
        "client": mock_client,
        "monitor_task": None
    }

    # Polling should mark it expired and disconnect client
    res = client.get(f"/api/v1/auth/qr/poll?session_id={session_id}")
    assert res.status_code == 200
    assert res.json()["status"] == "expired"
    assert session_id not in _active_qr_sessions
    assert mock_client.disconnect.called
