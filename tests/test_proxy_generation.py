import pytest
import json
import uuid
import shutil
import time
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from backend.transcoding.encoder import (
    probe_best_encoder,
    probe_stream_metadata,
    _build_encoder_args,
    encode_stream_v1,
    TranscodeError,
    PROXY_PROFILE,
    ACTIVE_FFMPEG_PROCESSES
)
from backend.transcoding.manager import TranscodingManager, ProxyManager
from backend.db.connection import get_db
from backend.config import settings


@pytest.mark.asyncio
async def test_encoder_probe_and_caching():
    """Verifies that encoder probe identifies an encoder and caches the result."""
    encoder = await probe_best_encoder()
    # On this test environment, ffmpeg has libx264 or hardware encoders
    assert encoder is not None
    assert isinstance(encoder, str)

    # Calling again returns cached encoder
    cached = await probe_best_encoder()
    assert cached == encoder


def test_build_encoder_args():
    """Verifies encoder args include FastStart, keyframe interval ~2s, maxrate 5M."""
    fps = 30.0
    args_nvenc = _build_encoder_args("h264_nvenc", fps)
    assert "-pix_fmt" in args_nvenc
    assert "yuv420p" in args_nvenc
    assert "-g" in args_nvenc
    assert "60" in args_nvenc
    assert "-sc_threshold" in args_nvenc
    assert "0" in args_nvenc
    assert "-maxrate" in args_nvenc
    assert ("5M" in args_nvenc or "1200k" in args_nvenc)

    args_x264 = _build_encoder_args("libx264", fps)
    assert "-crf" in args_x264
    assert ("26" in args_x264 or "28" in args_x264)
    assert "-maxrate" in args_x264
    assert ("5M" in args_x264 or "1200k" in args_x264)


@pytest.mark.asyncio
async def test_manager_startup_stale_recovery():
    """Verifies recovery marks stuck encoding/uploading jobs as failed on startup."""
    manager = TranscodingManager()

    file_id1 = f"v_stale_{uuid.uuid4().hex[:10]}"
    file_id2 = f"v_stale_{uuid.uuid4().hex[:10]}"

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'stale1.mp4', 'video/mp4', 'video', 1000, 'h1', 0, 1, 'completed', 'original', 'encoding')
            """,
            (file_id1,)
        )
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'stale2.mp4', 'video/mp4', 'video', 1000, 'h2', 0, 1, 'completed', 'original', 'uploading')
            """,
            (file_id2,)
        )
        await db.commit()

    await manager.recover_stale_tasks()

    async with get_db() as db:
        cur = await db.execute("SELECT id, proxy_status FROM files WHERE id IN (?, ?)", (file_id1, file_id2))
        rows = {r[0]: r[1] for r in await cur.fetchall()}
        assert rows[file_id1] == "failed"
        assert rows[file_id2] == "failed"


@pytest.mark.asyncio
async def test_manager_source_not_found():
    """Verifies manager marks proxy_status as unavailable_no_source if source does not exist."""
    manager = TranscodingManager()
    file_id = f"v_nosrc_{uuid.uuid4().hex[:10]}"

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'nosrc.mp4', 'video/mp4', 'video', 1000, 'h1', 0, 1, 'completed', 'original', 'none')
            """,
            (file_id,)
        )
        await db.commit()

    missing_path = Path("/tmp/nonexistent_video_path_xyz.mp4")
    await manager.enqueue_proxy(file_id, missing_path)

    async with get_db() as db:
        cur = await db.execute("SELECT proxy_status FROM files WHERE id = ?", (file_id,))
        status = (await cur.fetchone())[0]
        assert status == "unavailable_no_source"


@pytest.mark.asyncio
async def test_manager_encode_and_upload_flow(monkeypatch, tmp_path):
    """
    Verifies full manager lifecycle:
    - Enqueue job with existing source.
    - Encodes proxy file.
    - Uploads with proxy caption (kind=proxy, proxy_of=orig_id).
    - Commits child proxy row (kind=proxy) and parts.
    - Updates parent proxy_status='ready'.
    """
    manager = TranscodingManager()
    orig_id = f"v_orig_{uuid.uuid4().hex[:10]}"

    # Create dummy source video file
    dummy_source = tmp_path / "test_source.mp4"
    dummy_source.write_bytes(b"dummy video data content")

    async with get_db() as db:
        unique_sha = f"hash_source_{uuid.uuid4().hex}"
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'test_source.mp4', 'video/mp4', 'video', 1000000, ?, 0, 1, 'completed', 'original', 'none')
            """,
            (orig_id, unique_sha)
        )
        await db.commit()

    # Mock encode_stream_v1 to avoid running actual ffmpeg on dummy bytes
    async def fake_encode_stream_v1(src, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"encoded proxy mp4 bytes")
        return {
            "size_bytes": 23,
            "profile": "stream_v1",
            "encoder_used": "libx264",
            "width": 1280,
            "height": 720,
            "duration_s": 10.0
        }

    monkeypatch.setattr("backend.transcoding.manager.encode_stream_v1", fake_encode_stream_v1)

    uploaded_captions = []
    from backend.storage.client import storage_client
    async def fake_upload_part(part_path, caption=None, progress_callback=None):
        uploaded_captions.append(caption)
        return {
            "message_id": 9999,
            "channel_id": "-10012345",
            "file_id": None,
            "access_hash": None
        }

    monkeypatch.setattr(storage_client, "upload_part", fake_upload_part)

    await manager._process_job(orig_id, dummy_source)

    # Check upload caption had k=p and of=orig_id
    assert len(uploaded_captions) >= 1
    cap = uploaded_captions[0]
    payload = json.loads(cap.replace("VAYOM:v1:", ""))
    assert payload["k"] == "p"
    assert payload["of"] == orig_id
    assert payload["profile"] in ["stream_v1", PROXY_PROFILE]

    # Check database state
    async with get_db() as db:
        # Parent status must be 'ready'
        cur = await db.execute("SELECT proxy_status FROM files WHERE id = ?", (orig_id,))
        p_status = (await cur.fetchone())[0]
        assert p_status == "ready"

        # Child proxy must exist
        cur = await db.execute("SELECT id, kind, proxy_of, proxy_profile, width, height FROM files WHERE proxy_of = ?", (orig_id,))
        child_row = await cur.fetchone()
        assert child_row is not None
        assert child_row["kind"] == "proxy"
        assert child_row["proxy_of"] == orig_id
        assert child_row["proxy_profile"] in ["stream_v1", PROXY_PROFILE]
        assert child_row["width"] == 1280
        assert child_row["height"] == 720


