import io
import os
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from ..config import settings


class BoundedFileSlice(io.IOBase):
    """
    Zero-copy virtual bounded slice of a file on local disk.
    Implements a stream reader over [byte_offset, byte_offset + slice_size)
    with seek/tell/read support, avoiding disk duplication during multi-part upload.
    """
    def __init__(
        self,
        file_path: Union[str, Path],
        byte_offset: int,
        slice_size: int,
        name: Optional[str] = None
    ):
        self.file_path = Path(file_path).resolve()
        self.byte_offset = byte_offset
        self.slice_size = slice_size
        self._name = name or f"{self.file_path.stem}.part_{byte_offset // max(1, slice_size)}{self.file_path.suffix}"
        self._file: Optional[io.BufferedReader] = None
        self._pos = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def size(self) -> int:
        return self.slice_size

    def __len__(self) -> int:
        return self.slice_size

    def open(self):
        if self._file is None or self._file.closed:
            self._file = open(self.file_path, "rb")
            self._file.seek(self.byte_offset + self._pos)
        return self

    def close(self):
        if self._file and not self._file.closed:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def read(self, size: int = -1) -> bytes:
        if self._file is None or self._file.closed:
            self.open()
        if self._pos >= self.slice_size:
            return b""
        if size < 0:
            to_read = self.slice_size - self._pos
        else:
            to_read = min(size, self.slice_size - self._pos)

        target_abs = self.byte_offset + self._pos
        if self._file.tell() != target_abs:
            self._file.seek(target_abs)

        chunk = self._file.read(to_read)
        self._pos += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            new_pos = offset
        elif whence == os.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == os.SEEK_END:
            new_pos = self.slice_size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        self._pos = max(0, min(new_pos, self.slice_size))
        if self._file and not self._file.closed:
            self._file.seek(self.byte_offset + self._pos)
        return self._pos

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False


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
    output_dir: Optional[Path] = None,
    precomputed_sha: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Checks if a file exceeds max_part_bytes (default 1,887,436,800 bytes = 900 * 2 MiB).
    If <= max_part_bytes: returns a single part descriptor.
    If > max_part_bytes:
      - If output_dir is provided: slices the file into physical part files on disk.
      - If output_dir is None: generates zero-copy virtual bounded file slices (BoundedFileSlice),
        avoiding multi-gigabyte disk duplication while computing exact part SHA-256 hashes.
    """
    total_size = os.path.getsize(file_path)
    if total_size <= max_part_bytes:
        sha = precomputed_sha or calculate_sha256(file_path)
        part_slice = BoundedFileSlice(file_path, 0, total_size)
        return [{
            "part_index": 0,
            "byte_offset": 0,
            "part_size_bytes": total_size,
            "part_sha256": sha,
            "file_path": str(file_path),
            "slice": part_slice
        }]

    parts = []
    part_index = 0
    byte_offset = 0

    # Physical slicing if output_dir is explicitly specified
    if output_dir is not None:
        dest_dir = output_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        created_part_paths: List[Path] = []
        try:
            with open(file_path, "rb") as src:
                while byte_offset < total_size:
                    slice_size = min(max_part_bytes, total_size - byte_offset)
                    part_filename = f"{file_path.stem}.part_{part_index}{file_path.suffix}"
                    part_path = dest_dir / part_filename
                    created_part_paths.append(part_path)

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

                    # I-3: never let a truncated/unreadable source spin the outer loop forever
                    if bytes_written != slice_size:
                        raise IOError(
                            f"Unexpected EOF while splitting '{file_path.name}' "
                            f"(part {part_index}: wrote {bytes_written} of {slice_size} bytes; source truncated?)"
                        )

                    part_slice = BoundedFileSlice(part_path, 0, bytes_written, name=part_filename)
                    parts.append({
                        "part_index": part_index,
                        "byte_offset": byte_offset,
                        "part_size_bytes": bytes_written,
                        "part_sha256": h.hexdigest(),
                        "file_path": str(part_path),
                        "slice": part_slice
                    })
                    byte_offset += bytes_written
                    part_index += 1
            return parts
        except Exception:
            for p in created_part_paths:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            raise

    # Zero-Copy Virtual Slicing: no physical .part files written to disk
    with open(file_path, "rb") as src:
        while byte_offset < total_size:
            slice_size = min(max_part_bytes, total_size - byte_offset)
            part_filename = f"{file_path.stem}.part_{part_index}{file_path.suffix}"

            # Compute part SHA-256 via streaming read from source
            h = hashlib.sha256()
            bytes_read = 0
            src.seek(byte_offset)
            while bytes_read < slice_size:
                read_len = min(1024 * 1024, slice_size - bytes_read)
                buffer = src.read(read_len)
                if not buffer:
                    break
                h.update(buffer)
                bytes_read += len(buffer)

            if bytes_read != slice_size:
                raise IOError(
                    f"Unexpected EOF while splitting '{file_path.name}' "
                    f"(part {part_index}: read {bytes_read} of {slice_size} bytes; source truncated?)"
                )

            part_slice = BoundedFileSlice(file_path, byte_offset, slice_size, name=part_filename)
            parts.append({
                "part_index": part_index,
                "byte_offset": byte_offset,
                "part_size_bytes": slice_size,
                "part_sha256": h.hexdigest(),
                "file_path": str(file_path),
                "slice": part_slice
            })
            byte_offset += slice_size
            part_index += 1

    return parts
