"""Regression tests for the second remediation batch (High severity).

H-5: CORS `expose_headers` was removed, so cross-origin XHR could not read
     `Retry-After` and resumed uploading inside an active FloodWait.
H-1: the watch-folder auto-deleted the local source after a *mock* (offline)
     upload, destroying the only real copy of the bytes.
H-2: `recover_stale_tasks` ignored `proxy_status='queued'`, leaving proxies
     spinning forever after a crash.
H-3: any exception raised while recording a proxy failure killed the worker
     loop silently; every later proxy stayed in 'queued'.
H-8: QR onboarding popped the session before the storage reconnect succeeded,
     so a transient failure lost the double-create guard and produced a second
     vault channel.
M-1: multi-part videos were probed from a truncated part 0 (no metadata).
M-2: chunked single-part videos bypassed the FastStart remux.
"""
import asyncio
import time
import uuid
import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.db.connection import get_db
from backend.api import routes
from backend.api import auth as auth_module
from backend.transcoding import manager as manager_module


# ---------------------------------------------------------------------------
# H-5
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_h5_retry_after_is_exposed_cross_origin():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        res = await client.get(
            "/api/v1/health",
            headers={"Origin": "http://localhost:1420"},
        )

    exposed = res.headers.get("access-control-expose-headers", "").lower()
    assert "retry-after" in exposed, f"Retry-After is not readable cross-origin (got {exposed!r})"
    # Narrow allowlist, not a blanket wildcard.
    assert exposed.strip() != "*"


# ---------------------------------------------------------------------------
# H-1
# ---------------------------------------------------------------------------

async def _make_watch_folder(tmpdir: str) -> str:
    folder_id = f"wf_{uuid.uuid4().hex[:12]}"
    async with get_db() as db:
        await db.execute(
            "INSERT INTO watch_folders (id, path, label, auto_delete_local, is_active) VALUES (?, ?, ?, 0, 1)",
            (folder_id, tmpdir, "H-1 Test Folder"),
        )
        await db.commit()
    return folder_id


