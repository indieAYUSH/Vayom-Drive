import os
import asyncio
import json
import shutil
import logging
import time
import uuid
import mimetypes
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator, List, Union

from .base import BaseStorageAdapter
from ...config import settings
from ...workstation.security import is_contained
from ...db.connection import get_db
from ...index.metadata import (
    detect_file_category,
    generate_webp_thumbnail,
    extract_image_exif,
    extract_video_metadata
)
from ...compression.splitter import calculate_sha256

logger = logging.getLogger("vayom.storage.nas")

IGNORED_DIRS = {".vayom", ".git", "@eadir", "#recycle", ".trash", ".trash-1000", "__pycache__", "node_modules"}
IGNORED_FILES = {".ds_store", "thumbs.db", "desktop.ini"}


class NASStorageAdapter(BaseStorageAdapter):
    """
    NAS Storage Adapter:
    - Native unsplit continuous files (Constraint 1)
    - Relative POSIX storage_uri sandboxed inside nas_root (Constraint 5)
    - Non-blocking async I/O via asyncio.to_thread
    - Clean human-readable paths with collision guard
    - Zero-copy in-place indexing & automatic folder-to-album mapping
    - Atomic catalog snapshots (.vayom/index.json) with single-flight locking (Constraint 2)
    - Safe deletion verifying DB reference count before physical unlinking
    """
    backend_name: str = "nas"

    def __init__(self, nas_root: Optional[Union[str, Path]] = None):
        root_path = nas_root or getattr(settings, "NAS_VAULT_ROOT", None) or (settings.DATA_DIR / "nas_vault")
        self.nas_root = Path(os.path.realpath(str(root_path)))
        self.nas_root.mkdir(parents=True, exist_ok=True)
        self._catalog_lock = asyncio.Lock()

    def _resolve_and_verify(self, storage_uri: str) -> Path:
        """
        Resolves storage_uri relative to nas_root and verifies sandbox containment.
        Rejects path traversal attempts.
        """
        if not storage_uri or not storage_uri.strip():
            raise ValueError("storage_uri cannot be empty")

        clean_rel = storage_uri.strip().replace("\\", "/")
        while clean_rel.startswith("/"):
            clean_rel = clean_rel[1:]

        target = Path(os.path.realpath(os.path.join(self.nas_root, clean_rel)))
        if not is_contained(str(target), str(self.nas_root)):
            raise PermissionError(
                f"Path traversal detected: '{storage_uri}' resolves outside NAS root '{self.nas_root}'"
            )
        return target

    async def upload(
        self,
        local_path: Union[str, Path],
        file_id: str,
        filename: str,
        part_index: int = 0,
        total_parts: int = 1,
        byte_offset: int = 0,
        part_size: int = 0,
        sha256: str = "",
        mime_type: str = "application/octet-stream",
        category: str = "other",
        is_proxy: bool = False,
        proxy_of: Optional[str] = None,
        proxy_profile: Optional[str] = None,
        progress_callback: Optional[Any] = None,
        folder_name: Optional[str] = None,
        in_place: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Stores file on NAS as a single continuous unsplit file.
        Supports zero-copy in-place referencing if file is already inside nas_root.
        """
        local_real = os.path.realpath(str(local_path))

        # 1. Determine target relative directory & candidate destination
        safe_filename = Path(filename).name or Path(local_path).name or f"{file_id}.bin"
        if is_proxy:
            rel_dir = os.path.join(".vayom", "proxies", file_id)
        elif folder_name:
            clean_folder = os.path.normpath(folder_name).replace("\\", "/").strip("/")
            rel_dir = clean_folder
        else:
            rel_dir = ""

        candidate_rel = os.path.join(rel_dir, safe_filename).replace("\\", "/")
        dest_path = self._resolve_and_verify(candidate_rel)

        # 2. Zero-Copy In-Place Check (Constraint 5)
        # If in_place is explicitly requested, or if the source file already resides at dest_path
        if in_place or (is_contained(local_real, str(self.nas_root)) and local_real == os.path.realpath(str(dest_path))):
            rel_path = os.path.relpath(local_real, str(self.nas_root)).replace("\\", "/")
            verified = self._resolve_and_verify(rel_path)
            file_stat = await asyncio.to_thread(verified.stat)
            return {
                "storage_backend": self.backend_name,
                "storage_uri": rel_path,
                "part_index": 0,
                "total_parts": 1,
                "byte_offset": 0,
                "part_size_bytes": file_stat.st_size,
                "telegram_message_id": 0,
                "telegram_channel_id": "",
            }

        # 3. Collision Guard
        if await asyncio.to_thread(dest_path.exists):
            stem = Path(safe_filename).stem
            ext = Path(safe_filename).suffix
            safe_filename = f"{stem}_{file_id[:8]}{ext}"
            candidate_rel = os.path.join(rel_dir, safe_filename).replace("\\", "/")
            dest_path = self._resolve_and_verify(candidate_rel)

        # 4. Atomic Non-Blocking Write
        def _write_file() -> int:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = dest_path.parent / f".tmp_{file_id[:8]}_{dest_path.name}"
            with open(local_path, "rb") as src, open(tmp_path, "wb") as dst:
                while True:
                    chunk = src.read(512 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            os.replace(tmp_path, dest_path)
            return dest_path.stat().st_size

        actual_size = await asyncio.to_thread(_write_file)
        storage_uri = os.path.relpath(str(dest_path), str(self.nas_root)).replace("\\", "/")

        return {
            "storage_backend": self.backend_name,
            "storage_uri": storage_uri,
            "part_index": 0,
            "total_parts": 1,
            "byte_offset": 0,
            "part_size_bytes": actual_size,
            "telegram_message_id": 0,
            "telegram_channel_id": "",
        }

    async def stream_bytes(
        self,
        storage_uri: str,
        offset: int = 0,
        limit: Optional[int] = None,
        chunk_size: int = 512 * 1024,
        **kwargs
    ) -> AsyncIterator[bytes]:
        """
        Streams contiguous byte slices from the local/NAS filesystem
        using non-blocking asyncio.to_thread chunks.
        """
        target_path = self._resolve_and_verify(storage_uri)

        def _get_size():
            return target_path.stat().st_size if target_path.exists() else 0

        total_size = await asyncio.to_thread(_get_size)
        if total_size <= 0 or offset >= total_size:
            return

        bytes_to_read = total_size - offset if limit is None else min(limit, total_size - offset)
        curr_offset = offset
        bytes_remaining = bytes_to_read

        while bytes_remaining > 0:
            read_len = min(chunk_size, bytes_remaining)

            def _read_chunk(off: int, count: int) -> bytes:
                with open(target_path, "rb") as f:
                    f.seek(off)
                    return f.read(count)

            data = await asyncio.to_thread(_read_chunk, curr_offset, read_len)
            if not data:
                break
            yield data
            curr_offset += len(data)
            bytes_remaining -= len(data)

    async def delete_parts(self, parts: List[Dict[str, Any]]) -> int:
        """
        Permanently deletes file from NAS only if no other active records reference it.
        """
        deleted_count = 0
        for part in parts:
            storage_uri = part.get("storage_uri")
            if not storage_uri:
                continue
            try:
                target_path = self._resolve_and_verify(storage_uri)

                # Reference count check
                async with get_db() as db:
                    cur = await db.execute(
                        "SELECT COUNT(*) FROM file_parts WHERE storage_uri = ? AND storage_backend = 'nas'",
                        (storage_uri,)
                    )
                    count = (await cur.fetchone())[0]

                if count <= 1:
                    def _unlink():
                        if target_path.exists():
                            target_path.unlink()
                            return True
                        return False

                    unlinked = await asyncio.to_thread(_unlink)
                    if unlinked:
                        deleted_count += 1
                else:
                    logger.info(f"NAS file '{storage_uri}' retained; referenced by {count} records.")
            except Exception as e:
                logger.warning(f"Error unlinking NAS part '{storage_uri}': {e}")
        return deleted_count

    async def health_check(self) -> Dict[str, Any]:
        """
        Non-blocking check verifying NAS root directory availability, permissions, and disk space.
        """
        def _check():
            exists = self.nas_root.exists()
            is_dir = self.nas_root.is_dir() if exists else False
            writable = os.access(self.nas_root, os.W_OK) if exists else False
            usage = shutil.disk_usage(self.nas_root) if exists else None
            return {
                "status": "online" if exists and is_dir and writable else "error",
                "backend": "nas",
                "root": str(self.nas_root),
                "exists": exists,
                "writable": writable,
                "free_bytes": usage.free if usage else 0,
                "total_bytes": usage.total if usage else 0,
            }
        return await asyncio.to_thread(_check)

    async def sync_directory(self) -> Dict[str, Any]:
        """
        Zero-Copy In-Place Indexing & Automatic Folder (Album) Mapping:
        1. Scans nas_root recursively, ignoring hidden/system directories (.git, .vayom, @eaDir, etc.)
        2. Leaves discovered files in-place on disk.
        3. Maps subfolder names (e.g. Summer_Vacation) directly to the 'albums' table and links via 'file_albums'.
        4. Updates .vayom/index.json catalog snapshot.
        """
        def _scan_filesystem():
            found = []
            for root, dirs, files in os.walk(self.nas_root):
                # Prune ignored directories in-place
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in IGNORED_DIRS and not d.startswith(".") and not d.startswith("@") and not d.startswith("#")
                ]
                for f in files:
                    lower_f = f.lower()
                    if lower_f in IGNORED_FILES or lower_f.startswith(".") or lower_f.endswith(".tmp"):
                        continue
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, str(self.nas_root)).replace("\\", "/")
                    rel_dir = os.path.dirname(rel_p)
                    found.append((full_p, rel_p, rel_dir, f))
            return found

        entries = await asyncio.to_thread(_scan_filesystem)
        indexed_count = 0
        mapped_albums = set()

        async with get_db() as db:
            for full_p, rel_p, rel_dir, filename in entries:
                # Check if already indexed
                cur = await db.execute(
                    "SELECT file_id FROM file_parts WHERE storage_backend = 'nas' AND storage_uri = ?",
                    (rel_p,)
                )
                if await cur.fetchone():
                    continue

                f_stat = await asyncio.to_thread(os.stat, full_p)
                f_size = f_stat.st_size
                f_sha256 = await asyncio.to_thread(calculate_sha256, full_p)
                mime = mimetypes.guess_type(full_p)[0] or "application/octet-stream"
                cat = detect_file_category(mime, filename)
                file_id = f"nas-{uuid.uuid4().hex[:12]}"

                # Thumbnail generation
                thumb_path = settings.THUMBNAIL_DIR / f"{file_id}.webp"
                has_thumb = False
                if cat == "image":
                    has_thumb = await asyncio.to_thread(generate_webp_thumbnail, full_p, thumb_path)

                # Commit to files
                await db.execute(
                    """
                    INSERT INTO files (
                        id, filename, mime_type, file_category, size_bytes, sha256,
                        is_split, total_parts, has_thumbnail, thumbnail_path,
                        storage_backend, upload_status
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?, 'nas', 'completed')
                    """,
                    (
                        file_id, filename, mime, cat, f_size, f_sha256,
                        1 if has_thumb else 0, str(thumb_path) if has_thumb else None
                    )
                )

                # Commit to file_parts (continuous unsplit file)
                part_id = str(uuid.uuid4())
                await db.execute(
                    """
                    INSERT INTO file_parts (
                        id, file_id, part_index, byte_offset, part_size_bytes,
                        part_sha256, telegram_message_id, storage_backend, storage_uri
                    ) VALUES (?, ?, 0, 0, ?, ?, 0, 'nas', ?)
                    """,
                    (part_id, file_id, f_size, f_sha256, rel_p)
                )

                # Auto Folder (Album) Mapping
                if rel_dir and rel_dir != ".":
                    folder_name = rel_dir.split("/")[0]
                    mapped_albums.add(folder_name)
                    # Check/create album
                    a_cur = await db.execute("SELECT id FROM albums WHERE name = ?", (folder_name,))
                    a_row = await a_cur.fetchone()
                    if a_row:
                        album_id = a_row[0]
                    else:
                        album_id = f"alb-{uuid.uuid4().hex[:12]}"
                        await db.execute(
                            "INSERT INTO albums (id, name, description) VALUES (?, ?, ?)",
                            (album_id, folder_name, f"NAS Folder: {folder_name}")
                        )
                    await db.execute(
                        "INSERT OR IGNORE INTO file_albums (file_id, album_id) VALUES (?, ?)",
                        (file_id, album_id)
                    )

                indexed_count += 1

            await db.commit()

        # Update catalog snapshot
        await self.save_catalog_snapshot()

        return {
            "indexed_files": indexed_count,
            "mapped_albums": list(mapped_albums),
            "total_scanned": len(entries)
        }

    async def save_catalog_snapshot(self) -> None:
        """
        Atomic snapshot write: writes .vayom/index.json via temporary file + os.replace
        guarded by asyncio single-flight lock (_catalog_lock).
        """
        async with self._catalog_lock:
            async with get_db() as db:
                cur = await db.execute(
                    """
                    SELECT f.id, f.filename, f.mime_type, f.file_category, f.size_bytes,
                           f.sha256, f.storage_backend, f.created_at, fp.storage_uri,
                           GROUP_CONCAT(a.name) as album_names
                    FROM files f
                    JOIN file_parts fp ON f.id = fp.file_id
                    LEFT JOIN file_albums fa ON f.id = fa.file_id
                    LEFT JOIN albums a ON fa.album_id = a.id
                    WHERE f.is_deleted = 0 AND f.storage_backend = 'nas'
                    GROUP BY f.id
                    """
                )
                rows = await cur.fetchall()
                snapshot = {
                    "version": 1,
                    "exported_at": time.time(),
                    "nas_root": self.nas_root.name,
                    "files": [dict(r) for r in rows]
                }

            def _atomic_write():
                vayom_dir = self.nas_root / ".vayom"
                vayom_dir.mkdir(parents=True, exist_ok=True)
                snapshot_file = vayom_dir / "index.json"
                tmp_file = vayom_dir / f"index.json.tmp.{uuid.uuid4().hex[:8]}"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, indent=2)
                os.replace(tmp_file, snapshot_file)

            await asyncio.to_thread(_atomic_write)

    async def load_catalog_snapshot(self) -> int:
        """
        Loads .vayom/index.json into local SQLite catalog for instant multi-device sync.
        """
        vayom_dir = self.nas_root / ".vayom"
        snapshot_file = vayom_dir / "index.json"
        if not await asyncio.to_thread(snapshot_file.exists):
            return 0

        def _read_snapshot():
            with open(snapshot_file, "r", encoding="utf-8") as f:
                return json.load(f)

        data = await asyncio.to_thread(_read_snapshot)
        files = data.get("files", [])
        restored = 0

        async with get_db() as db:
            for item in files:
                file_id = item["id"]
                cur = await db.execute("SELECT id FROM files WHERE id = ?", (file_id,))
                if not await cur.fetchone():
                    await db.execute(
                        """
                        INSERT INTO files (
                            id, filename, mime_type, file_category, size_bytes, sha256,
                            is_split, total_parts, storage_backend, upload_status
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, 'nas', 'completed')
                        """,
                        (
                            file_id, item["filename"], item["mime_type"],
                            item["file_category"], item["size_bytes"], item["sha256"]
                        )
                    )
                    part_id = str(uuid.uuid4())
                    await db.execute(
                        """
                        INSERT INTO file_parts (
                            id, file_id, part_index, byte_offset, part_size_bytes,
                            part_sha256, telegram_message_id, storage_backend, storage_uri
                        ) VALUES (?, ?, 0, 0, ?, ?, 0, 'nas', ?)
                        """,
                        (part_id, file_id, item["size_bytes"], item["sha256"], item["storage_uri"])
                    )

                    album_names = item.get("album_names")
                    if album_names:
                        for alb_name in album_names.split(","):
                            alb_name = alb_name.strip()
                            if not alb_name:
                                continue
                            a_cur = await db.execute("SELECT id FROM albums WHERE name = ?", (alb_name,))
                            a_row = await a_cur.fetchone()
                            if a_row:
                                alb_id = a_row[0]
                            else:
                                alb_id = f"alb-{uuid.uuid4().hex[:12]}"
                                await db.execute(
                                    "INSERT INTO albums (id, name, description) VALUES (?, ?, ?)",
                                    (alb_id, alb_name, f"NAS Folder: {alb_name}")
                                )
                            await db.execute(
                                "INSERT OR IGNORE INTO file_albums (file_id, album_id) VALUES (?, ?)",
                                (file_id, alb_id)
                            )
                    restored += 1
            await db.commit()

        return restored
