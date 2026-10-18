from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator, List, Union

class BaseStorageAdapter(ABC):
    """
    Abstract base interface for all Vayom storage backends
    (Telegram, NAS, S3/MinIO/R2, Nextcloud WebDAV).
    """
    backend_name: str = "base"

    @abstractmethod
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
        """
        Uploads or commits a file (or part) to the storage backend.
        Returns a dict containing metadata for insertion into file_parts:
        {
            "storage_backend": str,
            "storage_uri": str,
            # optional backend-specific fields (e.g. telegram_message_id)
        }
        """
        pass

    @abstractmethod
    async def stream_bytes(
        self,
        storage_uri: str,
        offset: int = 0,
        limit: Optional[int] = None,
        chunk_size: int = 512 * 1024,
        **kwargs
    ) -> AsyncIterator[bytes]:
        """
        Yields contiguous bytes for range streaming (HTTP 206 Partial Content).
        """
        pass

    @abstractmethod
    async def delete_parts(self, parts: List[Dict[str, Any]]) -> int:
        """
        Deletes stored parts / objects from the storage backend.
        Returns the count of deleted elements.
        """
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """
        Returns connection and health status.
        """
        pass
