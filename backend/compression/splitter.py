import os
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from ..config import settings

def calculate_sha256(file_path: Path) -> str:
    """Computes the SHA-256 hash of a file efficiently using 1 MB chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()

def split_file_if_needed(
    file_path: Path,
    max_part_bytes: int = settings.MAX_PART_BYTES,
    output_dir: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """
    Checks if a file exceeds max_part_bytes (default 1.9 GB).
    If <= max_part_bytes: returns a single part descriptor.
    If > max_part_bytes: slices the file into sequential binary part files 
    and returns a contiguous Pointer-Offset extent manifest.
    """
    total_size = os.path.getsize(file_path)
    if total_size <= max_part_bytes:
        sha = calculate_sha256(file_path)
        return [{
            "part_index": 0,
            "byte_offset": 0,
            "part_size_bytes": total_size,
            "part_sha256": sha,
            "file_path": str(file_path)
        }]

    # Multi-part splitting
    parts = []
    part_index = 0
    byte_offset = 0
    dest_dir = output_dir if output_dir is not None else file_path.parent

    with open(file_path, "rb") as src:
        while byte_offset < total_size:
            slice_size = min(max_part_bytes, total_size - byte_offset)
            part_filename = f"{file_path.stem}.part_{part_index}{file_path.suffix}"
            part_path = dest_dir / part_filename

            h = hashlib.sha256()
            bytes_written = 0
            with open(part_path, "wb") as dst:
                while bytes_written < slice_size:
                    read_len = min(1024 * 1024, slice_size - bytes_written)
                    buffer = src.read(read_len)
                    if not buffer:
                        break
                    h.update(buffer)
                    dst.write(buffer)
                    bytes_written += len(buffer)

            parts.append({
                "part_index": part_index,
                "byte_offset": byte_offset,
                "part_size_bytes": bytes_written,
                "part_sha256": h.hexdigest(),
                "file_path": str(part_path)
            })

            byte_offset += bytes_written
            part_index += 1

    return parts
