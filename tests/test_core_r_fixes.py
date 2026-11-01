"""Regression tests for the core-audit R-series fixes.

R-01: album ZIP returned ciphertext (200) for a locked vault and truncated the
      response for an interrupted upload.
R-02: album ZIP buffered every file and the whole archive in memory.
R-03: catalog export/import/sync/rebuild dropped storage backend, part URI and
      proxy identity.
R-04: logout left Telegram session files on disk and kept the previous
      account's catalog/UI state in memory.
R-05: a failed proxy enqueue leaked its `_active_jobs` reservation forever.
"""
import io as _io
import json
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.db.connection import get_db
from backend.api import routes
from backend.api import auth as auth_module
from backend.transcoding import manager as manager_module
from backend.transcoding.manager import TranscodingManager
from backend.config import settings


class MockStorageClient:
    """Minimal in-memory stand-in for the Telegram storage client."""

    def __init__(self, data_by_message_id):
        self.data_by_message_id = data_by_message_id

    async def stream_part_bytes(
        self,
        message_id,
        channel_id=None,
        file_id=None,
        access_hash=None,
        offset=0,
        limit=None,
        chunk_size=64 * 1024,
    ):
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


async def _insert_file(
    db,
    file_id,
    filename,
    size_bytes,
    *,
    total_parts=1,
    upload_status="completed",
    encryption="none",
    nonce=None,
    kind="original",
    proxy_of=None,
    storage_backend="telegram",
    proxy_profile=None,
    proxy_status="none",
    created_at=None,
    mime="application/octet-stream",
    category="other",
):
    await db.execute(
        """
        INSERT INTO files (
            id, filename, mime_type, file_category, size_bytes, sha256,
            is_split, total_parts, upload_status, encryption, nonce, kind,
            proxy_of, proxy_profile, proxy_status, storage_backend, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        """,
        (
            file_id, filename, mime, category, size_bytes, "0" * 64,
            1 if total_parts > 1 else 0, total_parts, upload_status,
            encryption, nonce, kind, proxy_of, proxy_profile, proxy_status,
            storage_backend, created_at,
        ),
    )


async def _insert_part(
    db, file_id, part_index, offset, size, message_id, *, backend="telegram", uri=""
):
    await db.execute(
        """
        INSERT INTO file_parts (
            id, file_id, part_index, byte_offset, part_size_bytes,
            part_sha256, telegram_message_id, telegram_channel_id,
            storage_backend, storage_uri
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '-1001', ?, ?)
        """,
        (
            f"vp_{uuid.uuid4().hex}", file_id, part_index, offset, size,
            "0" * 64, message_id, backend, uri,
        ),
    )


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000")


# ---------------------------------------------------------------------------
# R-01 / R-02 — album ZIP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r01_locked_vault_album_zip_returns_423(monkeypatch):
    """A locked vault must not produce a 200 ZIP full of AES-CTR ciphertext."""
    from backend.crypto import vault_manager

    monkeypatch.setattr(vault_manager, "get_key", lambda: None)

    album_id = f"alb_{uuid.uuid4().hex[:8]}"
    file_id = f"v_{uuid.uuid4().hex}"
    payload = b"ciphertext-not-plaintext" * 4

    async with get_db() as db:
        await db.execute("INSERT INTO albums (id, name) VALUES (?, ?)", (album_id, "Locked"))
        await _insert_file(
            db, file_id, "secret.bin", len(payload),
            encryption="ctr", nonce="00" * 8,
        )
        await _insert_part(db, file_id, 0, 0, len(payload), 9001)
        await db.execute(
            "INSERT INTO file_albums (file_id, album_id) VALUES (?, ?)",
            (file_id, album_id),
        )
        await db.commit()

    monkeypatch.setattr(
        routes.storage_client, "stream_part_bytes",
        MockStorageClient({9001: payload}).stream_part_bytes,
    )

    async with _client() as client:
        res = await client.get(f"/api/v1/albums/{album_id}/download-zip")

    assert res.status_code == 423, res.text
    assert "locked" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_r01_incomplete_album_upload_returns_409_before_streaming(monkeypatch):
    """An interrupted upload must fail before a 200 ZIP body is started."""
    album_id = f"alb_{uuid.uuid4().hex[:8]}"
    file_id = f"v_{uuid.uuid4().hex}"

    async with get_db() as db:
        await db.execute("INSERT INTO albums (id, name) VALUES (?, ?)", (album_id, "Partial"))
        # Two parts expected, only one stored, upload never completed.
        await _insert_file(
            db, file_id, "partial.bin", 1000,
            total_parts=2, upload_status="uploading",
        )
        await _insert_part(db, file_id, 0, 0, 500, 9002)
        await db.execute(
            "INSERT INTO file_albums (file_id, album_id) VALUES (?, ?)",
            (file_id, album_id),
        )
        await db.commit()

    monkeypatch.setattr(
        routes.storage_client, "stream_part_bytes",
        MockStorageClient({9002: b"x" * 500}).stream_part_bytes,
    )

    async with _client() as client:
        res = await client.get(f"/api/v1/albums/{album_id}/download-zip")

    assert res.status_code == 409, res.text
    assert "interrupted" in res.json()["detail"].lower() or "uploading" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_r02_album_zip_streams_multiple_files_intact(monkeypatch):
    """The disk-backed ZIP writer still produces a valid multi-file archive."""
    album_id = f"alb_{uuid.uuid4().hex[:8]}"
    file_a = f"v_{uuid.uuid4().hex}"
    file_b = f"v_{uuid.uuid4().hex}"
    payloads = {
        9101: b"A" * (1024 * 1024),
        9102: b"B" * (3 * 1024 * 1024),
    }

    async with get_db() as db:
        await db.execute("INSERT INTO albums (id, name) VALUES (?, ?)", (album_id, "Big"))
        await _insert_file(db, file_a, "a.bin", len(payloads[9101]))
        await _insert_file(db, file_b, "b.bin", len(payloads[9102]))
        await _insert_part(db, file_a, 0, 0, len(payloads[9101]), 9101)
        await _insert_part(db, file_b, 0, 0, len(payloads[9102]), 9102)
        await db.execute(
            "INSERT INTO file_albums (file_id, album_id) VALUES (?, ?)",
            (file_a, album_id),
        )
        await db.execute(
            "INSERT INTO file_albums (file_id, album_id) VALUES (?, ?)",
            (file_b, album_id),
        )
        await db.commit()

    monkeypatch.setattr(
        routes.storage_client, "stream_part_bytes",
        MockStorageClient(payloads).stream_part_bytes,
    )

    async with _client() as client:
        res = await client.get(f"/api/v1/albums/{album_id}/download-zip")

    assert res.status_code == 200, res.text[:300]
    with zipfile.ZipFile(_io.BytesIO(res.content)) as zf:
        assert sorted(zf.namelist()) == ["a.bin", "b.bin"]
        assert zf.read("a.bin") == payloads[9101]
        assert zf.read("b.bin") == payloads[9102]