@pytest.mark.asyncio
async def test_h1_mock_upload_never_auto_deletes_local_file(monkeypatch):
    from backend.workstation.watcher import workstation_watcher

    async def fake_sync_catalog():
        return None

    monkeypatch.setattr(workstation_watcher, "_sync_catalog", fake_sync_catalog)

    async def mock_upload_part(*args, **kwargs):
        return {
            "message_id": 999001,
            "channel_id": "mock",
            "file_id": "mock-999001",
            "access_hash": "0",
            "mock": True,
        }

    monkeypatch.setattr(
        "backend.workstation.watcher.storage_client.upload_part", mock_upload_part
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        folder_id = await _make_watch_folder(tmpdir)
        target = Path(tmpdir) / "offline_doc.txt"
        target.write_bytes(b"the only copy of this data")
        old = time.time() - 10
        import os
        os.utime(target, (old, old))

        await workstation_watcher.process_file(folder_id, target, auto_delete_local=True)

        assert target.exists(), "local source was deleted even though it never reached Telegram"

        async with get_db() as db:
            cur = await db.execute(
                "SELECT id FROM files WHERE filename = ? AND is_deleted = 0",
                ("offline_doc.txt",),
            )
            assert await cur.fetchone() is not None, "mock-mode ingestion should remain usable"


@pytest.mark.asyncio
async def test_h1_real_upload_still_auto_deletes(monkeypatch):
    """The guard must be specific to mock uploads, not disable auto-delete."""
    from backend.workstation.watcher import workstation_watcher

    async def fake_sync_catalog():
        return None

    monkeypatch.setattr(workstation_watcher, "_sync_catalog", fake_sync_catalog)

    async def real_upload_part(*args, **kwargs):
        return {
            "message_id": 999002,
            "channel_id": "-100123",
            "file_id": "424242",
            "access_hash": "0",
            "mock": False,
        }

    monkeypatch.setattr(
        "backend.workstation.watcher.storage_client.upload_part", real_upload_part
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        folder_id = await _make_watch_folder(tmpdir)
        target = Path(tmpdir) / "real_doc.txt"
        target.write_bytes(b"bytes that really reached telegram")
        old = time.time() - 10
        import os
        os.utime(target, (old, old))

        await workstation_watcher.process_file(folder_id, target, auto_delete_local=True)

        assert not target.exists(), "a real Telegram upload should still honour auto-delete"


# ---------------------------------------------------------------------------
# H-2 / H-3
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_h2_recover_marks_stale_queued_jobs_failed():
    file_id = f"v_{uuid.uuid4().hex}"
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (
                id, filename, mime_type, file_category, size_bytes, sha256,
                is_split, total_parts, upload_status, encryption, kind, proxy_status
            ) VALUES (?, 'queued.mp4', 'video/mp4', 'video', 10, ?, 0, 1,
                      'completed', 'none', 'original', 'queued')
            """,
            (file_id, "0" * 64),
        )
        await db.commit()

    await manager_module.transcoding_manager.recover_stale_tasks()

    async with get_db() as db:
        cur = await db.execute("SELECT proxy_status FROM files WHERE id = ?", (file_id,))
        row = await cur.fetchone()
    assert row is not None and row[0] == "failed", f"stale queued job was not recovered: {row}"


@pytest.mark.asyncio
async def test_h3_worker_loop_survives_failure_bookkeeping(monkeypatch):
    """
    Isolates the loop fix: when marking a job 'failed' itself raises, the loop
    must still pick up the next job (no supervisor involved here).
    """
    manager = manager_module.TranscodingManager()
    manager._running = True

    attempts = []

    async def boom(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("encoder exploded")

    async def broken_get_db(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(manager, "_process_job", boom)
    monkeypatch.setattr(manager_module, "get_db", broken_get_db)

    await manager._queue.put({"original_id": "v_one", "source_path": Path("/nope1")})
    await manager._queue.put({"original_id": "v_two", "source_path": Path("/nope2")})

    loop_task = asyncio.create_task(manager._process_queue_loop())
    deadline = time.monotonic() + 2
    while len(attempts) < 2 and time.monotonic() < deadline and not loop_task.done():
        await asyncio.sleep(0.02)

    survived = len(attempts) == 2 and not loop_task.done()

    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    assert survived, (
        f"worker loop stopped after the first failure (processed {len(attempts)}/2 jobs, "
        f"done={loop_task.done()})"
    )


@pytest.mark.asyncio
async def test_h3_supervisor_restarts_dead_worker(monkeypatch):
    """A worker that somehow dies anyway must be brought back by the supervisor."""
    manager = manager_module.TranscodingManager()

    async def dying_loop():
        raise RuntimeError("catastrophic worker death")

    monkeypatch.setattr(manager, "_process_queue_loop", dying_loop)

    manager._running = True
    manager._spawn_worker()

    deadline = time.monotonic() + 2
    while manager._worker_restarts < 1 and time.monotonic() < deadline:
        await asyncio.sleep(0.02)

    restarts = manager._worker_restarts
    manager._running = False
    if manager._worker_task and not manager._worker_task.done():
        manager._worker_task.cancel()
        try:
            await manager._worker_task
        except (asyncio.CancelledError, RuntimeError):
            pass

    assert restarts >= 1, "supervisor did not restart the dead worker"


# ---------------------------------------------------------------------------
# H-8
# ---------------------------------------------------------------------------

class _FakeQrClient:
    async def disconnect(self):
        return None

    async def get_entity(self, *args, **kwargs):
        raise RuntimeError("offline")

    async def __call__(self, *args, **kwargs):
        raise AssertionError("channel must not be created twice")


@pytest.mark.asyncio
async def test_h8_failed_reconnect_preserves_qr_session(monkeypatch):
    monkeypatch.setattr(auth_module.settings, "save_auth_config", lambda *a, **k: None)

    session_id = f"qr_{uuid.uuid4().hex[:12]}"
    session = {
        "status": "confirmed",
        "client": _FakeQrClient(),
        "user": {"first_name": "Test", "id": 1},
        "confirmed_at": time.time() - 60,  # past the 10 s anti-ban window
        "created_channel_id": "-100999888777",
    }
    auth_module._active_qr_sessions[session_id] = session

    async def failing_reconnect(*args, **kwargs):
        raise RuntimeError("telegram unreachable")

    monkeypatch.setattr(auth_module.storage_client, "reconnect", failing_reconnect)

    body = auth_module.QRFinalizeRequest(
        session_id=session_id, display_name="Test", action="auto_create"
    )

    with pytest.raises(auth_module.HTTPException) as exc_info:
        await auth_module.finalize_qr_login(body)

    assert exc_info.value.status_code == 503
    assert session_id in auth_module._active_qr_sessions, "session was discarded on a retryable failure"
    assert session["created_channel_id"] == "-100999888777", "double-create guard was lost"

    # Retry succeeds -> the same channel is reused and the session is consumed.
    created_channels = []

    async def ok_reconnect(*args, **kwargs):
        created_channels.append(True)

    monkeypatch.setattr(auth_module.storage_client, "reconnect", ok_reconnect)

    result = await auth_module.finalize_qr_login(body)
    assert result["success"] is True
    assert result["channel_id"] == "-100999888777"
    assert session_id not in auth_module._active_qr_sessions


# ---------------------------------------------------------------------------
# M-1 / M-2
# ---------------------------------------------------------------------------

async def _init_upload(client: AsyncClient, filename: str, size: int, total_parts: int) -> str:
    res = await client.post(
        "/api/v1/upload/init",
        data={
            "filename": filename,
            "total_size": str(size),
            "total_parts": str(total_parts),
            "mime_type": "video/mp4",
            "encryption": "none",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["file_id"]


async def _upload_chunk(client: AsyncClient, file_id: str, part_index: int, total_parts: int, payload: bytes, filename: str, total_size: int):
    res = await client.post(
        "/api/v1/upload/chunk",
        data={
            "file_id": file_id,
            "part_index": str(part_index),
            "total_parts": str(total_parts),
            "byte_offset": str(part_index * len(payload)),
            "filename": filename,
            "total_size": str(total_size),
        },
        files={"chunk": (filename, payload, "video/mp4")},
    )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_m1_multipart_video_skips_truncated_part0_probe(monkeypatch):
    """Part 0 of a multi-part video has no moov atom; probing it is wasted work."""
    async def fake_upload_part(*args, **kwargs):
        return {"message_id": 555001, "channel_id": "-100123", "file_id": "1", "access_hash": "0", "mock": False}

    async def noop_sync():
        return None

    probed = []
    scheduled = []

    async def fake_thumb(*args, **kwargs):
        probed.append("thumb")
        return True

    async def fake_meta(*args, **kwargs):
        probed.append("meta")
        return {}

    monkeypatch.setattr(routes.storage_client, "upload_part", fake_upload_part)
    monkeypatch.setattr(routes, "sync_master_catalog", noop_sync)
    monkeypatch.setattr(routes, "generate_video_thumbnail", fake_thumb)
    monkeypatch.setattr(routes, "extract_video_metadata", fake_meta)
    monkeypatch.setattr(routes, "_schedule_deferred_video_metadata", lambda fid: scheduled.append(fid))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        file_id = await _init_upload(client, "big.mp4", 2048, total_parts=2)
        await _upload_chunk(client, file_id, 0, 2, b"\x00" * 1024, "big.mp4", 2048)

        assert probed == [], f"multi-part part 0 must not be probed (got {probed})"
        assert scheduled == [], "metadata pass must wait for completion"

        await _upload_chunk(client, file_id, 1, 2, b"\x00" * 1024, "big.mp4", 2048)

    assert scheduled == [file_id], "deferred metadata pass was not scheduled on completion"


@pytest.mark.asyncio
async def test_m1_deferred_pass_fills_in_video_metadata(monkeypatch):
    file_id = f"v_{uuid.uuid4().hex}"
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (
                id, filename, mime_type, file_category, size_bytes, sha256,
                is_split, total_parts, upload_status, encryption, kind, proxy_status
            ) VALUES (?, 'deferred.mp4', 'video/mp4', 'video', 10, ?, 1, 2,
                      'completed', 'none', 'original', 'none')
            """,
            (file_id, "0" * 64),
        )
        await db.commit()

    async def fake_thumb(url, out_path):
        assert url.startswith("http://127.0.0.1:")
        return True

    async def fake_meta(url):
        assert url.startswith("http://127.0.0.1:")
        return {
            "dimensions": {"width": 3840, "height": 2160},
            "duration_seconds": 92.5,
            "codec": "HEVC",
            "container": "mov,mp4",
            "bitrate": 8_000_000,
        }

    monkeypatch.setattr(routes, "generate_video_thumbnail", fake_thumb)
    monkeypatch.setattr(routes, "extract_video_metadata", fake_meta)

    await routes._deferred_video_metadata_pass(file_id)

    async with get_db() as db:
        cur = await db.execute(
            "SELECT width, height, duration_s, codec, has_thumbnail FROM files WHERE id = ?",
            (file_id,),
        )
        row = await cur.fetchone()

    assert row[0] == 3840 and row[1] == 2160
    assert row[2] == 92.5 and row[3] == "HEVC"
    assert row[4] == 1


@pytest.mark.asyncio
async def test_m2_single_part_chunked_video_is_faststart_remuxed(monkeypatch):
    async def fake_upload_part(target, caption="", **kwargs):
        # The uploaded target must be the remuxed staging file.
        from pathlib import Path as _P
        p = _P(target) if not hasattr(target, "read") else None
        if p is not None:
            assert p.read_bytes() == b"REMUXED-BYTES"
        return {"message_id": 555002, "channel_id": "-100123", "file_id": "2", "access_hash": "0", "mock": False}

    async def noop_sync():
        return None

    calls = []

    async def fake_faststart(input_path, output_path):
        calls.append((str(input_path), str(output_path)))
        Path(output_path).write_bytes(b"REMUXED-BYTES")
        return True

    monkeypatch.setattr(routes.storage_client, "upload_part", fake_upload_part)
    monkeypatch.setattr(routes, "sync_master_catalog", noop_sync)
    monkeypatch.setattr(routes, "apply_mp4_faststart", fake_faststart)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        file_id = await _init_upload(client, "single.mp4", 2048, total_parts=1)
        await _upload_chunk(client, file_id, 0, 1, b"ORIGINAL-BYTES", "single.mp4", 2048)

    assert len(calls) == 1, "FastStart was not applied to a single-part chunked video"

    async with get_db() as db:
        cur = await db.execute(
            "SELECT part_size_bytes FROM file_parts WHERE file_id = ?", (file_id,)
        )
        row = await cur.fetchone()
    assert row is not None and row[0] == len(b"REMUXED-BYTES")


@pytest.mark.asyncio
async def test_m2_non_video_is_not_remuxed(monkeypatch):
    async def fake_upload_part(*args, **kwargs):
        return {"message_id": 555003, "channel_id": "-100123", "file_id": "3", "access_hash": "0", "mock": False}

    async def noop_sync():
        return None

    called = []

    async def fake_faststart(*args, **kwargs):
        called.append(True)
        return True

    monkeypatch.setattr(routes.storage_client, "upload_part", fake_upload_part)
    monkeypatch.setattr(routes, "sync_master_catalog", noop_sync)
    monkeypatch.setattr(routes, "apply_mp4_faststart", fake_faststart)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        res = await client.post(
            "/api/v1/upload/init",
            data={
                "filename": "notes.txt",
                "total_size": "20",
                "total_parts": "1",
                "mime_type": "text/plain",
                "encryption": "none",
            },
        )
        file_id = res.json()["file_id"]
        chunk = await client.post(
            "/api/v1/upload/chunk",
            data={
                "file_id": file_id,
                "part_index": "0",
                "total_parts": "1",
                "byte_offset": "0",
                "filename": "notes.txt",
                "total_size": "20",
            },
            files={"chunk": ("notes.txt", b"plain text payload!!", "text/plain")},
        )

    assert chunk.status_code == 200, chunk.text
    assert called == [], "FastStart must only run for videos"
