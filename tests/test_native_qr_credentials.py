"""Regression tests for native (packaged) QR login credentials.

The PyInstaller sidecar shipped with no ``.env`` next to the executable, so
``TELEGRAM_API_ID`` fell back to 0. ``TelegramClient(...)`` then raised
``ValueError`` and ``POST /auth/qr/start`` returned a generic HTTP 500, which
the desktop app rendered as "Could not initialize Telegram QR session" without
ever displaying a QR code.

These tests lock in:
1. A missing app credential returns an actionable 503 before the storage
   session is disconnected.
2. A Telegram connection failure is mapped to 503 instead of an opaque 500.
3. Configuration discovery honors ``VAYOM_ENV_FILE`` (and wins over the repo
   ``.env``) and tolerates malformed numeric values without crashing startup.
4. A relative ``DATA_DIR`` cannot escape to ``/data`` in packaged builds.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from backend.api import auth as auth_module
from backend import config as config_module
from backend.config import settings
from backend.config import _int_env, _resolve_data_dir
from backend.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_qr_start_without_credentials_returns_actionable_503(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 0)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "")

    disconnect_called = False

    async def _record_disconnect():
        nonlocal disconnect_called
        disconnect_called = True

    monkeypatch.setattr(auth_module.storage_client, "disconnect", _record_disconnect)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        resp = await client.post("/api/v1/auth/qr/start")

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "TELEGRAM_API_ID" in detail
    assert "TELEGRAM_API_HASH" in detail
    # A failed start must not tear down an already-authenticated storage client.
    assert disconnect_called is False


@pytest.mark.asyncio
async def test_qr_start_maps_telegram_connection_failure_to_503(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 123456)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")

    instances = []

    class _ConnectionFails:
        def __init__(self, *args, **kwargs):
            self.disconnected = False
            instances.append(self)

        async def connect(self):
            raise ConnectionError("network unavailable")

        async def disconnect(self):
            self.disconnected = True

    monkeypatch.setattr(auth_module, "TelegramClient", _ConnectionFails)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        resp = await client.post("/api/v1/auth/qr/start")

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "Could not reach Telegram" in detail
    # Raw exception text (which may contain local paths) must not leak.
    assert "network unavailable" not in detail
    assert instances and instances[0].disconnected is True


@pytest.mark.asyncio
async def test_qr_start_timeout_returns_504(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 123456)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(auth_module, "QR_HANDSHAKE_TIMEOUT_SECONDS", 0.05)

    instances = []

    class _HangingClient:
        def __init__(self, *args, **kwargs):
            self.disconnected = False
            instances.append(self)

        async def connect(self):
            import asyncio
            await asyncio.sleep(5)

        async def qr_login(self):  # pragma: no cover - never reached
            return None

        async def disconnect(self):
            self.disconnected = True

    monkeypatch.setattr(auth_module, "TelegramClient", _HangingClient)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        resp = await client.post("/api/v1/auth/qr/start")

    assert resp.status_code == 504
    assert "Timed out" in resp.json()["detail"]
    assert instances and instances[0].disconnected is True


@pytest.mark.asyncio
async def test_qr_start_invalid_credentials_returns_config_error(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 123456)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")

    class _InvalidCredentials:
        def __init__(self, *args, **kwargs):
            pass

        async def connect(self):
            raise ValueError("Your API ID or Hash cannot be empty or None.")

        async def disconnect(self):
            pass

    monkeypatch.setattr(auth_module, "TelegramClient", _InvalidCredentials)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        resp = await client.post("/api/v1/auth/qr/start")

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "rejected the configured API credentials" in detail
    assert "Your API ID" not in detail


@pytest.mark.asyncio
async def test_byok_qr_start_and_finalize_with_custom_credentials(monkeypatch, tmp_path):
    # Global environment settings have NO credentials (0 and "")
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 0)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "")
    cfg_file = tmp_path / "auth_config.json"
    monkeypatch.setattr(settings, "_custom_auth_config_file", cfg_file)

    captured_init = {}

    class _MockQRLogin:
        url = "tg://login?token=mock_sovereign_token"

    class _MockChat:
        id = 99887766
        title = "Sovereign's Vayom Vault"

    class _MockCreateRes:
        chats = [_MockChat()]

    class _MockClient:
        def __init__(self, session, api_id, api_hash):
            captured_init["api_id"] = api_id
            captured_init["api_hash"] = api_hash
            self._connected = False

        async def connect(self):
            self._connected = True

        async def qr_login(self):
            return _MockQRLogin()

        async def disconnect(self):
            self._connected = False

        def is_connected(self):
            return self._connected

        async def __call__(self, req):
            return _MockCreateRes()

        async def get_entity(self, entity):
            class _Entity:
                username = None
                usernames = None
            return _Entity()

    monkeypatch.setattr(auth_module, "TelegramClient", _MockClient)
    async def _noop():
        pass
    monkeypatch.setattr(auth_module.storage_client, "disconnect", _noop)
    monkeypatch.setattr(auth_module.storage_client, "reconnect", _noop)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        # Start BYOK QR with custom credentials
        byok_payload = {"api_id": 884422, "api_hash": "a1b2c3d4e5f60718293a4b5c6d7e8f90"}
        resp = await client.post("/api/v1/auth/qr/start", json=byok_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["url"] == "tg://login?token=mock_sovereign_token"

        # Verify client was initialized with the BYOK keys
        assert captured_init["api_id"] == 884422
        assert captured_init["api_hash"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90"

        # Mock the session transition to confirmed
        sess = auth_module._active_qr_sessions[data["session_id"]]
        sess["status"] = "confirmed"
        sess["confirmed_at"] = 0  # Bypass anti-ban idle wait
        sess["user"] = {
            "id": 987654321,
            "first_name": "Sovereign",
            "last_name": "Architect",
            "username": "sovereign_dev",
            "is_premium": True
        }

        # Finalize QR login
        fin_resp = await client.post(
            "/api/v1/auth/qr/finalize",
            json={"session_id": data["session_id"], "action": "auto_create"}
        )
        assert fin_resp.status_code == 200
        fin_data = fin_resp.json()
        assert fin_data["success"] is True

        # Check saved configuration
        import json
        saved_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved_cfg["auth_mode"] == "byok_qr"
        assert saved_cfg["api_id"] == 884422
        assert saved_cfg["api_hash"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90"
        assert saved_cfg["channel_id"] == "-10099887766"


@pytest.mark.asyncio
async def test_byok_qr_start_rejected_credentials_returns_byok_error(monkeypatch):
    class _FailingClient:
        def __init__(self, session, api_id, api_hash):
            pass

        async def connect(self):
            raise auth_module.ApiIdInvalidError(request=None)

        async def disconnect(self):
            pass

    monkeypatch.setattr(auth_module, "TelegramClient", _FailingClient)
    async def _noop():
        pass
    monkeypatch.setattr(auth_module.storage_client, "disconnect", _noop)
    monkeypatch.setattr(auth_module.storage_client, "reconnect", _noop)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        resp = await client.post(
            "/api/v1/auth/qr/start",
            json={"api_id": 11111, "api_hash": "invalidhash"}
        )

    assert resp.status_code == 503
    assert "rejected the provided API credentials" in resp.json()["detail"]
    assert "my.telegram.org" in resp.json()["detail"]


def test_int_env_ignores_invalid_values(monkeypatch):
    monkeypatch.setenv("VAYOM_TEST_INT", "not-a-number")
    assert _int_env("VAYOM_TEST_INT", 7) == 7

    monkeypatch.setenv("VAYOM_TEST_INT", " 42 ")
    assert _int_env("VAYOM_TEST_INT", 7) == 42

    monkeypatch.delenv("VAYOM_TEST_INT", raising=False)
    assert _int_env("VAYOM_TEST_INT", 7) == 7


def test_relative_data_dir_is_ignored_in_packaged_builds(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.IS_FROZEN", True)
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path / "per-user")
    # No legacy vayom.db in this launch directory.
    monkeypatch.chdir(tmp_path)

    resolved = _resolve_data_dir("./data")

    assert resolved == tmp_path / "per-user"


def test_relative_data_dir_reuses_existing_portable_vault(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.IS_FROZEN", True)
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path / "per-user")
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "portable"
    legacy.mkdir()
    (legacy / "vayom.db").write_text("", encoding="utf-8")

    resolved = _resolve_data_dir("./portable")

    assert resolved == legacy


def test_relative_nas_root_is_ignored_in_packaged_builds(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.IS_FROZEN", True)
    default = tmp_path / "data" / "nas_vault"

    resolved = config_module._resolve_local_path("./data/nas_vault", default)

    assert resolved == default


def test_frozen_discovery_does_not_load_launch_directory_env(monkeypatch, tmp_path):
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    meipass = tmp_path / "meipass"
    meipass.mkdir()
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()

    # A stray .env in the launch directory must not override embedded creds.
    (launch_dir / ".env").write_text("TELEGRAM_API_ID=0\n", encoding="utf-8")
    (launch_dir / "telegram_app.env").write_text("TELEGRAM_API_ID=0\n", encoding="utf-8")
    (meipass / "telegram_app.env").write_text("TELEGRAM_API_ID=123456\n", encoding="utf-8")

    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr("backend.config.IS_FROZEN", True)
    monkeypatch.setattr("backend.config.ROOT_DIR", exe_dir)
    monkeypatch.setattr("backend.config._platform_config_dirs", lambda: [])
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    files = config_module._configuration_files()

    assert (launch_dir / ".env") not in files
    assert (launch_dir / "telegram_app.env") not in files
    assert (meipass / "telegram_app.env") in files
    # Bundled app credentials load after every .env candidate.
    assert files.index(meipass / "telegram_app.env") > files.index(exe_dir / ".env")


def test_relative_data_dir_resolves_inside_project_in_dev(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.IS_FROZEN", False)
    monkeypatch.setattr("backend.config.ROOT_DIR", tmp_path)

    resolved = _resolve_data_dir("./data")

    assert resolved == (tmp_path / "data").resolve()


def test_vayom_env_file_is_loaded_and_wins_over_repo_dotenv(tmp_path):
    env_file = tmp_path / "telegram_app.env"
    env_file.write_text(
        "TELEGRAM_API_ID=987654\nTELEGRAM_API_HASH=abcdef0123456789\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("TELEGRAM_API_ID", None)
    env.pop("TELEGRAM_API_HASH", None)
    env["VAYOM_ENV_FILE"] = str(env_file)
    env["DATA_DIR"] = str(tmp_path / "app-data")
    # Prove malformed numeric configuration no longer crashes startup.
    env["PORT"] = "not-a-port"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from backend.config import settings; "
                "print(settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH, settings.PORT)"
            ),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "987654 abcdef0123456789 8000"
