import asyncio
import os
import tempfile
import uuid
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.db.connection import get_db, init_db
from backend.workstation.watcher import LocalFolderWatcher
from backend.storage.client import storage_client
from backend.api.routes import (
    sync_master_catalog,
    get_catalog_sync_state,
    _catalog_sync_state,
    _execute_sync_with_retry,
)
from backend.streaming.router import root_router

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_db():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(init_db())
    old_auth_mode = settings.AUTH_MODE
    old_channel = settings.TELEGRAM_CHANNEL_ID
    settings.AUTH_MODE = "bot"
    settings.TELEGRAM_CHANNEL_ID = "-1001234567890"
    yield
    settings.AUTH_MODE = old_auth_mode
    settings.TELEGRAM_CHANNEL_ID = old_channel


# --- I-19: Watcher reload race condition tests ---

@pytest.mark.asyncio
async def test_i19_watcher_reload_during_folder_fetch():
    """
    I-19: Verifies that calling reload() while _get_active_folders() is awaiting
    sets _needs_reload and _reload_event, causing the loop to re-fetch immediately
    rather than waiting on an overwritten or obsolete stop event.
    """
    watcher = LocalFolderWatcher()
    fetch_count = 0
    folder_a = {"id": "f1", "path": "/tmp", "label": "tmp", "auto_delete_local": 0, "target_album_id": None}
    folder_b = {"id": "f2", "path": "/var", "label": "var", "auto_delete_local": 0, "target_album_id": None}

    original_get_active = watcher._get_active_folders

    async def mock_get_active_folders():
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            # Simulate concurrent reload() arrival during first DB query
            await watcher.reload()
            return [folder_a]
        else:
            # Second fetch picks up folder_b
            watcher._running = False
            return [folder_a, folder_b]

    watcher._get_active_folders = mock_get_active_folders
    watcher._running = True

    # Run one pass of _watch_loop logic
    loop_task = asyncio.create_task(watcher._watch_loop())
    await asyncio.wait_for(loop_task, timeout=2.0)

    # Must have looped twice because reload() was triggered during first fetch
    assert fetch_count >= 2


@pytest.mark.asyncio
async def test_i19_reload_sets_needs_reload_and_event():
    """Verifies that reload() atomically sets both _needs_reload flag and _reload_event."""
    watcher = LocalFolderWatcher()
    assert watcher._needs_reload is False
    assert watcher._reload_event.is_set() is False

    await watcher.reload()
    assert watcher._needs_reload is True
    assert watcher._reload_event.is_set() is True


# --- E-3: Catalog sync retry, state tracking, and health metrics tests ---

@pytest.mark.asyncio
async def test_e3_catalog_sync_retry_on_transient_failure():
    """
    E-3: Verifies that _execute_sync_with_retry performs bounded exponential
    retries on transient failure and updates catalog sync state to 'synced'.
    """
    call_attempts = 0

    async def mock_save_pin(data, old_pinned_id=None):
        nonlocal call_attempts
        call_attempts += 1
        if call_attempts < 2:
            raise RuntimeError("Telegram FloodWait transient error")
        return 9999

    with patch.object(storage_client, "save_and_pin_catalog", side_effect=mock_save_pin):
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await sync_master_catalog(delay_seconds=0.0)

            # Assert retry happened
            assert call_attempts == 2
            mock_sleep.assert_called_once()

            state = get_catalog_sync_state()
            assert state["status"] == "synced"
            assert state["last_error"] is None
            assert state["retry_count"] == 0
            assert state["pinned_msg_id"] == 9999
            assert state["last_synced_at"] is not None


@pytest.mark.asyncio
async def test_e3_catalog_sync_permanent_failure_marks_failed():
    """
    E-3: Verifies that when all retries are exhausted, catalog sync state is marked
    as 'failed' with the last error message recorded.
    """
    call_attempts = 0

    async def mock_save_pin_fail(data, old_pinned_id=None):
        nonlocal call_attempts
        call_attempts += 1
        raise ConnectionError(f"Telegram server offline attempt {call_attempts}")

    with patch.object(storage_client, "save_and_pin_catalog", side_effect=mock_save_pin_fail):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError):
                await sync_master_catalog(delay_seconds=0.0)

            assert call_attempts == 3
            state = get_catalog_sync_state()
            assert state["status"] == "failed"
            assert "Telegram server offline" in state["last_error"]
            assert state["retry_count"] == 3


def test_e3_metrics_and_channel_health_expose_catalog_sync():
    """
    E-3: Verifies that /system/metrics and /vault/channel-health expose
    catalog_sync details in their response payloads.
    """
    _catalog_sync_state.update({
        "status": "synced",
        "last_synced_at": "2026-10-18T20:00:00Z",
        "last_error": None,
        "pinned_msg_id": 4242,
    })

    # Test /system/metrics
    resp_metrics = client.get("/api/v1/system/metrics")
    assert resp_metrics.status_code == 200
    metrics_data = resp_metrics.json()
    assert "catalog_sync" in metrics_data
    assert metrics_data["catalog_sync"]["status"] == "synced"
    assert metrics_data["catalog_sync"]["pinned_msg_id"] == 4242

    # Test /vault/channel-health
    with patch.object(storage_client, "check_channel_health", new_callable=AsyncMock) as mock_health:
        mock_health.return_value = {"online": True, "primary_channel": {"healthy": True}}
        resp_health = client.get("/api/v1/vault/channel-health")
        assert resp_health.status_code == 200
        health_data = resp_health.json()
        assert "catalog_sync" in health_data
        assert health_data["catalog_sync"]["status"] == "synced"


# --- F-4: Dead feature surfaces retirement tests ---

def test_f4_share_links_gated_when_disabled():
    """
    F-4: Verifies that unused share-link endpoints return 404
    when ENABLE_SHARE_LINKS is disabled.
    """
    settings.ENABLE_SHARE_LINKS = False

    resp = client.get("/api/v1/shares")
    assert resp.status_code == 404
    assert "Share links feature is disabled" in resp.json()["detail"]

    resp = client.post("/api/v1/shares", json={"file_id": "any-file", "hours": 24})
    assert resp.status_code == 404
    assert "Share links feature is disabled" in resp.json()["detail"]

    resp = client.post("/api/v1/shares/fake-share-id/revoke")
    assert resp.status_code == 404
    assert "Share links feature is disabled" in resp.json()["detail"]

    resp = client.get("/api/v1/s/fake-token")
    assert resp.status_code == 404
    assert "Share links feature is disabled" in resp.json()["detail"]

    # Main redirect
    resp = client.get("/s/fake-token")
    assert resp.status_code == 404
    assert "Share links feature is disabled" in resp.json()["detail"]


def test_f4_no_hardcoded_default_credentials():
    """
    F-4: Verifies that production settings do not ship with hardcoded
    dummy credentials ('vayom123' / 'vayom_drive_secret_key_change_in_prod').
    """
    assert settings.ADMIN_PASSWORD != "vayom123"
    assert settings.SESSION_SECRET != "vayom_drive_secret_key_change_in_prod"


def test_f4_root_virtual_stream_hidden_from_schema():
    """
    F-4: Verifies that the duplicate root-level /virtual-stream/{file_id}
    route is excluded from OpenAPI schema (include_in_schema=False).
    """
    route_found = False
    for route in root_router.routes:
        if getattr(route, "path", "") == "/virtual-stream/{file_id}":
            route_found = True
            assert route.include_in_schema is False
            break
    assert route_found is True
