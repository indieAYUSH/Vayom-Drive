import os
import asyncio
import json
import logging
import mimetypes
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator, List, Union

import httpx

from .base import BaseStorageAdapter
from ...config import settings
from ...db.connection import get_db
from ...index.metadata import detect_file_category

logger = logging.getLogger("vayom.storage.nextcloud")


class NextcloudWebDAVAdapter(BaseStorageAdapter):
    """
    Nextcloud / ownCloud WebDAV Storage Adapter:
    - Native unsplit continuous files (Constraint 1)
    - Recursive parent directory creation via WebDAV MKCOL (Constraint 3)
    - Async chunked streaming uploads avoiding RAM buffer bloat (Constraint 3)
    - Range-based byte streaming with inclusive end offsets (Constraint 3)
    - Concurrency-safe catalog snapshot via atomic WebDAV MOVE (Constraint 2)
    - Reference-counting check before WebDAV DELETE
    - App password redaction in all outputs (Constraint 6)
    """
    backend_name: str = "nextcloud"

    def __init__(
        self,
        webdav_url: Optional[str] = None,
        username: Optional[str] = None,
        app_password: Optional[str] = None,
        vault_prefix: Optional[str] = None,
    ):
        raw_url = webdav_url or getattr(settings, "NEXTCLOUD_WEBDAV_URL", "")
        self.webdav_url = raw_url.rstrip("/") if raw_url else ""
        self.username = username or getattr(settings, "NEXTCLOUD_USERNAME", "")
        self.app_password = app_password or getattr(settings, "NEXTCLOUD_APP_PASSWORD", "")
        self.vault_prefix = (vault_prefix or getattr(settings, "NEXTCLOUD_VAULT_PREFIX", "vayom_vault")).strip("/")
        self._catalog_lock = asyncio.Lock()

    @property
    def _auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self.username, self.app_password)

    def _build_url(self, rel_path: str = "") -> str:
        clean = rel_path.lstrip("/").replace("\\", "/")
        if not self.webdav_url:
            return f"/{clean}"
        if clean:
            return f"{self.webdav_url}/{clean}"
        return self.webdav_url

    async def _ensure_parent_dirs(self, client: httpx.AsyncClient, object_path: str) -> None:
        """
        Recursively creates all missing parent directories via WebDAV MKCOL (Constraint 3)
        to prevent 409 Conflict.
        """
        parts = object_path.strip("/").split("/")
        if len(parts) <= 1:
            return

        current_path = ""
        for folder in parts[:-1]:
            current_path = f"{current_path}/{folder}" if current_path else folder
            mkcol_url = self._build_url(current_path)
            try:
                resp = await client.request("MKCOL", mkcol_url, auth=self._auth)
                # 201 Created = success; 405 Method Not Allowed = folder already exists
                if resp.status_code not in (201, 405):
                    logger.debug(f"MKCOL for {current_path} returned {resp.status_code}")
            except Exception as e:
                logger.debug(f"MKCOL error for {current_path}: {e}")

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
        **kwargs
    ) -> Dict[str, Any]:
        """
        Uploads file as a single continuous unsplit file via WebDAV PUT using async streaming generator.
        """
        local_p = Path(local_path)
        file_size = local_p.stat().st_size
        safe_filename = Path(filename).name or f"{file_id}.bin"

        if is_proxy:
            rel_path = f"{self.vault_prefix}/.vayom/proxies/{file_id}/{safe_filename}"
        elif folder_name:
            clean_folder = folder_name.strip("/").replace("\\", "/")
            rel_path = f"{self.vault_prefix}/{clean_folder}/{safe_filename}"
        else:
            rel_path = f"{self.vault_prefix}/{safe_filename}"

        target_url = self._build_url(rel_path)

        async with httpx.AsyncClient(timeout=600.0) as client:
            # 1. MKCOL parent directories (Constraint 3)
            await self._ensure_parent_dirs(client, rel_path)

            # 2. Async file chunk generator (Constraint 3: prevents loading entire files into RAM)
            async def _chunk_generator():
                bytes_sent = 0
                chunk_sz = 512 * 1024
                with open(local_p, "rb") as f:
                    while True:
                        chunk = f.read(chunk_sz)
                        if not chunk:
                            break
                        bytes_sent += len(chunk)
                        if progress_callback:
                            try:
                                progress_callback(bytes_sent, file_size)
                            except Exception:
                                pass
                        yield chunk

            headers = {
                "Content-Type": mime_type or "application/octet-stream",
                "Content-Length": str(file_size),
            }

            resp = await client.put(
                target_url,
                content=_chunk_generator(),
                headers=headers,
                auth=self._auth
            )

            if resp.status_code not in (200, 201, 204):
                raise RuntimeError(
                    f"Nextcloud WebDAV PUT failed ({resp.status_code}): {resp.text}"
                )

        return {
            "storage_backend": self.backend_name,
            "storage_uri": rel_path,
            "part_index": 0,
            "total_parts": 1,
            "byte_offset": 0,
            "part_size_bytes": file_size,
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
        Streams contiguous byte ranges from Nextcloud WebDAV using HTTP Range headers.
        HTTP Range is inclusive: end = offset + limit - 1 (Constraint 3).
        """
        url = self._build_url(storage_uri)
        headers = {}
        if limit is not None:
            end_byte = offset + limit - 1
            headers["Range"] = f"bytes={offset}-{end_byte}"
        elif offset > 0:
            headers["Range"] = f"bytes={offset}-"

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("GET", url, headers=headers, auth=self._auth) as response:
                if response.status_code not in (200, 206):
                    logger.warning(
                        f"Nextcloud stream failed for {storage_uri}: status {response.status_code}"
                    )
                    return
                async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                    yield chunk

    async def delete_parts(self, parts: List[Dict[str, Any]]) -> int:
        """Deletes file via WebDAV DELETE only if no other active records reference it."""
        deleted_count = 0
        async with httpx.AsyncClient(timeout=60.0) as client:
            for part in parts:
                storage_uri = part.get("storage_uri")
                if not storage_uri:
                    continue

                async with get_db() as db:
                    cur = await db.execute(
                        "SELECT COUNT(*) FROM file_parts WHERE storage_uri = ? AND storage_backend = 'nextcloud'",
                        (storage_uri,)
                    )
                    count = (await cur.fetchone())[0]

                if count <= 1:
                    url = self._build_url(storage_uri)
                    try:
                        resp = await client.delete(url, auth=self._auth)
                        if resp.status_code in (200, 204):
                            deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete Nextcloud file {storage_uri}: {e}")
        return deleted_count

    async def health_check(self) -> Dict[str, Any]:
        """Verifies WebDAV connectivity while redacting password (Constraint 6)."""
        masked_pass = f"{self.app_password[:3]}***" if len(self.app_password) > 3 else "***"
        url = self._build_url(self.vault_prefix)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.request("PROPFIND", url, headers={"Depth": "0"}, auth=self._auth)
                status = "online" if resp.status_code in (200, 207) else "error"
                return {
                    "status": status,
                    "backend": "nextcloud",
                    "webdav_url": self.webdav_url,
                    "username": self.username,
                    "app_password": masked_pass,
                    "status_code": resp.status_code,
                }
        except Exception as e:
            return {
                "status": "error",
                "backend": "nextcloud",
                "webdav_url": self.webdav_url,
                "username": self.username,
                "app_password": masked_pass,
                "error": str(e),
            }

    async def save_catalog_snapshot(self) -> None:
        """
        Concurrency-safe atomic snapshot (Constraint 2):
        Writes .vayom/index.json.tmp.{uuid} via PUT, then moves to .vayom/index.json via MOVE.
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
                    WHERE f.is_deleted = 0 AND f.storage_backend = 'nextcloud'
                    GROUP BY f.id
                    """
                )
                rows = await cur.fetchall()
                snapshot = {
                    "version": 1,
                    "exported_at": time.time(),
                    "backend": "nextcloud",
                    "files": [dict(r) for r in rows]
                }

            snapshot_bytes = json.dumps(snapshot, indent=2).encode("utf-8")
            target_rel = f"{self.vault_prefix}/.vayom/index.json"
            tmp_rel = f"{self.vault_prefix}/.vayom/index.json.tmp.{uuid.uuid4().hex[:8]}"

            target_url = self._build_url(target_rel)
            tmp_url = self._build_url(tmp_rel)

            async with httpx.AsyncClient(timeout=60.0) as client:
                await self._ensure_parent_dirs(client, tmp_rel)
                put_resp = await client.put(
                    tmp_url,
                    content=snapshot_bytes,
                    headers={"Content-Type": "application/json"},
                    auth=self._auth
                )
                if put_resp.status_code not in (200, 201, 204):
                    raise RuntimeError(f"WebDAV snapshot PUT failed: {put_resp.text}")

                # Atomic MOVE
                move_resp = await client.request(
                    "MOVE",
                    tmp_url,
                    headers={"Destination": target_url, "Overwrite": "T"},
                    auth=self._auth
                )
                if move_resp.status_code not in (200, 201, 204):
                    raise RuntimeError(f"WebDAV snapshot MOVE failed: {move_resp.text}")

    async def load_catalog_snapshot(self) -> int:
        """Restores catalog snapshot from Nextcloud WebDAV into local SQLite."""
        target_rel = f"{self.vault_prefix}/.vayom/index.json"
        target_url = self._build_url(target_rel)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(target_url, auth=self._auth)
            if resp.status_code != 200:
                return 0
            data = resp.json()

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
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, 'nextcloud', 'completed')
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
                        ) VALUES (?, ?, 0, 0, ?, ?, 0, 'nextcloud', ?)
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
                                    (alb_id, alb_name, f"Nextcloud Folder: {alb_name}")
                                )
                            await db.execute(
                                "INSERT OR IGNORE INTO file_albums (file_id, album_id) VALUES (?, ?)",
                                (file_id, alb_id)
                            )
                    restored += 1
            await db.commit()
        return restored