@pytest.mark.asyncio
async def test_manual_trigger_proxy_endpoint():
    """Verifies POST /api/v1/files/{file_id}/generate-proxy validation and guards."""
    from fastapi.testclient import TestClient
    from backend.main import app

    orig_id = f"v_orig_{uuid.uuid4().hex[:10]}"

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'remote_video.mp4', 'video/mp4', 'video', 50000000, 'hash_remote', 0, 1, 'completed', 'original', 'none')
            """,
            (orig_id,)
        )
        await db.commit()

    with TestClient(app) as client:
        # Without local file and without force_download -> 400 requires confirmation
        res = client.post(f"/api/v1/files/{orig_id}/generate-proxy")
        assert res.status_code == 400
        assert "force_download=True" in res.json()["detail"]

        # With nonexistent file_id -> 404
        res_404 = client.post(f"/api/v1/files/v_nonexistent_999/generate-proxy")
        assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_skip_proxy_for_audio_only(monkeypatch, tmp_path):
    """Verifies that audio-only files (audio track but no video) skip proxy generation."""
    manager = TranscodingManager()
    orig_id = f"v_orig_{uuid.uuid4().hex[:10]}"
    dummy_audio = tmp_path / "podcast.mp3"
    dummy_audio.write_bytes(b"dummy audio content")

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'podcast.mp3', 'audio/mpeg', 'audio', 50000, 'hash_audio', 0, 1, 'completed', 'original', 'queued')
            """,
            (orig_id,)
        )
        await db.commit()

    async def fake_probe(src):
        return {
            "has_audio": True,
            "has_video": False,
            "width": 0,
            "height": 0,
            "duration_s": 120.0
        }

    monkeypatch.setattr("backend.transcoding.manager.probe_stream_metadata", fake_probe)

    await manager._process_job(orig_id, dummy_audio)

    async with get_db() as db:
        cur = await db.execute("SELECT proxy_status FROM files WHERE id = ?", (orig_id,))
        status = (await cur.fetchone())[0]
        assert status == "skipped_small"


