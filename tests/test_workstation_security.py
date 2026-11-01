import asyncio
import os
import tempfile
import uuid
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.db.connection import get_db, init_db
from backend.workstation.security import (
    validate_watch_folder_path,
    is_safe_file_path,
    is_contained,
)
from backend.workstation.watcher import workstation_watcher

client = TestClient(app)


def test_workstation_unauthenticated_access_rejected(tmp_path, monkeypatch):
    """Verifies that all workstation endpoints strictly reject unauthenticated requests with 401."""
    fake_config = tmp_path / "nonexistent_auth_config.json"
    monkeypatch.setattr(settings, "auth_config_file", fake_config)
    monkeypatch.setattr(settings, "AUTH_MODE", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHANNEL_ID", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")

    # 1. GET /watch-folders -> 401
    resp = client.get("/api/v1/workstation/watch-folders")
    assert resp.status_code == 401
    assert "Authentication required" in resp.json()["detail"]

    # 2. POST /watch-folders -> 401
    resp = client.post(
        "/api/v1/workstation/watch-folders",
        json={"path": str(tmp_path)}
    )
    assert resp.status_code == 401

    # 3. PATCH /watch-folders/{id} -> 401
    resp = client.patch(
        "/api/v1/workstation/watch-folders/wf_123",
        json={"label": "New"}
    )
    assert resp.status_code == 401

    # 4. DELETE /watch-folders/{id} -> 401
    resp = client.delete("/api/v1/workstation/watch-folders/wf_123")
    assert resp.status_code == 401

    # 5. POST /watch-folders/{id}/scan -> 401
    resp = client.post("/api/v1/workstation/watch-folders/wf_123/scan")
    assert resp.status_code == 401

    # 6. GET /events -> 401
    resp = client.get("/api/v1/workstation/events")
    assert resp.status_code == 401


