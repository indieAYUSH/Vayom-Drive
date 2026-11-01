import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
import pytest
from PIL import Image

from backend.index.metadata import (
    extract_image_exif,
    extract_video_metadata,
    detect_file_category,
    generate_webp_thumbnail,
)


@pytest.mark.asyncio
async def test_extract_video_metadata_4k_h264():
    """Generates synthetic 4K H.264 video with DJI tag and verifies extraction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "dji_4k_test.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "color=c=navy:s=3840x2160:d=0.5",
            "-c:v", "libx264",
            "-movflags", "use_metadata_tags",
            "-metadata", "handler_name=DJI Osmo Pocket 3",
            "-metadata", "make=DJI",
            "-metadata", "model=Osmo Pocket 3",
            str(video_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert res.returncode == 0, f"ffmpeg failed: {res.stderr.decode()}"

        meta = await extract_video_metadata(video_path)
        assert meta.get("codec") == "H.264"
        assert meta.get("resolution_tier") == "4K UHD"
        assert meta.get("dimensions") == {"width": 3840, "height": 2160}
        assert meta.get("camera_make") == "DJI"
        assert meta.get("camera_model") == "Osmo Pocket 3"
        assert "h.264" in meta.get("tags", [])
        assert "4k" in meta.get("tags", [])
        assert "dji" in meta.get("tags", [])


@pytest.mark.asyncio
async def test_extract_video_metadata_1080p_hevc():
    """Generates synthetic 1080p HEVC video and verifies extraction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "sony_hevc_test.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "color=c=black:s=1920x1080:d=0.5",
            "-c:v", "libx265",
            "-movflags", "use_metadata_tags",
            "-metadata", "make=Sony",
            "-metadata", "model=ILCE-7M4",
            str(video_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert res.returncode == 0, f"ffmpeg failed: {res.stderr.decode()}"

        meta = await extract_video_metadata(video_path)
        assert meta.get("codec") == "HEVC"
        assert meta.get("resolution_tier") == "1080p FHD"
        assert meta.get("dimensions") == {"width": 1920, "height": 1080}
        assert meta.get("camera_make") == "Sony"
        assert meta.get("camera_model") == "ILCE-7M4"
        assert "hevc" in meta.get("tags", [])
        assert "1080p" in meta.get("tags", [])
        assert "sony" in meta.get("tags", [])


def test_extract_image_exif_metadata():
    """Verifies image EXIF metadata extraction with Pillow IFD."""
    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = Path(tmpdir) / "sample_photo.jpg"
        img = Image.new("RGB", (1200, 800), color="blue")
        exif_dict = img.getexif()
        # EXIF tags: 271=Make, 272=Model
        exif_dict[271] = "Canon"
        exif_dict[272] = "EOS R5"
        img.save(img_path, exif=exif_dict)

        meta = extract_image_exif(img_path)
        assert meta.get("camera_make") == "Canon"
        assert meta.get("camera_model") == "EOS R5"
        assert meta.get("dimensions") == {"width": 1200, "height": 800}
        assert "canon" in meta.get("tags", [])
        assert "eos r5" in meta.get("tags", [])


def test_generate_webp_thumbnail_converts_high_bitdepth_modes(monkeypatch, tmp_path):
    """I-26: 16-bit integer / float sources (I;16, I, F) are converted to RGB before WebP encoding."""
    cases = [("I;16", 30000), ("I", 30000), ("F", 0.5)]
    sources = []
    for mode, value in cases:
        src = tmp_path / f"src_{mode.replace(';', '_')}.tiff"
        Image.new(mode, (64, 64), value).save(src, format="TIFF")
        sources.append(src)

    saved_modes = []
    real_save = Image.Image.save

    def capture_save(self, fp, format=None, **kwargs):
        saved_modes.append(self.mode)
        return real_save(self, fp, format, **kwargs)

    monkeypatch.setattr(Image.Image, "save", capture_save)

    for src in sources:
        out = tmp_path / f"thumb_{src.stem}.webp"
        assert generate_webp_thumbnail(src, out) is True
        with Image.open(out) as thumb:
            assert thumb.mode == "RGB"

    assert saved_modes == ["RGB", "RGB", "RGB"]


def test_generate_webp_thumbnail_logs_failures_with_source_path(caplog, tmp_path):
    """I-26: thumbnail failures are logged with the source path instead of being swallowed."""
    bad_src = tmp_path / "corrupt_photo.jpg"
    bad_src.write_bytes(b"this is not a valid image file")
    out = tmp_path / "corrupt_photo.webp"

    with caplog.at_level(logging.WARNING, logger="vayom.index.metadata"):
        assert generate_webp_thumbnail(bad_src, out) is False

    assert str(bad_src) in caplog.text


class _FakeSubprocess:
    """Minimal asyncio-subprocess stand-in for deterministic I-10 unit tests."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0, hang: bool = False):
        self.returncode = returncode
        self.killed = False
        self.waited = False
        self._stdout = stdout
        self._hang = hang

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(30)
        return self._stdout, b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


@pytest.mark.asyncio
async def test_extract_video_metadata_hyphen_filename_after_separator(monkeypatch, tmp_path):
    """I-10: the path is passed after '--' so '-sample.mp4' cannot be parsed as an ffprobe flag."""
    captured_argv = []
    fake_stdout = json.dumps({
        "streams": [{
            "codec_type": "video",
            "codec_name": "h264",
            "width": 640,
            "height": 360,
            "r_frame_rate": "30/1",
        }],
        "format": {},
    }).encode()

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_argv.extend(args)
        return _FakeSubprocess(stdout=fake_stdout)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    video_path = tmp_path / "-sample.mp4"
    meta = await extract_video_metadata(video_path)

    separator_index = captured_argv.index("--")
    assert captured_argv[separator_index + 1] == str(video_path)
    assert separator_index == len(captured_argv) - 2  # the path is the final argument
    assert meta.get("codec") == "H.264"


@pytest.mark.asyncio
async def test_extract_video_metadata_hyphenated_real_file(monkeypatch):
    """I-10: a real ffprobe run succeeds for a relative path starting with '-sample.mp4'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plain_path = Path(tmpdir) / "sample.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "color=c=black:s=640x360:d=0.5",
            "-c:v", "libx264",
            str(plain_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert res.returncode == 0, f"ffmpeg failed: {res.stderr.decode()}"

        plain_path.rename(Path(tmpdir) / "-sample.mp4")
        # Run from inside tmpdir so the path string begins with '-' (the injection case).
        monkeypatch.chdir(tmpdir)

        meta = await extract_video_metadata(Path("-sample.mp4"))
        assert meta.get("codec") == "H.264"
        assert meta.get("dimensions") == {"width": 640, "height": 360}


@pytest.mark.asyncio
async def test_extract_video_metadata_timeout_kills_process(monkeypatch, tmp_path):
    """I-10: when ffprobe times out it is killed and awaited (no orphaned process)."""
    fake_proc = _FakeSubprocess(hang=True)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    real_wait_for = asyncio.wait_for

    async def short_wait_for(awaitable, timeout):
        # Force the 10 s production timeout to fire almost immediately.
        return await real_wait_for(awaitable, timeout=0.05)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "wait_for", short_wait_for)

    meta = await extract_video_metadata(tmp_path / "-sample.mp4")

    assert meta == {}
    assert fake_proc.killed
    assert fake_proc.waited