@pytest.mark.asyncio
async def test_reuse_existing_proxy_for_duplicate_hash(monkeypatch, tmp_path):
    """Verifies that an original with identical SHA-256 reuses existing completed proxy without re-encoding."""
    manager = TranscodingManager()
    orig1_id = f"v_orig1_{uuid.uuid4().hex[:10]}"
    orig2_id = f"v_orig2_{uuid.uuid4().hex[:10]}"
    prx1_id = f"v_prx1_{uuid.uuid4().hex[:10]}"
    shared_hash = "identical_sha256_hash_12345"

    dummy_file = tmp_path / "video2.mp4"
    dummy_file.write_bytes(b"dummy video bytes")

    async with get_db() as db:
        # File 1 has a ready proxy
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'video1.mp4', 'video/mp4', 'video', 1000000, ?, 0, 1, 'completed', 'original', 'ready')
            """,
            (orig1_id, shared_hash)
        )
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_of, proxy_status, width, height, duration_s, proxy_profile)
            VALUES (?, 'video1.proxy.mp4', 'video/mp4', 'video', 200000, 'proxy_hash_999', 0, 1, 'completed', 'proxy', ?, 'ready', 854, 480, 60.0, ?)
            """,
            (prx1_id, orig1_id, PROXY_PROFILE)
        )
        await db.execute(
            """
            INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id, telegram_channel_id)
            VALUES ('vp_1', ?, 0, 0, 200000, 'proxy_hash_999', 5555, '-100123')
            """,
            (prx1_id,)
        )

        # File 2 is an identical duplicate
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'video2.mp4', 'video/mp4', 'video', 1000000, ?, 0, 1, 'completed', 'original', 'queued')
            """,
            (orig2_id, shared_hash)
        )
        await db.commit()

    # encode_stream_v1 should NEVER be called since proxy is reused
    encode_called = False
    async def trap_encode(*args, **kwargs):
        nonlocal encode_called
        encode_called = True
        raise AssertionError("encode_stream_v1 should not be called when duplicate proxy exists!")

    monkeypatch.setattr("backend.transcoding.manager.encode_stream_v1", trap_encode)

    await manager._process_job(orig2_id, dummy_file)

    assert encode_called is False
    async with get_db() as db:
        cur = await db.execute("SELECT proxy_status FROM files WHERE id = ?", (orig2_id,))
        assert (await cur.fetchone())[0] == "ready"

        cur_prx = await db.execute("SELECT id, proxy_of, width, height FROM files WHERE proxy_of = ?", (orig2_id,))
        row = await cur_prx.fetchone()
        assert row is not None
        assert row["proxy_of"] == orig2_id
        assert row["width"] in [1280, 854]
        assert row["height"] in [720, 480]


@pytest.mark.asyncio
async def test_proxy_duration_mismatch_failure(monkeypatch, tmp_path):
    """Verifies that if proxy duration diverges > 1% from source duration, transcode fails."""
    manager = TranscodingManager()
    orig_id = f"v_orig_{uuid.uuid4().hex[:10]}"
    dummy_source = tmp_path / "drift_test.mp4"
    dummy_source.write_bytes(b"drift test video")

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'drift_test.mp4', 'video/mp4', 'video', 500000, 'hash_drift', 0, 1, 'completed', 'original', 'queued')
            """,
            (orig_id,)
        )
        await db.commit()

    async def fake_probe(src):
        return {
            "has_audio": True,
            "has_video": True,
            "width": 1920,
            "height": 1080,
            "duration_s": 100.0  # 100 seconds source
        }

    # Proxy produced only 50 seconds (severe truncation)
    async def fake_encode(src, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"truncated proxy")
        return {
            "size_bytes": 15,
            "profile": "stream_v1",
            "encoder_used": "libx264",
            "width": 1280,
            "height": 720,
            "duration_s": 50.0  # 50% drift -> must fail
        }

    monkeypatch.setattr("backend.transcoding.manager.probe_stream_metadata", fake_probe)
    monkeypatch.setattr("backend.transcoding.manager.encode_stream_v1", fake_encode)

    with pytest.raises(Exception):
        await manager._process_job(orig_id, dummy_source)

    async with get_db() as db:
        cur = await db.execute("SELECT proxy_status FROM files WHERE id = ?", (orig_id,))
        # Must fail due to duration mismatch
        assert (await cur.fetchone())[0] == "failed"


@pytest.mark.asyncio
async def test_proxymanager_alias():
    """Verifies ProxyManager is an alias to TranscodingManager."""
    assert ProxyManager is TranscodingManager


@pytest.mark.asyncio
async def test_cleanup_stale_proxy_temp_files(tmp_path, monkeypatch):
    """
    Verifies that cleanup_stale_temp_files removes orphaned .tmp and .tmp.mp4 files
    older than 1 hour in settings.DATA_DIR / 'proxies_tmp'.
    """
    manager = ProxyManager()
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    proxies_tmp = tmp_path / "proxies_tmp"
    proxies_tmp.mkdir(parents=True, exist_ok=True)

    # 1. Stale .tmp file (> 1 hour old)
    stale_tmp = proxies_tmp / "source_stale1.tmp"
    stale_tmp.write_text("old temp source data")
    old_time = time.time() - 7200  # 2 hours ago
    os.utime(stale_tmp, (old_time, old_time))

    # 2. Stale .tmp.mp4 file (> 1 hour old)
    stale_mp4 = proxies_tmp / "orphaned_preview.tmp.mp4"
    stale_mp4.write_text("old temp mp4 data")
    os.utime(stale_mp4, (old_time, old_time))

    # 3. Stale file and preview mp4 in subdirectory
    sub_dir = proxies_tmp / "v_prx_abandoned"
    sub_dir.mkdir(parents=True, exist_ok=True)
    stale_sub_file = sub_dir / "preview.tmp"
    stale_sub_file.write_text("abandoned proxy piece")
    os.utime(stale_sub_file, (old_time, old_time))

    stale_preview_mp4 = sub_dir / "preview_stream_v2_480p.mp4"
    stale_preview_mp4.write_text("abandoned proxy mp4 output")
    os.utime(stale_preview_mp4, (old_time, old_time))

    # Run cleanup
    removed = await manager.cleanup_stale_temp_files(max_age_seconds=3600.0)

    assert removed == 4
    assert not stale_tmp.exists()
    assert not stale_mp4.exists()
    assert not stale_sub_file.exists()
    assert not stale_preview_mp4.exists()
    assert not sub_dir.exists(), "Empty subdirectory should have been pruned"