def test_workstation_auth_with_tokens(tmp_path, monkeypatch):
    """Verifies authentication via Authorization Bearer token and X-API-Key."""
    fake_config = tmp_path / "nonexistent_auth_config.json"
    monkeypatch.setattr(settings, "auth_config_file", fake_config)
    monkeypatch.setattr(settings, "AUTH_MODE", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHANNEL_ID", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "SESSION_SECRET", "test_session_secret_xyz")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "test_admin_password_abc")

    # Valid Bearer token
    resp = client.get(
        "/api/v1/workstation/watch-folders",
        headers={"Authorization": "Bearer test_session_secret_xyz"}
    )
    assert resp.status_code == 200

    # Valid X-API-Key
    resp = client.get(
        "/api/v1/workstation/watch-folders",
        headers={"X-API-Key": "test_admin_password_abc"}
    )
    assert resp.status_code == 200

    # Invalid token -> 401
    resp = client.get(
        "/api/v1/workstation/watch-folders",
        headers={"Authorization": "Bearer totally_wrong_secret"}
    )
    assert resp.status_code == 401
    assert "Invalid authentication credentials" in resp.json()["detail"]


def test_workstation_untrusted_cross_origin_blocked(tmp_path, monkeypatch):
    """Verifies that cross-origin requests from untrusted web origins are blocked with 403."""
    # Even if node is authenticated:
    monkeypatch.setattr(settings, "AUTH_MODE", "bot")
    monkeypatch.setattr(settings, "TELEGRAM_CHANNEL_ID", "-100123456789")

    resp = client.get(
        "/api/v1/workstation/watch-folders",
        headers={"Origin": "https://malicious-website.com"}
    )
    assert resp.status_code == 403
    assert "Cross-origin access from this origin is forbidden" in resp.json()["detail"]

    # Allowed local origin succeeds
    resp = client.get(
        "/api/v1/workstation/watch-folders",
        headers={"Origin": "http://localhost:5173"}
    )
    assert resp.status_code == 200


def test_watch_folder_path_validation_rejects_sensitive_locations(tmp_path, monkeypatch):
    """Tests that validate_watch_folder_path rejects filesystem root, /etc, home dirs, DATA_DIR, ROOT_DIR."""
    monkeypatch.setattr(settings, "AUTH_MODE", "bot")
    monkeypatch.setattr(settings, "TELEGRAM_CHANNEL_ID", "-100123456789")

    # 1. Reject filesystem root '/'
    resp = client.post("/api/v1/workstation/watch-folders", json={"path": "/"})
    assert resp.status_code == 400
    assert "root directory" in resp.json()["detail"].lower()

    # 2. Reject '/etc'
    if Path("/etc").exists():
        resp = client.post("/api/v1/workstation/watch-folders", json={"path": "/etc"})
        assert resp.status_code == 400
        assert "system directory" in resp.json()["detail"].lower() or "etc" in resp.json()["detail"].lower()

    # 3. Reject user home directory root
    home_dir = str(Path.home())
    resp = client.post("/api/v1/workstation/watch-folders", json={"path": home_dir})
    assert resp.status_code == 400
    assert "home directory cannot be watched directly" in resp.json()["detail"].lower()

    # 4. Reject settings.DATA_DIR
    resp = client.post("/api/v1/workstation/watch-folders", json={"path": str(settings.DATA_DIR)})
    assert resp.status_code == 400
    assert "data directory" in resp.json()["detail"].lower()

    # 5. Reject subfolder inside settings.DATA_DIR (e.g. data/uploads)
    resp = client.post("/api/v1/workstation/watch-folders", json={"path": str(settings.UPLOAD_TEMP_DIR)})
    assert resp.status_code == 400
    assert "data directory" in resp.json()["detail"].lower()

    # 6. Reject settings.ROOT_DIR (project root)
    resp = client.post("/api/v1/workstation/watch-folders", json={"path": str(settings.ROOT_DIR)})
    assert resp.status_code == 400
    assert "project directory" in resp.json()["detail"].lower() or "data directory" in resp.json()["detail"].lower()

    # 7. Reject hidden directories (e.g. .ssh)
    fake_ssh = tmp_path / ".ssh"
    fake_ssh.mkdir(exist_ok=True)
    resp = client.post("/api/v1/workstation/watch-folders", json={"path": str(fake_ssh)})
    assert resp.status_code == 400
    assert "hidden" in resp.json()["detail"].lower()


def test_watch_folder_path_validation_accepts_valid_subfolder(monkeypatch):
    """Tests that a legitimate media subfolder inside an allowlisted root is accepted."""
    monkeypatch.setattr(settings, "AUTH_MODE", "bot")
    monkeypatch.setattr(settings, "TELEGRAM_CHANNEL_ID", "-100123456789")

    with tempfile.TemporaryDirectory() as td:
        valid_folder = Path(td) / "camera_roll"
        valid_folder.mkdir(exist_ok=True)

        resp = client.post(
            "/api/v1/workstation/watch-folders",
            json={"path": str(valid_folder), "label": "Camera Roll"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["label"] == "Camera Roll"
        assert data["path"] == str(valid_folder.resolve())


def test_watch_folder_target_album_id_validation(monkeypatch):
    """I-17: registering or updating a watch folder with a non-existent target_album_id must 404."""
    monkeypatch.setattr(settings, "AUTH_MODE", "bot")
    monkeypatch.setattr(settings, "TELEGRAM_CHANNEL_ID", "-100123456789")

    valid_album_id = f"alb_{uuid.uuid4().hex[:8]}"
    missing_album_id = f"alb_missing_{uuid.uuid4().hex[:8]}"

    async def _seed_album():
        async with get_db() as db:
            await db.execute("INSERT INTO albums (id, name) VALUES (?, ?)", (valid_album_id, "Valid Album"))
            await db.commit()

    async def _watch_folder_exists(path: str) -> bool:
        async with get_db() as db:
            cur = await db.execute("SELECT id FROM watch_folders WHERE path = ?", (path,))
            return await cur.fetchone() is not None

    async def _cleanup(path: str):
        async with get_db() as db:
            await db.execute("DELETE FROM watch_folders WHERE path = ?", (path,))
            await db.execute("DELETE FROM albums WHERE id = ?", (valid_album_id,))
            await db.commit()

    asyncio.run(_seed_album())

    with tempfile.TemporaryDirectory() as td:
        watch_dir = Path(td) / "album_watch"
        watch_dir.mkdir(exist_ok=True)
        watch_path = str(watch_dir.resolve())

        try:
            # 1. POST with a non-existent album -> 404, folder must NOT be registered
            resp = client.post(
                "/api/v1/workstation/watch-folders",
                json={"path": watch_path, "target_album_id": missing_album_id}
            )
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Target album not found"
            assert asyncio.run(_watch_folder_exists(watch_path)) is False

            # 2. POST with a valid album -> 200 and folder registered with the album
            resp = client.post(
                "/api/v1/workstation/watch-folders",
                json={"path": watch_path, "target_album_id": valid_album_id}
            )
            assert resp.status_code == 200
            folder_id = resp.json()["id"]
            assert resp.json()["target_album_id"] == valid_album_id

            # 3. PATCH to a non-existent album -> 404
            resp = client.patch(
                f"/api/v1/workstation/watch-folders/{folder_id}",
                json={"target_album_id": missing_album_id}
            )
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Target album not found"

            # 4. PATCH to a valid album -> 200
            resp = client.patch(
                f"/api/v1/workstation/watch-folders/{folder_id}",
                json={"target_album_id": valid_album_id}
            )
            assert resp.status_code == 200

            # 5. PATCH with an empty string clears the association (non-empty check only)
            resp = client.patch(
                f"/api/v1/workstation/watch-folders/{folder_id}",
                json={"target_album_id": ""}
            )
            assert resp.status_code == 200
        finally:
            asyncio.run(_cleanup(watch_path))


def test_is_safe_file_path():
    """Unit test for is_safe_file_path checking various forbidden and permitted file paths."""
    # Forbidden: DATA_DIR files
    assert is_safe_file_path(settings.DATA_DIR / "vayom.db") is False
    assert is_safe_file_path(settings.DATA_DIR / "auth_config.json") is False
    assert is_safe_file_path(settings.UPLOAD_TEMP_DIR / "chunk.bin") is False

    # Forbidden: ROOT_DIR files
    assert is_safe_file_path(settings.ROOT_DIR / ".env") is False
    assert is_safe_file_path(settings.ROOT_DIR / "backend" / "config.py") is False

    # Forbidden: System files
    assert is_safe_file_path(Path("/etc/shadow")) is False
    assert is_safe_file_path(Path("/etc/passwd")) is False

    # Forbidden: Direct children of user home
    home = Path.home()
    assert is_safe_file_path(home / ".bashrc") is False
    assert is_safe_file_path(home / ".ssh" / "id_rsa") is False
    assert is_safe_file_path(home / ".config" / "browser" / "cookies.sqlite") is False

    # Permitted: files in subdirectories of temp or home (e.g. ~/Pictures/photo.jpg)
    with tempfile.TemporaryDirectory() as td:
        safe_file = Path(td) / "drone_footage.mp4"
        safe_file.write_text("sample content")
        assert is_safe_file_path(safe_file) is True


@pytest.mark.asyncio
async def test_watcher_refuses_unsafe_file_processing_and_deletion(monkeypatch):
    """Tests that LocalFolderWatcher.process_file refuses to ingest or delete sensitive files."""
    watcher = workstation_watcher

    # Mock storage_client.upload_part
    mock_upload = AsyncMock()
    monkeypatch.setattr("backend.storage.client.storage_client.upload_part", mock_upload)

    # 1. Attempt to process sensitive .env file
    env_file = settings.ROOT_DIR / ".env"
    if env_file.exists():
        await watcher.process_file(
            folder_id="wf_fake",
            file_path=env_file,
            auto_delete_local=True
        )
        # upload_part must NEVER have been called for .env!
        mock_upload.assert_not_called()
        # .env must still exist!
        assert env_file.exists()
