import json
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from PIL import Image, ExifTags
from ..config import settings

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

def generate_webp_thumbnail(image_path: Path, output_path: Path, size: Tuple[int, int] = (240, 240), quality: int = 70) -> bool:
    """Generates an ultra-compact WebP thumbnail (~12 KB) for instant grid browsing."""
    try:
        with Image.open(image_path) as img:
            img.thumbnail(size, Image.Resampling.LANCZOS)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "WEBP", quality=quality, optimize=True)
            return True
    except Exception:
        return False

def extract_image_exif(image_path: Path) -> Dict[str, Any]:
    """Extracts optical EXIF tags and GPS data if embedded."""
    metadata: Dict[str, Any] = {}
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return metadata

            named_exif = {}
            for tag_id, value in exif.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                try:
                    # Filter out non-serializable byte arrays
                    if isinstance(value, (str, int, float)):
                        named_exif[tag_name] = value
                except Exception:
                    pass

            metadata["make"] = named_exif.get("Make")
            metadata["model"] = named_exif.get("Model")
            metadata["dimensions"] = {"width": img.width, "height": img.height}
    except Exception:
        pass

    return metadata
