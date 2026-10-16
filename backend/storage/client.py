import os
import asyncio
import json
import logging
from typing import Optional, Callable, AsyncIterator
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageMediaDocument
from ..config import settings

logger = logging.getLogger("vayom.storage")

class TelegramStorageClient:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        self._lock = asyncio.Lock()
        self._media_cache: dict[tuple[str, int], Any] = {}

    async def connect(self):
        """Initializes and authenticates the Telethon bot client."""
        async with self._lock:
            if self._is_connected and self.client:
                return

            if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH or not settings.TELEGRAM_BOT_TOKEN:
                logger.warning("Telegram credentials not fully configured. Operating in mock/offline mode.")
                return

            session_file = str(settings.DATA_DIR / "vayom_bot")
            self.client = TelegramClient(
                session_file,
                settings.TELEGRAM_API_ID,
                settings.TELEGRAM_API_HASH
            )

            await self.client.start(bot_token=settings.TELEGRAM_BOT_TOKEN)
            self._is_connected = True
            logger.info("Successfully connected to Telegram MTProto as Bot!")

    async def upload_part(
        self,
        file_path: str,
        caption: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> dict:
        """
        Uploads a single file part (up to 2000 MB) to the private channel.
        Automatically catches and respects FloodWait rate limits.
        Returns dict with message_id, channel_id, file_id, access_hash.
        """
        if not self._is_connected or not self.client:
            # Full-fidelity offline mock mode: persists part to mock_blobs
            import shutil
            mock_dir = settings.DATA_DIR / "mock_blobs"
            mock_dir.mkdir(parents=True, exist_ok=True)
            msg_id = 1000 + int(asyncio.get_event_loop().time() * 1000) % 900000
            shutil.copy2(file_path, mock_dir / f"{msg_id}.bin")
            with open(mock_dir / f"{msg_id}.caption", "w", encoding="utf-8") as cf:
                cf.write(caption)
            logger.info(f"Offline mock upload: stored part {msg_id}")
            return {
                "message_id": msg_id,
                "channel_id": str(settings.TELEGRAM_CHANNEL_ID or "mock"),
                "file_id": f"mock-{msg_id}",
                "access_hash": "0"
            }

        channel = int(settings.TELEGRAM_CHANNEL_ID)

        while True:
            try:
                message = await self.client.send_file(
                    channel,
                    file_path,
                    caption=caption,
                    progress_callback=progress_callback,
                    force_document=True  # Preserve exact original bytes and MIME type
                )
                doc = getattr(message.media, "document", None) if message and message.media else None
                self._media_cache[(str(channel), message.id)] = message.media
                return {
                    "message_id": message.id,
                    "channel_id": str(channel),
                    "file_id": str(doc.id) if doc else None,
                    "access_hash": str(doc.access_hash) if doc else None
                }
            except FloodWaitError as e:
                logger.warning(f"Telegram FloodWait encountered: sleeping for {e.seconds}s...")
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                logger.error(f"Error uploading file part to Telegram: {e}")
                raise

    async def stream_part_bytes(
        self,
        message_id: int,
        channel_id: Optional[str] = None,
        file_id: Optional[str] = None,
        access_hash: Optional[str] = None,
        offset: int = 0,
        limit: Optional[int] = None,
        chunk_size: int = 64 * 1024
    ) -> AsyncIterator[bytes]:
        """
        Streams a byte slice of a document from Telegram without loading the whole file into RAM.
        Uses in-memory media reference cache to prevent rate limits during rapid video scrubbing.
        """
        if not self._is_connected or not self.client:
            mock_dir = settings.DATA_DIR / "mock_blobs"
            blob_path = mock_dir / f"{message_id}.bin"
            if blob_path.exists():
                file_size = os.path.getsize(blob_path)
                with open(blob_path, "rb") as f:
                    f.seek(offset)
                    bytes_left = limit if limit is not None else (file_size - offset)
                    while bytes_left > 0:
                        chunk = f.read(min(chunk_size, bytes_left))
                        if not chunk:
                            break
                        yield chunk
                        bytes_left -= len(chunk)
                return
            yield b"\x00" * (limit or 1024)
            return

        target_channel_str = str(channel_id or settings.TELEGRAM_CHANNEL_ID)
        cache_key = (target_channel_str, message_id)

        # 1. Check in-memory media cache (avoids repeated Telegram RPC calls on Range requests)
        media = self._media_cache.get(cache_key)
        if not media:
            channel = int(target_channel_str)
            message = await self.client.get_messages(channel, ids=message_id)
            if not message or not message.media:
                raise FileNotFoundError(f"Message {message_id} not found in channel {channel}")
            media = message.media
            if len(self._media_cache) > 500:
                self._media_cache.pop(next(iter(self._media_cache)))
            self._media_cache[cache_key] = media

        # 2. Telethon iter_download streams chunks directly from Telegram DC sockets
        bytes_sent = 0
        async for chunk in self.client.iter_download(
            media,
            offset=offset,
            request_size=chunk_size
        ):
            if limit is not None and bytes_sent + len(chunk) > limit:
                remaining = limit - bytes_sent
                yield chunk[:remaining]
                break

            yield chunk
            bytes_sent += len(chunk)

    async def scan_channel_envelopes(self, limit: int = 2000) -> list[dict]:
        """
        Scans channel message history backward from HEAD.
        Extracts VAYOM:v1: JSON envelopes from captions for zero-loss recovery.
        Uses ephemeral probe message to identify channel HEAD, then fetches by ID chunks.
        """
        if not self._is_connected or not self.client:
            mock_dir = settings.DATA_DIR / "mock_blobs"
            envelopes = []
            if mock_dir.exists():
                for cap_file in sorted(mock_dir.glob("*.caption"), reverse=True):
                    try:
                        msg_id = int(cap_file.stem)
                        text = cap_file.read_text(encoding="utf-8")
                        if text.startswith("VAYOM:v1:"):
                            data = json.loads(text[len("VAYOM:v1:"):])
                            data["telegram_message_id"] = msg_id
                            envelopes.append(data)
                    except Exception:
                        pass
            return envelopes

        envelopes = []
        channel = int(settings.TELEGRAM_CHANNEL_ID)

        # 1. Ephemeral probe to discover channel HEAD ID
        head_id = 1
        try:
            probe = await self.client.send_message(channel, "VAYOM:DISASTER_RECOVERY_HEAD_PROBE")
            head_id = probe.id
            await self.client.delete_messages(channel, [head_id])
        except Exception as e:
            logger.warning(f"Could not probe channel HEAD, falling back to scanning range: {e}")
            head_id = 500

        # 2. Scan message IDs backwards from HEAD in batches of 100
        start_id = max(1, head_id - limit)
        for chunk_start in range(head_id - 1, start_id - 1, -100):
            chunk_end = max(start_id, chunk_start - 99)
            batch_ids = list(range(chunk_end, chunk_start + 1))
            if not batch_ids:
                continue

            try:
                messages = await self.client.get_messages(channel, ids=batch_ids)
                for message in messages:
                    if not message:
                        continue
                    text = message.text or message.message or ""
                    if text.startswith("VAYOM:v1:"):
                        try:
                            payload = text[len("VAYOM:v1:"):]
                            data = json.loads(payload)
                            data["telegram_message_id"] = message.id
                            envelopes.append(data)
                        except Exception as err:
                            logger.warning(f"Failed to parse caption envelope in message {message.id}: {err}")
            except Exception as e:
                logger.error(f"Error fetching message batch {batch_ids[0]}-{batch_ids[-1]}: {e}")

        return envelopes

# Global Singleton storage client
storage_client = TelegramStorageClient()
