import logging
from typing import Optional, Dict, Any, AsyncIterator
from .adapters.base import BaseStorageAdapter
from .adapters.telegram import TelegramStorageAdapter

logger = logging.getLogger("vayom.storage.manager")

_adapters: Dict[str, BaseStorageAdapter] = {}

def get_storage_adapter(backend: Optional[str] = None) -> BaseStorageAdapter:
    """
    Returns the storage adapter for the specified backend ('telegram', 'nas', 's3', 'nextcloud').
    Defaults to 'telegram' if None or empty.
    """
    backend_key = (backend or "telegram").strip().lower()

    if backend_key not in _adapters:
        if backend_key == "telegram":
            _adapters[backend_key] = TelegramStorageAdapter()
        elif backend_key == "nas":
            from .adapters.nas import NASStorageAdapter
            _adapters[backend_key] = NASStorageAdapter()
        elif backend_key == "s3":
            from .adapters.s3 import S3StorageAdapter
            _adapters[backend_key] = S3StorageAdapter()
        elif backend_key == "nextcloud":
            from .adapters.nextcloud import NextcloudWebDAVAdapter
            _adapters[backend_key] = NextcloudWebDAVAdapter()
        else:
            # Fallback or placeholder until specific adapter is registered
            logger.warning(f"Storage adapter '{backend_key}' requested before registration; falling back to telegram adapter.")
            if "telegram" not in _adapters:
                _adapters["telegram"] = TelegramStorageAdapter()
            return _adapters["telegram"]

    return _adapters[backend_key]

def register_storage_adapter(backend: str, adapter: BaseStorageAdapter) -> None:
    """
    Registers or overrides a storage adapter for a given backend name.
    """
    _adapters[backend.strip().lower()] = adapter
    logger.info(f"Registered storage adapter: {backend}")

async def stream_part_bytes_universal(
    part: Dict[str, Any],
    offset: int = 0,
    limit: Optional[int] = None,
    chunk_size: int = 512 * 1024
) -> AsyncIterator[bytes]:
    """
    Universal part byte streamer: inspects part metadata (storage_backend, storage_uri)
    and dispatches to the appropriate storage adapter transparently.
    """
    if offset < 0:
        raise ValueError(f"Offset cannot be negative: {offset}")
    if limit is not None and limit <= 0:
        return

    backend = part.get("storage_backend") or "telegram"
    adapter = get_storage_adapter(backend)
    storage_uri = part.get("storage_uri") or ""

    async for chunk in adapter.stream_bytes(
        storage_uri=storage_uri,
        offset=offset,
        limit=limit,
        chunk_size=chunk_size,
        telegram_message_id=part.get("telegram_message_id"),
        telegram_channel_id=part.get("telegram_channel_id"),
        telegram_file_id=part.get("telegram_file_id"),
        telegram_access_hash=part.get("telegram_access_hash"),
        part_record=part
    ):
        yield chunk
