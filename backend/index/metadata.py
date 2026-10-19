import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from PIL import Image, ExifTags
from ..config import settings

# I-2: pin Pillow's decompression-bomb guard explicitly (same threshold as Pillow's default).
# Image decodes now run inside worker threads, so this cap is the memory-safety backstop.
Image.MAX_IMAGE_PIXELS = 89_478_485

logger = logging.getLogger("vayom.index.metadata")


async def _communicate_with_timeout(proc, timeout: float):
    """Waits for a subprocess, killing it on timeout so it cannot leak."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise


def detect_file_category(mime_type: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if mime_type.startswith("image/") or ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".arw", ".cr2", ".nef", ".dng", ".tiff"]:
        return "image"
    if mime_type.startswith("video/") or ext in [".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"]:
        return "video"
    if ext in [".zip", ".tar", ".gz", ".zst", ".xz", ".7z", ".rar", ".bz2"]:
        return "archive"
    if ext in [".pdf", ".doc", ".docx", ".txt", ".md", ".csv", ".xlsx", ".json", ".py", ".rs", ".ts"]:
        return "document"
    return "other"


def generate_webp_thumbnail(
    image_path: Path, 
    output_path: Path, 
    size: Tuple[int, int] = (480, 480), 
    quality: int = 85
) -> bool:
    """
    Generates a crisp, high-definition WebP thumbnail for instant grid browsing.
    Enhanced quality by ~20% (quality=85, 480x480 max bounds, LANCZOS resampling, method=6).
    """
    try:
        from PIL import ImageOps
        with Image.open(image_path) as img:
            # Handle phone/camera EXIF orientation tags automatically
            img = ImageOps.exif_transpose(img)

            # I-26: 16-bit integer / float modes (I;16, I;16B/L/N, I, F) are rejected by
            # older libwebp/Pillow builds — normalise them to RGB before encoding.
            if img.mode.startswith("I;16") or img.mode in ("I", "F"):
                img = img.convert("RGB")
            # Convert unsupported color modes (e.g. CMYK, palette) to RGB/RGBA
            elif img.mode in ("CMYK", "P", "1"):
                img = img.convert("RGB")
            elif img.mode == "LA":
                img = img.convert("RGBA")

            img.thumbnail(size, Image.Resampling.LANCZOS)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(
                output_path, 
                "WEBP", 
                quality=quality, 
                method=6,  # Maximum compression quality search in libwebp
                optimize=True
            )
            return True
    except Exception as e:
        # I-26: never swallow silently — log the source path so systematic thumbnail
        # failures (unsupported mode, corrupt file, decode cap) are diagnosable.
        logger.warning(f"Failed to generate WebP thumbnail for {image_path}: {e}")
        return False


async def generate_video_thumbnail(
    video_path: Path,
    output_path: Path,
    size: Tuple[int, int] = (480, 480),
    quality: int = 85
) -> bool:
    """
    Extracts a crisp frame from video and converts it to high-definition WebP thumbnail using ffmpeg.
    Probes at 1.0s, with fallback to 0.0s. Supports both local file paths and HTTP stream URLs.
    """
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Try at 1.0s first
        cmd = [
            "ffmpeg", "-y",
            "-ss", "00:00:01",
            "-i", str(video_path),
            "-vframes", "1",
            "-vf", f"scale='min({size[0]},iw)':-1",
            "-c:v", "libwebp",
            "-quality", str(quality),
            str(output_path)
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await _communicate_with_timeout(proc, 20.0)
        if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return True

        # Fallback to start of video (00:00:00)
        cmd_fallback = [
            "ffmpeg", "-y",
            "-ss", "00:00:00",
            "-i", str(video_path),
            "-vframes", "1",
            "-vf", f"scale='min({size[0]},iw)':-1",
            "-c:v", "libwebp",
            "-quality", str(quality),
            str(output_path)
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd_fallback,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await _communicate_with_timeout(proc, 20.0)
        return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
    except Exception as e:
        logger.warning(f"Thumbnail generation failed for {video_path}: {e}")
        return False


async def apply_mp4_faststart(input_path: Path, output_path: Path) -> bool:
    """
    Relocates the MP4 'moov' atom box to the front of the file (FastStart / Web Optimized).
    This allows web browsers and native players to stream videos immediately
    without downloading or seeking to the end of the file.
    Does NOT re-encode video or audio streams (-c copy).
    """
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path)
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await _communicate_with_timeout(proc, 60.0)
        return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
    except Exception as e:
        logger.warning(f"FastStart remux failed for {input_path}: {e}")
        return False


def extract_image_exif(image_path: Path) -> Dict[str, Any]:
    """Extracts optical EXIF tags, camera model, lens, exposure, and GPS data if embedded."""
    metadata: Dict[str, Any] = {}
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return metadata

            named_exif = {}
            for tag_id, value in exif.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                if isinstance(value, (str, int, float)):
                    named_exif[tag_name] = value

            # Also inspect detailed Exif IFD sub-tags (Lens, FNumber, ExposureTime, FocalLength, ISOSpeedRatings)
            try:
                exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
                for tag_id, value in exif_ifd.items():
                    tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                    if isinstance(value, (str, int, float)):
                        named_exif[tag_name] = value
                    elif hasattr(value, "numerator") and hasattr(value, "denominator"):
                        # Rational number (e.g. F-number, focal length, shutter speed)
                        named_exif[tag_name] = float(value)
            except Exception as e:
                logger.debug(f"EXIF IFD sub-tags unreadable for {image_path}: {e}")

            make = named_exif.get("Make")
            model = named_exif.get("Model")
            metadata["make"] = make
            metadata["model"] = model
            metadata["camera_make"] = str(make).strip() if make else None
            metadata["camera_model"] = str(model).strip() if model else None

            lens = named_exif.get("LensModel") or named_exif.get("LensMake")
            if lens:
                metadata["lens"] = str(lens).strip()

            if "FNumber" in named_exif:
                fnum = named_exif["FNumber"]
                metadata["aperture"] = f"f/{fnum:.1f}" if isinstance(fnum, float) else f"f/{fnum}"

            if "FocalLength" in named_exif:
                fl = named_exif["FocalLength"]
                metadata["focal_length"] = f"{int(fl)}mm" if isinstance(fl, (int, float)) else str(fl)

            if "ISOSpeedRatings" in named_exif:
                metadata["iso"] = int(named_exif["ISOSpeedRatings"])

            if "ExposureTime" in named_exif:
                exp = named_exif["ExposureTime"]
                if isinstance(exp, float) and 0 < exp < 1:
                    metadata["shutter_speed"] = f"1/{int(round(1.0 / exp))}s"
                else:
                    metadata["shutter_speed"] = f"{exp}s"

            # I-14: report display dimensions (honour EXIF orientation 5-8, matching the thumbnail)
            orientation = exif.get(0x0112, 1)
            width, height = img.width, img.height
            if orientation in (5, 6, 7, 8):
                width, height = height, width
            metadata["dimensions"] = {"width": width, "height": height}

            # I-13: capture timestamp + GPS coordinates for the timeline/map surfaces
            dt_raw = named_exif.get("DateTimeOriginal") or named_exif.get("DateTime")
            if isinstance(dt_raw, str) and dt_raw.strip():
                try:
                    metadata["taken_at"] = datetime.strptime(dt_raw.strip(), "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass

            try:
                gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo) or {}
            except Exception:
                gps_ifd = {}

            def _gps_degrees(value) -> Optional[float]:
                try:
                    deg, minute, sec = value
                    return float(deg) + float(minute) / 60.0 + float(sec) / 3600.0
                except Exception:
                    return None

            def _gps_ref(value, default: str) -> str:
                if isinstance(value, bytes):
                    value = value.decode("ascii", "ignore")
                return str(value or default).strip().upper()

            gps_lat = _gps_degrees(gps_ifd.get(2))
            gps_lon = _gps_degrees(gps_ifd.get(4))
            if gps_lat is not None and gps_lon is not None:
                if _gps_ref(gps_ifd.get(1), "N").startswith("S"):
                    gps_lat = -gps_lat
                if _gps_ref(gps_ifd.get(3), "E").startswith("W"):
                    gps_lon = -gps_lon
                metadata["latitude"] = round(gps_lat, 6)
                metadata["longitude"] = round(gps_lon, 6)

            # Tags from keywords / comments
            tags = []
            if make:
                tags.append(str(make).lower().strip())
            if model:
                tags.append(str(model).lower().strip())
            if "XPKeywords" in named_exif:
                kw = str(named_exif["XPKeywords"]).split(";")
                tags.extend([k.strip().lower() for k in kw if k.strip()])
            if tags:
                metadata["tags"] = list(dict.fromkeys(tags))
    except Exception:
        pass

    return metadata


async def extract_video_metadata(video_path: Path) -> Dict[str, Any]:
    """
    Extracts video codec (HEVC, H.264, ProRes, AV1), resolution tier,
    frame rate, duration, and device model using native ffprobe.
    """
    metadata: Dict[str, Any] = {}
    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            # I-10: terminate option parsing so a leading-dash filename
            # (e.g. "-sample.mp4") is treated as a path, not an ffprobe flag.
            "--",
            str(video_path)
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            # I-10: communicate() cancellation alone does not reap the child;
            # kill + await so a hung ffprobe cannot leak as a zombie process.
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return metadata
        if proc.returncode != 0 or not stdout:
            return metadata

        probe_data = json.loads(stdout.decode("utf-8", errors="ignore"))
        streams = probe_data.get("streams", [])
        format_info = probe_data.get("format", {})

        # Find primary video stream
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not video_stream:
            return metadata

        raw_codec = video_stream.get("codec_name", "").lower()
        codec_map = {
            "hevc": "HEVC",
            "h265": "HEVC",
            "h264": "H.264",
            "avc1": "H.264",
            "prores": "ProRes",
            "av1": "AV1",
            "av01": "AV1",
            "vp9": "VP9",
            "vp8": "VP8"
        }
        codec = codec_map.get(raw_codec, raw_codec.upper() if raw_codec else "UNKNOWN")
        metadata["codec"] = codec

        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        if width and height:
            metadata["dimensions"] = {"width": width, "height": height}
            max_dim = max(width, height)
            min_dim = min(width, height)
            # I-12: tier by the short side (standard classes — YouTube-style), never the OR-max logic
            # that let ultrawide/square clips jump a class; long side only for the 8K threshold.
            if max_dim >= 7600:
                res_tier = "8K"
            elif min_dim >= 2160:
                res_tier = "4K UHD"
            elif min_dim >= 1440:
                res_tier = "2K QHD"
            elif min_dim >= 1080:
                res_tier = "1080p FHD"
            elif min_dim >= 720:
                res_tier = "720p HD"
            else:
                res_tier = "SD"
            metadata["resolution_tier"] = res_tier

        # Frame rate
        r_fps = video_stream.get("r_frame_rate", "")
        if "/" in r_fps:
            num, den = r_fps.split("/", 1)
            try:
                den_f = float(den)
                if den_f > 0:
                    metadata["frame_rate"] = round(float(num) / den_f, 1)
            except Exception:
                pass

        # Duration & bitrate (I-11: "N/A"/non-numeric tags must not abort the remaining metadata)
        try:
            dur = float(video_stream.get("duration") or format_info.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur > 0:
            metadata["duration_seconds"] = round(dur, 2)

        try:
            raw_br = video_stream.get("bit_rate") or format_info.get("bit_rate")
            if raw_br:
                metadata["bitrate"] = int(raw_br)
        except (TypeError, ValueError):
            pass

        format_name = format_info.get("format_name", "")
        if format_name:
            metadata["container"] = format_name.split(",")[0].strip()

        # Device make / model detection (Sony, DJI, Apple, GoPro)
        raw_tags = dict(format_info.get("tags", {}))
        raw_tags.update(video_stream.get("tags", {}))
        tags_lower = {str(k).lower(): v for k, v in raw_tags.items()}

        make = tags_lower.get("make") or tags_lower.get("com.apple.quicktime.make")
        model = tags_lower.get("model") or tags_lower.get("com.apple.quicktime.model")

        # Check DJI / GoPro / Sony / Apple specific tags or handler names
        handler = str(video_stream.get("handler_name", "")).lower()
        all_tags_str = str(raw_tags).lower()
        if not make:
            if "dji" in handler or "dji" in all_tags_str:
                make = "DJI"
            elif "gopro" in handler or "gopro" in all_tags_str:
                make = "GoPro"
            elif "sony" in handler or "sony" in all_tags_str:
                make = "Sony"
            elif "canon" in handler or "canon" in all_tags_str:
                make = "Canon"
            elif "apple" in handler or "apple" in all_tags_str:
                make = "Apple"

        if make:
            metadata["camera_make"] = str(make).strip()
            metadata["make"] = str(make).strip()
        if model:
            metadata["camera_model"] = str(model).strip()
            metadata["model"] = str(model).strip()

        # Tag chips
        auto_tags = [codec.lower()]
        if "resolution_tier" in metadata:
            auto_tags.append(metadata["resolution_tier"].lower().split()[0])
        if make:
            auto_tags.append(str(make).lower().strip())
        if model:
            auto_tags.append(str(model).lower().strip())
        metadata["tags"] = list(dict.fromkeys(auto_tags))

    except Exception as e:
        logger.warning(f"Video metadata extraction failed for {video_path}: {e}")

    return metadata
