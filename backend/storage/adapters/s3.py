import os
import asyncio
import hashlib
import hmac
import json
import logging
import mimetypes
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator, List, Union

import httpx

from .base import BaseStorageAdapter
from ...config import settings
from ...db.connection import get_db
from ...index.metadata import detect_file_category

logger = logging.getLogger("vayom.storage.s3")

MULTIPART_THRESHOLD = 100 * 1024 * 1024  # 100 MB
MULTIPART_CHUNK_SIZE = 16 * 1024 * 1024  # 16 MB chunks


def sign_v4_headers(
    method: str,
    url: str,
    headers: Dict[str, str],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str = "us-east-1",
    service: str = "s3",
    timestamp: Optional[datetime] = None,
) -> Dict[str, str]:
    """
    Computes AWS Signature Version 4 headers for standard S3 / R2 / MinIO requests.
    Zero external dependencies — pure Python hashlib and hmac.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    amz_date = timestamp.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = timestamp.strftime("%Y%m%d")

    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc

    signed_headers_map = {k.lower(): v.strip() for k, v in headers.items()}
    signed_headers_map["host"] = host
    signed_headers_map["x-amz-date"] = amz_date
    signed_headers_map["x-amz-content-sha256"] = payload_hash

    canonical_uri = urllib.parse.quote(parsed.path or "/", safe="/")
    
    # Canonical Query String
    if parsed.query:
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        params.sort(key=lambda x: x[0])
        canonical_qs = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in params
        )
    else:
        canonical_qs = ""

    sorted_header_keys = sorted(signed_headers_map.keys())
    canonical_headers = "".join(f"{k}:{signed_headers_map[k]}\n" for k in sorted_header_keys)
    signed_headers_str = ";".join(sorted_header_keys)

    canonical_request = (
        f"{method.upper()}\n"
        f"{canonical_uri}\n"
        f"{canonical_qs}\n"
        f"{canonical_headers}\n"
        f"{signed_headers_str}\n"
        f"{payload_hash}"
    )

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n"
        f"{amz_date}\n"
        f"{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _sign(f"AWS4{secret_key}".encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")

    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth_header = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers_str}, Signature={signature}"
    )

    result_headers = dict(signed_headers_map)
    result_headers["authorization"] = auth_header
    return result_headers


class S3StorageAdapter(BaseStorageAdapter):
    """
    S3 / Cloudflare R2 / MinIO Storage Adapter:
    - Native unsplit continuous files (Constraint 1)
    - Multipart uploads for files > 100 MB stitching into single continuous S3 objects
    - Standard AWS SigV4 via async httpx
    - Range-based byte streaming
    - Concurrency-safe atomic catalog snapshots (.vayom/index.json) (Constraint 2)
    - Reference-counted safe deletion
    - Full secret key redaction (Constraint 6)
    """
    backend_name: str = "s3"

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        bucket_name: Optional[str] = None,
        region_name: Optional[str] = None,
        vault_prefix: Optional[str] = None,
    ):
        self.endpoint_url = (
            endpoint_url
            or getattr(settings, "S3_ENDPOINT_URL", "")
            or "https://s3.amazonaws.com"
        ).rstrip("/")
        self.access_key_id = access_key_id or getattr(settings, "S3_ACCESS_KEY_ID", "")
        self.secret_access_key = secret_access_key or getattr(settings, "S3_SECRET_ACCESS_KEY", "")
        self.bucket_name = bucket_name or getattr(settings, "S3_BUCKET_NAME", "")
        self.region_name = region_name or getattr(settings, "S3_REGION_NAME", "us-east-1")
        self.vault_prefix = (vault_prefix or getattr(settings, "S3_VAULT_PREFIX", "vayom_vault")).strip("/")
        self._catalog_lock = asyncio.Lock()

    def _build_url(self, key: str = "", query: str = "") -> str:
        """Constructs path-style or endpoint S3 URL."""
        clean_key = key.lstrip("/")
        if self.bucket_name:
            path = f"/{self.bucket_name}"
            if clean_key:
                path = f"{path}/{clean_key}"
        else:
            path = f"/{clean_key}" if clean_key else "/"

        url = f"{self.endpoint_url}{path}"
        if query:
            url = f"{url}?{query}"
        return url

    def _get_signed_headers(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        payload_hash: str = "UNSIGNED-PAYLOAD"
    ) -> Dict[str, str]:
        headers = headers or {}
        return sign_v4_headers(
            method=method,
            url=url,
            headers=headers,
            payload_hash=payload_hash,
            access_key=self.access_key_id,
            secret_key=self.secret_access_key,
            region=self.region_name,
        )

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
        Stores file on S3 as a single continuous unsplit object.
        Uses single PUT for <= 100 MB, S3 Multipart Upload for > 100 MB.
        """
        local_p = Path(local_path)
        file_size = local_p.stat().st_size

        # Determine target S3 object key
        safe_filename = Path(filename).name or f"{file_id}.bin"
        if is_proxy:
            object_key = f"{self.vault_prefix}/.vayom/proxies/{file_id}/{safe_filename}"
        elif folder_name:
            clean_folder = folder_name.strip("/").replace("\\", "/")
            object_key = f"{self.vault_prefix}/{clean_folder}/{safe_filename}"
        else:
            object_key = f"{self.vault_prefix}/{safe_filename}"

        if file_size > MULTIPART_THRESHOLD:
            await self._upload_multipart(local_p, object_key, file_size, mime_type, progress_callback)
        else:
            await self._upload_single(local_p, object_key, file_size, mime_type, progress_callback)

        return {
            "storage_backend": self.backend_name,
            "storage_uri": object_key,
            "part_index": 0,
            "total_parts": 1,
            "byte_offset": 0,
            "part_size_bytes": file_size,
            "telegram_message_id": 0,
            "telegram_channel_id": "",
        }

    async def _upload_single(
        self,
        local_path: Path,
        object_key: str,
        file_size: int,
        mime_type: str,
        progress_callback: Optional[Any] = None
    ) -> None:
        """Uploads file in a single PUT request with streaming body."""
        url = self._build_url(object_key)

        def _get_payload_hash() -> str:
            with open(local_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        payload_hash = await asyncio.to_thread(_get_payload_hash)
        headers = {
            "Content-Type": mime_type or "application/octet-stream",
            "Content-Length": str(file_size),
        }
        signed = self._get_signed_headers("PUT", url, headers, payload_hash=payload_hash)

        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(local_path, "rb") as f:
                content = f.read()
            resp = await client.put(url, content=content, headers=signed)
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"S3 PUT upload failed ({resp.status_code}): {resp.text}")

        if progress_callback:
            try:
                progress_callback(file_size, file_size)
            except Exception:
                pass

    async def _upload_multipart(
        self,
        local_path: Path,
        object_key: str,
        file_size: int,
        mime_type: str,
        progress_callback: Optional[Any] = None
    ) -> None:
        """Performs S3 Multipart Upload for files > 100 MB."""
        init_url = self._build_url(object_key, query="uploads")
        init_headers = self._get_signed_headers("POST", init_url, {"Content-Type": mime_type})

        async with httpx.AsyncClient(timeout=300.0) as client:
            init_resp = await client.post(init_url, headers=init_headers)
            if init_resp.status_code not in (200, 201):
                raise RuntimeError(f"S3 initiate multipart upload failed: {init_resp.text}")

            root = ET.fromstring(init_resp.text)
            upload_id = None
            for elem in root.iter():
                if elem.tag.endswith("UploadId"):
                    upload_id = elem.text
                    break
            if not upload_id:
                raise RuntimeError(f"Could not parse UploadId from S3 response: {init_resp.text}")

            parts: List[Dict[str, Any]] = []
            uploaded_bytes = 0

            try:
                with open(local_path, "rb") as f:
                    part_num = 1
                    while True:
                        chunk = f.read(MULTIPART_CHUNK_SIZE)
                        if not chunk:
                            break

                        part_hash = hashlib.sha256(chunk).hexdigest()
                        part_url = self._build_url(object_key, query=f"partNumber={part_num}&uploadId={upload_id}")
                        part_headers = self._get_signed_headers("PUT", part_url, {"Content-Length": str(len(chunk))}, payload_hash=part_hash)

                        part_resp = await client.put(part_url, content=chunk, headers=part_headers)
                        if part_resp.status_code not in (200, 201):
                            raise RuntimeError(f"S3 upload part {part_num} failed: {part_resp.text}")

                        etag = part_resp.headers.get("etag", "").strip('"')
                        parts.append({"PartNumber": part_num, "ETag": f'"{etag}"'})

                        uploaded_bytes += len(chunk)
                        if progress_callback:
                            try:
                                progress_callback(uploaded_bytes, file_size)
                            except Exception:
                                pass
                        part_num += 1

                # Complete Multipart Upload
                complete_xml = "<CompleteMultipartUpload>" + "".join(
                    f"<Part><PartNumber>{p['PartNumber']}</PartNumber><ETag>{p['ETag']}</ETag></Part>"
                    for p in parts
                ) + "</CompleteMultipartUpload>"
                complete_bytes = complete_xml.encode("utf-8")
                complete_hash = hashlib.sha256(complete_bytes).hexdigest()

                complete_url = self._build_url(object_key, query=f"uploadId={upload_id}")
                complete_headers = self._get_signed_headers(
                    "POST", complete_url,
                    {"Content-Type": "application/xml", "Content-Length": str(len(complete_bytes))},
                    payload_hash=complete_hash
                )
                complete_resp = await client.post(complete_url, content=complete_bytes, headers=complete_headers)
                if complete_resp.status_code not in (200, 201):
                    raise RuntimeError(f"S3 complete multipart upload failed: {complete_resp.text}")
            except Exception as e:
                # Abort Multipart Upload on failure
                abort_url = self._build_url(object_key, query=f"uploadId={upload_id}")
                abort_headers = self._get_signed_headers("DELETE", abort_url)
                await client.delete(abort_url, headers=abort_headers)
                raise e

    async def stream_bytes(
        self,
        storage_uri: str,
        offset: int = 0,
        limit: Optional[int] = None,
        chunk_size: int = 512 * 1024,
        **kwargs
    ) -> AsyncIterator[bytes]:
        """Streams byte range directly from S3 using Range HTTP header."""
        url = self._build_url(storage_uri)
        headers = {}
        if limit is not None:
            end_byte = offset + limit - 1
            headers["Range"] = f"bytes={offset}-{end_byte}"
        elif offset > 0:
            headers["Range"] = f"bytes={offset}-"

        signed = self._get_signed_headers("GET", url, headers, payload_hash="UNSIGNED-PAYLOAD")

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("GET", url, headers=signed) as response:
                if response.status_code not in (200, 206):
                    logger.warning(f"S3 stream failed for {storage_uri}: status {response.status_code}")
                    return
                async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                    yield chunk

    async def delete_parts(self, parts: List[Dict[str, Any]]) -> int:
        """Deletes S3 object if no other record references it."""
        deleted_count = 0
        async with httpx.AsyncClient(timeout=60.0) as client:
            for part in parts:
                storage_uri = part.get("storage_uri")
                if not storage_uri:
                    continue

                async with get_db() as db:
                    cur = await db.execute(
                        "SELECT COUNT(*) FROM file_parts WHERE storage_uri = ? AND storage_backend = 's3'",
                        (storage_uri,)
                    )
                    count = (await cur.fetchone())[0]

                if count <= 1:
                    url = self._build_url(storage_uri)
                    signed = self._get_signed_headers("DELETE", url)
                    try:
                        resp = await client.delete(url, headers=signed)
                        if resp.status_code in (200, 204):
                            deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete S3 object {storage_uri}: {e}")
        return deleted_count

    async def health_check(self) -> Dict[str, Any]:
        """Checks S3 bucket accessibility while redacting credentials (Constraint 6)."""
        masked_key = (
            f"{self.access_key_id[:4]}***"
            if len(self.access_key_id) > 4
            else "***"
        )
        url = self._build_url(query="max-keys=1")
        signed = self._get_signed_headers("GET", url)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=signed)
                status = "online" if resp.status_code in (200, 204) else "error"
                return {
                    "status": status,
                    "backend": "s3",
                    "endpoint": self.endpoint_url,
                    "bucket": self.bucket_name,
                    "access_key_id": masked_key,
                    "status_code": resp.status_code,
                }
        except Exception as e:
            return {
                "status": "error",
                "backend": "s3",
                "endpoint": self.endpoint_url,
                "bucket": self.bucket_name,
                "access_key_id": masked_key,
                "error": str(e),
            }

    async def save_catalog_snapshot(self) -> None:
        """Atomic snapshot write to S3 (.vayom/index.json) with single-flight lock."""
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
                    WHERE f.is_deleted = 0 AND f.storage_backend = 's3'
                    GROUP BY f.id
                    """
                )
                rows = await cur.fetchall()
                snapshot = {
                    "version": 1,
                    "exported_at": time.time(),
                    "backend": "s3",
                    "files": [dict(r) for r in rows]
                }

            snapshot_bytes = json.dumps(snapshot, indent=2).encode("utf-8")
            object_key = f"{self.vault_prefix}/.vayom/index.json"
            url = self._build_url(object_key)
            payload_hash = hashlib.sha256(snapshot_bytes).hexdigest()
            signed = self._get_signed_headers(
                "PUT", url,
                {"Content-Type": "application/json", "Content-Length": str(len(snapshot_bytes))},
                payload_hash=payload_hash
            )

            async with httpx.AsyncClient(timeout=60.0) as client:
                await client.put(url, content=snapshot_bytes, headers=signed)

    async def load_catalog_snapshot(self) -> int:
        """Restores S3 catalog snapshot from .vayom/index.json into local SQLite."""
        object_key = f"{self.vault_prefix}/.vayom/index.json"
        url = self._build_url(object_key)
        signed = self._get_signed_headers("GET", url)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=signed)
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
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, 's3', 'completed')
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
                        ) VALUES (?, ?, 0, 0, ?, ?, 0, 's3', ?)
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
                                    (alb_id, alb_name, f"S3 Folder: {alb_name}")
                                )
                            await db.execute(
                                "INSERT OR IGNORE INTO file_albums (file_id, album_id) VALUES (?, ?)",
                                (file_id, alb_id)
                            )
                    restored += 1
            await db.commit()
        return restored