# ---------------------------------------------------------------------------
# R-03 — catalog backup/restore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r03_catalog_roundtrip_preserves_storage_and_proxy(monkeypatch):
    """Export -> import must keep S3/NAS pointers and proxy identity."""
    async def _no_sync(delay_seconds: float = 0.0):
        return None

    monkeypatch.setattr(routes, "sync_master_catalog", _no_sync)

    original_id = f"v_{uuid.uuid4().hex}"
    proxy_id = f"v_{uuid.uuid4().hex}"

    async with get_db() as db:
        await _insert_file(
            db, original_id, "raw.mov", 2048,
            storage_backend="s3",
            created_at="2026-01-01T00:00:00+00:00",
        )
        await _insert_part(
            db, original_id, 0, 0, 2048, 9201,
            backend="s3", uri="s3://bucket/originals/raw.mov",
        )
        await _insert_file(
            db, proxy_id, "raw.mov.proxy.mp4", 1024,
            kind="proxy", proxy_of=original_id,
            proxy_profile="stream_v1", proxy_status="ready",
            storage_backend="nas",
            created_at="2026-01-02T00:00:00+00:00",
        )
        await _insert_part(
            db, proxy_id, 0, 0, 1024, 9202,
            backend="nas", uri="/mnt/nas/proxies/raw.mov.proxy.mp4",
        )
        await db.commit()

    async with _client() as client:
        exported = await client.get("/api/v1/vault/catalog/export")
        assert exported.status_code == 200, exported.text[:300]
        catalog = exported.json()

        by_id = {item["id"]: item for item in catalog["files"]}
        assert by_id[original_id]["storage_backend"] == "s3"
        assert by_id[original_id]["parts"][0]["storage_backend"] == "s3"
        assert by_id[original_id]["parts"][0]["storage_uri"] == "s3://bucket/originals/raw.mov"
        assert by_id[proxy_id]["kind"] == "proxy"
        assert by_id[proxy_id]["proxy_of"] == original_id
        assert by_id[proxy_id]["proxy_profile"] == "stream_v1"

        # Simulate disaster recovery into a fresh catalog.
        async with get_db() as db:
            await db.execute("DELETE FROM file_parts")
            await db.execute("DELETE FROM files")
            await db.commit()

        imported = await client.post(
            "/api/v1/vault/catalog/import",
            files={
                "catalog_file": (
                    "catalog.json",
                    json.dumps(catalog).encode("utf-8"),
                    "application/json",
                )
            },
        )
    assert imported.status_code == 200, imported.text[:300]
    assert imported.json()["restored_files"] == 2

    async with get_db() as db:
        cur = await db.execute("SELECT * FROM files WHERE id = ?", (original_id,))
        original = dict(await cur.fetchone())
        assert original["storage_backend"] == "s3"

        cur = await db.execute("SELECT * FROM files WHERE id = ?", (proxy_id,))
        proxy = dict(await cur.fetchone())
        assert proxy["kind"] == "proxy"
        assert proxy["proxy_of"] == original_id
        assert proxy["proxy_profile"] == "stream_v1"
        assert proxy["storage_backend"] == "nas"

        cur = await db.execute("SELECT * FROM file_parts WHERE file_id = ?", (original_id,))
        original_part = dict(await cur.fetchone())
        assert original_part["storage_backend"] == "s3"
        assert original_part["storage_uri"] == "s3://bucket/originals/raw.mov"

        cur = await db.execute("SELECT * FROM file_parts WHERE file_id = ?", (proxy_id,))
        proxy_part = dict(await cur.fetchone())
        assert proxy_part["storage_backend"] == "nas"
        assert proxy_part["storage_uri"] == "/mnt/nas/proxies/raw.mov.proxy.mp4"

        # Proxies must not be promoted to independent originals in the gallery.
        cur = await db.execute("SELECT COUNT(*) AS c FROM files WHERE kind = 'original'")
        assert (await cur.fetchone())["c"] == 1


