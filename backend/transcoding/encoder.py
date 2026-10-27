import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger("vayom.transcoding.encoder")

# Bump this whenever the proxy recipe changes. It is persisted in the Telegram
# caption and SQLite so old, large proxies are never mistaken for a current one.
PROXY_PROFILE = "stream_v2_480p"
PROXY_MAX_HEIGHT = 480
PROXY_MAX_FPS = 30.0
PROXY_MAXRATE = "1200k"
PROXY_BUFSIZE = "2400k"


async def _communicate_with_timeout(proc, timeout: float):
    """Waits for a subprocess, killing it on timeout so it cannot leak."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

CANDIDATE_ENCODERS = [
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
    "h264_videotoolbox",
    "h264_mf",
    "libopenh264",
    "libx264"
]

_CACHED_BEST_ENCODER: Optional[str] = None
_PROBE_LOCK = asyncio.Lock()


class TranscodeError(Exception):
    """Raised when proxy transcoding fails."""
    pass


async def is_ffmpeg_available() -> bool:
    """Checks whether ffmpeg and ffprobe exist on the system PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def probe_best_encoder() -> Optional[str]:
    """
    Probes available hardware and software H.264 encoders at runtime in order of preference.
    Verifies each with a tiny test encode. Caches the result.
    Returns None if ffmpeg is not available or no encoder succeeds.
    """
    global _CACHED_BEST_ENCODER
    if _CACHED_BEST_ENCODER is not None:
        return _CACHED_BEST_ENCODER

    async with _PROBE_LOCK:
        if _CACHED_BEST_ENCODER is not None:
            return _CACHED_BEST_ENCODER

        if not await is_ffmpeg_available():
            logger.warning("ffmpeg or ffprobe not found in system PATH. Transcoding is unavailable.")
            return None

        for candidate in CANDIDATE_ENCODERS:
            try:
                cmd = [
                    "ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                    "-c:v", candidate,
                    "-f", "null", "-"
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                try:
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    continue

                if proc.returncode == 0:
                    logger.info(f"Detected working H.264 encoder: {candidate}")
                    _CACHED_BEST_ENCODER = candidate
                    return candidate
            except Exception as e:
                logger.debug(f"Encoder probe failed for {candidate}: {e}")

        logger.warning("No functional H.264 encoder found by probe.")
        return None


async def probe_stream_metadata(source_path: Path) -> Dict[str, Any]:
    """Probes video technical properties using ffprobe."""
    meta: Dict[str, Any] = {
        "width": 0,
        "height": 0,
        "fps": 30.0,
        "duration_s": 0.0,
        "is_interlaced": False,
        "is_hdr": False,
        "has_audio": False,
        "has_video": False
    }
    if not shutil.which("ffprobe"):
        return meta

    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format",
            "--", str(source_path)
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await _communicate_with_timeout(proc, 15.0)
        if proc.returncode != 0 or not stdout:
            return meta

        data = json.loads(stdout.decode("utf-8", errors="ignore"))
        streams = data.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if a_stream:
            meta["has_audio"] = True

        if v_stream:
            meta["has_video"] = True
            meta["width"] = int(v_stream.get("width") or 0)
            meta["height"] = int(v_stream.get("height") or 0)
            
            # FPS calculation
            r_fps = v_stream.get("r_frame_rate", "30/1")
            if "/" in r_fps:
                num, den = r_fps.split("/", 1)
                try:
                    meta["fps"] = round(float(num) / float(den), 2) if float(den) > 0 else 30.0
                except Exception:
                    meta["fps"] = 30.0

            # Interlaced check
            field_order = v_stream.get("field_order", "progressive")
            if field_order and field_order != "progressive":
                meta["is_interlaced"] = True

            # HDR check
            color_transfer = str(v_stream.get("color_transfer", "")).lower()
            color_primaries = str(v_stream.get("color_primaries", "")).lower()
            if "smpte2084" in color_transfer or "arib-std-b67" in color_transfer or "bt2020" in color_primaries:
                meta["is_hdr"] = True

        # Duration
        fmt = data.get("format", {})
        dur = float(v_stream.get("duration") or fmt.get("duration") or 0.0) if v_stream else 0.0
        meta["duration_s"] = round(dur, 2)
    except Exception as e:
        logger.warning(f"ffprobe metadata extraction failed for {source_path}: {e}")

    return meta


def _build_encoder_args(encoder: str, fps: float) -> List[str]:
    """Builds specific quality and rate-control flags for chosen encoder."""
    gop_size = max(15, int(round(fps * 2)))  # ~2 seconds per keyframe

    common = [
        "-c:v", encoder,
        "-g", str(gop_size),
        "-sc_threshold", "0",
        "-pix_fmt", "yuv420p"
    ]

    if encoder == "h264_nvenc":
        return common + [
            "-preset", "p4",
            "-rc", "vbr",
            "-cq", "30",
            "-b:v", "0",
            "-maxrate", PROXY_MAXRATE,
            "-bufsize", PROXY_BUFSIZE
        ]
    elif encoder == "h264_qsv":
        return common + [
            "-preset", "medium",
            "-global_quality", "30",
            "-maxrate", PROXY_MAXRATE,
            "-bufsize", PROXY_BUFSIZE
        ]
    elif encoder == "h264_amf":
        return common + [
            "-quality", "speed",
            "-rc", "cqp",
            "-qp_i", "30",
            "-qp_p", "30",
            "-maxrate", PROXY_MAXRATE,
            "-bufsize", PROXY_BUFSIZE
        ]
    elif encoder == "h264_videotoolbox":
        return common + [
            "-q:v", "70",
            "-b:v", "1000k",
            "-maxrate", PROXY_MAXRATE,
            "-bufsize", PROXY_BUFSIZE
        ]
    elif encoder == "h264_mf":
        return common + [
            "-b:v", "1000k",
            "-maxrate", PROXY_MAXRATE,
            "-bufsize", PROXY_BUFSIZE
        ]
    elif encoder == "libopenh264":
        return common + [
            "-b:v", "1000k",
            "-maxrate", PROXY_MAXRATE,
            "-bufsize", PROXY_BUFSIZE
        ]
    else:
        # Default libx264 software. CRF keeps static scenes small, while the
        # maxrate caps motion-heavy recordings so 480p stays bandwidth-friendly.
        return common + [
            "-preset", "veryfast",
            "-crf", "28",
            "-maxrate", PROXY_MAXRATE,
            "-bufsize", PROXY_BUFSIZE
        ]


async def encode_stream_v2_480p(source_path: Path, target_path: Path) -> Dict[str, Any]:
    """
    Encodes video into the compact 'stream_v2_480p' proxy profile:
    - H.264 8-bit yuv420p
    - Max 480p (height capped, width derived; never upscale)
    - Max 30 fps (drops excess frames from high-fps screen recordings)
    - Constant frame rate (prevents A/V drift)
    - Keyframe every ~2 seconds (-g, -sc_threshold 0) for instant seeking
    - FastStart enabled (-movflags +faststart) so moov atom is at byte 0
    - AAC 96k stereo audio
    - Map first video stream and default audio only, drop subtitle/data streams
    - Returns technical metadata of generated proxy
    """
    if not source_path.exists():
        raise TranscodeError(f"Source video path does not exist: {source_path}")

    encoder = await probe_best_encoder()
    if not encoder:
        raise TranscodeError("No functional H.264 encoder is available on this system.")

    meta = await probe_stream_metadata(source_path)
    source_fps = float(meta.get("fps") or 30.0)
    # Keep 60 fps sources at 30 fps: 480p proxy playback does not benefit from
    # the extra frames, and halving them cuts bandwidth and encode time.
    fps = min(source_fps, PROXY_MAX_FPS) if source_fps > 0 else PROXY_MAX_FPS

    # Build video filter chain
    vf_filters = []
    if meta.get("is_interlaced"):
        vf_filters.append("yadif=0:-1:0")

    if meta.get("is_hdr"):
        # H-06: tonemap operates on linear-light float pixels. Without the
        # zscale linear conversion the filter either fails or washes out.
        vf_filters.append(
            "zscale=t=linear:npl=100,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
        )

    # Downscale to max 480p, preserving aspect ratio with even dimensions; never upscale
    vf_filters.append(f"scale=-2:'trunc(min({PROXY_MAX_HEIGHT}\\,ih)/2)*2'")

    vf_arg = ",".join(vf_filters)

    cmd = [
        "ffmpeg", "-y", "-v", "warning",
        "-i", str(source_path),
        "-vf", vf_arg,
        "-r", str(fps),
    ]

    # Encoder specific flags
    cmd.extend(_build_encoder_args(encoder, fps))

    # Audio flags
    if meta.get("has_audio"):
        cmd.extend(["-c:a", "aac", "-b:a", "96k", "-ac", "2"])
    else:
        cmd.extend(["-an"])

    # FastStart & container flags
    cmd.extend([
        "-movflags", "+faststart",
        "-map", "0:v:0",
    ])
    if meta.get("has_audio"):
        cmd.extend(["-map", "0:a:0?"])

    # Drop subtitles and data streams
    cmd.extend(["-sn", "-dn"])

    # Output file
    cmd.append(str(target_path))

    target_path.parent.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    try:
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        if target_path.exists():
            target_path.unlink()
        raise

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="ignore") if stderr else "Unknown ffmpeg error"
        if target_path.exists():
            try:
                target_path.unlink()
            except Exception:
                pass
        raise TranscodeError(f"Transcoding failed (code {proc.returncode}): {err_msg[:400]}")

    if not target_path.exists() or target_path.stat().st_size == 0:
        raise TranscodeError("Transcoding produced an empty or missing output file.")

    # Probe resulting proxy file to obtain exact dimensions & size
    proxy_meta = await probe_stream_metadata(target_path)
    return {
        "size_bytes": target_path.stat().st_size,
        "profile": PROXY_PROFILE,
        "encoder_used": encoder,
        "width": proxy_meta.get("width") or 0,
        "height": proxy_meta.get("height") or 0,
        "fps": fps,
        "duration_s": proxy_meta.get("duration_s") or meta.get("duration_s") or 0.0
    }


# Backwards compatibility alias
encode_stream_v1 = encode_stream_v2_480p

