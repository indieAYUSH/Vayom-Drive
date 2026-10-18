import logging
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator, List, Union
from urllib.parse import urlparse

from .base import BaseStorageAdapter
from ..client import storage_client, format_part_caption
from ...config import settings

logger = logging.getLogger("vayom.storage.telegram")

class TelegramStorageAdapter(BaseStorageAdapter):
    backend_name: str = "telegram"

    def __init__(self, client=None):
        self.client = client or storage_client

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
        **kwargs
    ) -> Dict[str, Any]:
        caption = format_part_caption(
            file_id=file_id,
            part_index=part_index,
            total_parts=total_parts,
            byte_offset=byte_offset,
            part_size=part_size,
            part_sha256=sha256,
            filename=filename,
            category=category,
            kind="proxy" if is_proxy else "original",
            proxy_of=proxy_of,
            proxy_profile=proxy_profile
        )

        res = await self.client.upload_part(
            str(local_path),
            caption=caption,
            progress_callback=progress_callback
        )

        channel_id = str(settings.TELEGRAM_CHANNEL_ID or "")
        if isinstance(res, dict):
            msg_id = res.get("message_id")
            channel_id = str(res.get("channel_id") or channel_id)
            tg_file_id = res.get("file_id")
            tg_access_hash = res.get("access_hash")
        else:
            msg_id = int(res)
            tg_file_id = None
            tg_access_hash = None

        storage_uri = f"tg://{channel_id}/{msg_id}" if msg_id else ""

        return {
            "storage_backend": self.backend_name,
            "storage_uri": storage_uri,
            "telegram_message_id": msg_id or 0,
            "telegram_channel_id": channel_id,
            "telegram_file_id": tg_file_id,
            "telegram_access_hash": tg_access_hash,
        }

    async def stream_bytes(
        self,
        storage_uri: str,
        offset: int = 0,
        limit: Optional[int] = None,
        chunk_size: int = 512 * 1024,
        **kwargs
    ) -> AsyncIterator[bytes]:
        msg_id = kwargs.get("telegram_message_id") or kwargs.get("message_id")
        channel_id = kwargs.get("telegram_channel_id") or kwargs.get("channel_id")
        file_id = kwargs.get("telegram_file_id") or kwargs.get("file_id")
        access_hash = kwargs.get("telegram_access_hash") or kwargs.get("access_hash")

        if not msg_id and storage_uri.startswith("tg://"):
            try:
                parsed = urlparse(storage_uri)
                channel_id = channel_id or parsed.netloc
                path_parts = parsed.path.strip("/").split("/")
                if path_parts and path_parts[0].isdigit():
                    msg_id = int(path_parts[0])
            except Exception as e:
                logger.warning(f"Could not parse storage_uri '{storage_uri}': {e}")

        if not msg_id:
            logger.error(f"Cannot stream telegram bytes without valid message_id (uri={storage_uri})")
            return

        async for chunk in self.client.stream_part_bytes(
            message_id=int(msg_id),
            channel_id=str(channel_id) if channel_id else None,
            file_id=file_id,
            access_hash=access_hash,
            offset=offset,
            limit=limit,
            chunk_size=chunk_size
        ):
            yield chunk

    async def delete_parts(self, parts: List[Dict[str, Any]]) -> int:
        return await self.client.delete_parts(parts)

    async def health_check(self) -> Dict[str, Any]:
        return await self.client.check_channel_health()
