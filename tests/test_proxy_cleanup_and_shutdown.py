import asyncio
import os
import signal
import subprocess
import time
import uuid
import shutil
from pathlib import Path
import pytest

from backend.config import settings
from backend.db.connection import get_db
import backend.transcoding.manager as manager_module
from backend.transcoding.manager import TranscodingManager
from backend.transcoding.encoder import PROXY_PROFILE, is_ffmpeg_available


def is_process_zombie(pid: int) -> bool:
    """Checks whether a PID on Linux is in zombie (defunct) state."""
    try:
        proc_status = Path(f"/proc/{pid}/status")
        if not proc_status.exists():
            return False
        for line in proc_status.read_text().splitlines():
            if line.startswith("State:"):
                return "Z" in line
        return False
    except Exception:
        return False


def is_process_alive(pid: int) -> bool:
    """Checks whether a process PID is currently alive and running (not a zombie)."""
    try:
        proc_status = Path(f"/proc/{pid}/status")
        if not proc_status.exists():
            return False
        for line in proc_status.read_text().splitlines():
            if line.startswith("State:"):
                return "Z" not in line
        return True
    except Exception:
        return False


def test_proxy_manager_alias_exists():
    """Verifies that ProxyManager alias exists in manager module."""
    assert hasattr(manager_module, "ProxyManager"), "ProxyManager alias not exported in backend.transcoding.manager"
    assert manager_module.ProxyManager is TranscodingManager or issubclass(manager_module.ProxyManager, TranscodingManager)