# ---------------------------------------------------------------------------
# R-04 — logout cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r04_logout_removes_session_files_and_cancels_qr(monkeypatch):
    data_dir = settings.DATA_DIR
    session_files = [
        "vayom_user.session",
        "vayom_user.session-wal",
        "vayom_bot.session",
        "vayom_bot.session-shm",
        "vayom_bot.session-journal",
    ]
    for name in session_files:
        (data_dir / name).write_text("session material", encoding="utf-8")
    unrelated = data_dir / "unrelated.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    monkeypatch.setattr(settings, "clear_auth_config", lambda: None)
    monkeypatch.setattr(auth_module.storage_client, "client", None, raising=False)

    async def _no_disconnect():
        return None

    monkeypatch.setattr(auth_module.storage_client, "disconnect", _no_disconnect)

    class _FakeQRClient:
        def __init__(self):
            self.disconnected = False

        def is_connected(self):
            return True

        async def disconnect(self):
            self.disconnected = True

    qr_client = _FakeQRClient()
    auth_module._active_qr_sessions["test-session"] = {
        "client": qr_client,
        "monitor_task": None,
    }
    try:
        async with _client() as client:
            res = await client.post("/api/v1/auth/logout")
    finally:
        auth_module._active_qr_sessions.clear()

    assert res.status_code == 200, res.text
    assert set(session_files) <= set(res.json()["sessions_removed"])
    for name in session_files:
        assert not (data_dir / name).exists(), f"{name} survived logout"
    assert unrelated.exists()
    assert qr_client.disconnected is True
    assert "test-session" not in auth_module._active_qr_sessions


@pytest.mark.asyncio
async def test_r04_bot_login_ignores_stale_authorized_session(monkeypatch):
    """A different bot token must not inherit the previous bot's session."""
    stale_session = settings.DATA_DIR / "vayom_bot.session"
    stale_session.write_text("old authorized bot", encoding="utf-8")

    disconnected = False

    async def _disconnect():
        nonlocal disconnected
        disconnected = True

    monkeypatch.setattr(auth_module.storage_client, "disconnect", _disconnect)

    reconnects = 0

    async def _reconnect():
        nonlocal reconnects
        reconnects += 1

    monkeypatch.setattr(auth_module.storage_client, "reconnect", _reconnect)
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 123456)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(settings, "save_auth_config", lambda payload: None)

    instances = []

    class _FakeClient:
        def __init__(self, session_path, api_id, api_hash):
            # The stale authorization must be gone before Telethon opens it.
            self.session_existed_at_init = Path(f"{session_path}.session").exists()
            self.start_token = None
            instances.append(self)

        async def start(self, bot_token=None):
            self.start_token = bot_token

        async def disconnect(self):
            return None

        async def get_entity(self, channel_id):
            return SimpleNamespace(broadcast=False, creator=False, admin_rights=None)

    monkeypatch.setattr(auth_module, "TelegramClient", _FakeClient)

    async with _client() as client:
        res = await client.post(
            "/api/v1/auth/bot",
            json={
                "bot_token": "new-bot-token",
                "channel_id": "-1001234567890",
                "display_name": "Fresh Bot",
            },
        )

    assert res.status_code == 200, res.text
    assert disconnected is True
    assert instances and instances[0].session_existed_at_init is False
    assert instances[0].start_token == "new-bot-token"
    assert reconnects == 1


# ---------------------------------------------------------------------------
# R-05 — proxy reservation release
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r05_proxy_reservation_released_when_enqueue_fails(monkeypatch, tmp_path):
    manager = TranscodingManager()
    source = tmp_path / "video.mp4"
    source.write_bytes(b"fake video")

    async def _ffmpeg_available():
        return True

    monkeypatch.setattr(manager_module, "is_ffmpeg_available", _ffmpeg_available)

    class _FailingDB:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(manager_module, "get_db", lambda: _FailingDB())

    with pytest.raises(RuntimeError):
        await manager.enqueue_proxy("v_test", source)

    # The failed enqueue must not permanently block a later retry.
    assert "v_test" not in manager._active_jobs

    class _FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, *args, **kwargs):
            return None

        async def commit(self):
            return None

    monkeypatch.setattr(manager_module, "get_db", lambda: _FakeDB())
    await manager.enqueue_proxy("v_test", source)

    assert "v_test" in manager._active_jobs
    assert manager._queue.qsize() == 1