@pytest.mark.asyncio
async def test_cleanup_preserves_active_and_young_files(tmp_path, monkeypatch):
    """
    Verifies that fresh files (< 1 hour) and actively processing files are NOT deleted.
    """
    manager = ProxyManager()
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    proxies_tmp = tmp_path / "proxies_tmp"
    proxies_tmp.mkdir(parents=True, exist_ok=True)

    old_time = time.time() - 7200  # 2 hours ago

    # 1. Active file in _active_jobs
    manager._active_jobs.add("active_video_123")
    active_tmp = proxies_tmp / "source_active_video_123.tmp"
    active_tmp.write_text("actively encoding source")
    os.utime(active_tmp, (old_time, old_time))

    # 2. Active file in _active_file_paths
    active_path_file = proxies_tmp / "custom_task.tmp.mp4"
    active_path_file.write_text("in progress encode")
    os.utime(active_path_file, (old_time, old_time))
    manager._active_file_paths.add(active_path_file.resolve())

    # 3. Fresh file (created just now, < 1 hour)
    fresh_tmp = proxies_tmp / "source_recent.tmp"
    fresh_tmp.write_text("recently created source")

    # 4. Non-matching file (e.g., .log or .json)
    other_file = proxies_tmp / "notes.txt"
    other_file.write_text("some metadata")
    os.utime(other_file, (old_time, old_time))

    # 5. An actual stale file that SHOULD be deleted
    stale_file = proxies_tmp / "source_stale_to_delete.tmp"
    stale_file.write_text("stale data")
    os.utime(stale_file, (old_time, old_time))

    removed = await manager.cleanup_stale_temp_files(max_age_seconds=3600.0)

    assert removed == 1
    assert not stale_file.exists(), "Stale file should be deleted"
    assert active_tmp.exists(), "Active job file must be preserved"
    assert active_path_file.exists(), "Actively tracked file path must be preserved"
    assert fresh_tmp.exists(), "Fresh file (< 1h) must be preserved"
    assert other_file.exists(), "Non-temp file must be preserved"


@pytest.mark.asyncio
async def test_proxymanager_stop_cancels_ffmpeg_subprocesses():
    """
    Verifies that ProxyManager.stop() cleanly cancels and awaits active ffmpeg
    subprocesses so no zombie processes linger.
    """
    manager = ProxyManager()

    # Create mock subprocesses
    proc1 = MagicMock()
    proc1.returncode = None
    proc1.kill = MagicMock()
    proc1.wait = AsyncMock(return_value=-9)

    proc2 = MagicMock()
    proc2.returncode = None
    proc2.kill = MagicMock()
    proc2.wait = AsyncMock(return_value=-9)

    manager._active_processes.add(proc1)
    ACTIVE_FFMPEG_PROCESSES.add(proc2)

    await manager.stop()

    proc1.kill.assert_called_once()
    proc1.wait.assert_awaited_once()

    proc2.kill.assert_called_once()
    proc2.wait.assert_awaited_once()

    assert len(manager._active_processes) == 0
    assert len(ACTIVE_FFMPEG_PROCESSES) == 0


@pytest.mark.asyncio
async def test_proxymanager_start_triggers_cleanup(tmp_path, monkeypatch):
    """
    Verifies that ProxyManager.start() automatically sweeps stale files on startup.
    """
    manager = ProxyManager()
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    proxies_tmp = tmp_path / "proxies_tmp"
    proxies_tmp.mkdir(parents=True, exist_ok=True)

    stale_file = proxies_tmp / "old_stale_source.tmp"
    stale_file.write_text("stale data on startup")
    old_time = time.time() - 5000  # > 1 hour
    os.utime(stale_file, (old_time, old_time))

    try:
        await manager.start()
        assert not stale_file.exists(), "Stale temp file should be swept during manager.start()"
    finally:
        await manager.stop()