@pytest.mark.asyncio
async def test_stale_temp_cleanup_on_startup(tmp_path, monkeypatch):
    """
    Adversarial test:
    - Temp files older than 1 hour are cleaned up on startup.
    - Fresh temp files (< 5 mins) are preserved.
    - Files belonging to active transcoding jobs are preserved regardless of age.
    """
    test_tmp_dir = tmp_path / "proxies_tmp"
    test_tmp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    now = time.time()

    # 1. Stale files (> 1 hour old: 3601 - 7200 seconds)
    stale_source = test_tmp_dir / "source_stale_001.tmp"
    stale_source.write_bytes(b"stale source data 1")
    os.utime(stale_source, (now - 3700, now - 3700))

    stale_orphan_mp4 = test_tmp_dir / "orphaned_preview.tmp.mp4"
    stale_orphan_mp4.write_bytes(b"stale mp4 data")
    os.utime(stale_orphan_mp4, (now - 5000, now - 5000))

    stale_sub_dir = test_tmp_dir / "v_prx_stale_dir"
    stale_sub_dir.mkdir(parents=True, exist_ok=True)
    stale_sub_file = stale_sub_dir / "preview_stream_v2_480p.mp4"
    stale_sub_file.write_bytes(b"stale preview in subfolder")
    os.utime(stale_sub_file, (now - 4000, now - 4000))
    os.utime(stale_sub_dir, (now - 4000, now - 4000))

    # 2. Fresh files (< 5 mins old: 60 - 180 seconds)
    fresh_source = test_tmp_dir / "source_fresh_002.tmp"
    fresh_source.write_bytes(b"fresh source data 2")
    os.utime(fresh_source, (now - 60, now - 60))

    fresh_orphan_mp4 = test_tmp_dir / "recent_orphan.tmp.mp4"
    fresh_orphan_mp4.write_bytes(b"fresh mp4 data")
    os.utime(fresh_orphan_mp4, (now - 120, now - 120))

    fresh_sub_dir = test_tmp_dir / "v_prx_fresh_dir"
    fresh_sub_dir.mkdir(parents=True, exist_ok=True)
    fresh_sub_file = fresh_sub_dir / "preview_stream_v2_480p.mp4"
    fresh_sub_file.write_bytes(b"fresh preview in subfolder")
    os.utime(fresh_sub_file, (now - 180, now - 180))
    os.utime(fresh_sub_dir, (now - 180, now - 180))

    # 3. Active job file (older than 1 hr, but actively being processed)
    active_id = "v_active_999"
    active_source = test_tmp_dir / f"source_{active_id}.tmp"
    active_source.write_bytes(b"active job data")
    os.utime(active_source, (now - 7200, now - 7200))

    # Initialize manager and mark active_id as active
    manager = TranscodingManager()
    manager._active_jobs.add(active_id)

    # Start manager (should execute startup recovery and stale temp cleanup)
    await manager.start()

    try:
        # Verify stale files are cleaned up
        assert not stale_source.exists(), "Stale source temp file (> 1 hr) was not cleaned up!"
        assert not stale_orphan_mp4.exists(), "Stale .tmp.mp4 file (> 1 hr) was not cleaned up!"
        assert not stale_sub_file.exists(), "Stale file in subfolder (> 1 hr) was not cleaned up!"
        assert not stale_sub_dir.exists(), "Stale orphaned folder (> 1 hr) was not cleaned up!"

        # Verify fresh files (< 5 mins) are preserved
        assert fresh_source.exists(), "Fresh source temp file (< 5 mins) was deleted!"
        assert fresh_orphan_mp4.exists(), "Fresh .tmp.mp4 file (< 5 mins) was deleted!"
        assert fresh_sub_file.exists(), "Fresh file in subfolder (< 5 mins) was deleted!"
        assert fresh_sub_dir.exists(), "Fresh subfolder (< 5 mins) was deleted!"

        # Verify active job file is strictly preserved
        assert active_source.exists(), "File belonging to an active job was improperly deleted!"

    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_clean_shutdown_terminates_ffmpeg_without_zombies(tmp_path, monkeypatch):
    """
    Adversarial test:
    - Clean shutdown of ProxyManager terminates ffmpeg subprocesses.
    - Subprocesses are reaped properly without lingering zombies.
    """
    if not await is_ffmpeg_available():
        pytest.skip("ffmpeg not available")

    # Start a long-running ffmpeg process simulating active transcoding
    # Generate 30 seconds of synthetic video to null
    cmd = [
        "ffmpeg", "-y", "-v", "quiet",
        "-f", "lavfi", "-i", "testsrc=duration=30:size=640x360:rate=30",
        "-c:v", "libx264", "-f", "null", "-"
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    pid = proc.pid
    assert is_process_alive(pid), f"ffmpeg process {pid} should be alive initially"

    manager = getattr(manager_module, "ProxyManager", TranscodingManager)()
    await manager.start()

    # Track process in manager if tracking mechanism exists, or attach to active processes
    if hasattr(manager, "_active_processes"):
        manager._active_processes.add(proc)
    if hasattr(manager_module, "ACTIVE_FFMPEG_PROCESSES"):
        manager_module.ACTIVE_FFMPEG_PROCESSES.add(proc)

    # Let it run briefly
    await asyncio.sleep(0.1)
    assert is_process_alive(pid), f"ffmpeg process {pid} should still be running"

    # Now execute clean shutdown
    await manager.stop()

    # Wait up to 3 seconds for process termination and reaping
    deadline = time.time() + 3.0
    while is_process_alive(pid) and time.time() < deadline:
        await asyncio.sleep(0.05)

    # Give OS a moment to reap
    await asyncio.sleep(0.1)

    # Verify: Process must NOT be running
    assert not is_process_alive(pid), f"ffmpeg process {pid} is still alive after manager.stop()!"

    # Verify: Process must NOT be a zombie
    assert not is_process_zombie(pid), f"ffmpeg process {pid} was left as a zombie after manager.stop()!"


@pytest.mark.asyncio
async def test_duplicate_detection_and_profile_invariants():
    """Verifies that stream_v2_480p and stream_v1 profiles and duplicate logic remain intact."""
    from backend.transcoding.encoder import (
        PROXY_PROFILE,
        PROXY_MAX_HEIGHT,
        encode_stream_v1,
        encode_stream_v2_480p,
    )
    assert PROXY_PROFILE == "stream_v2_480p"
    assert PROXY_MAX_HEIGHT == 480
    assert encode_stream_v1 is encode_stream_v2_480p
