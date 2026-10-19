import os
import time
import asyncio
import json
import logging
import uuid
import shutil
import mimetypes
from pathlib import Path
from typing import Optional, Callable, AsyncIterator, Any, Union
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, FileReferenceExpiredError, FilerefUpgradeNeededError
from telethon.tl.types import MessageMediaDocument
from ..config import settings
from .session import RobustSQLiteSession

logger = logging.getLogger("vayom.storage")

def format_part_caption(
    file_id: str,
    part_index: int,
    total_parts: int,
    byte_offset: int,
    part_size: int,
    part_sha256: str,
    filename: Optional[str] = None,
    category: Optional[str] = "other",
    nonce: Optional[str] = None,
    encryption: str = "none",
    kind: str = "original",
    proxy_of: Optional[str] = None,
    proxy_profile: Optional[str] = None
) -> str:
    """
    Builds a VAYOM:v1: caption envelope adhering to Phase 1 constraints:
    - Part 0 caption <= 950 bytes
    - Later parts (Part 1..N) <= 200 bytes
    - Optional keys: 'k': 'p', 'of': '<original_id>', 'profile': '<profile>'
    - Absent 'k' = original
    """
    payload: Dict[str, Any] = {
        "id": file_id,
        "part": part_index,
        "total": total_parts,
        "offset": byte_offset,
        "size": part_size,
        "sha256": part_sha256
    }

    if kind == "proxy":
        payload["k"] = "p"
        if part_index == 0:
            if proxy_of:
                payload["of"] = proxy_of
            if proxy_profile:
                payload["profile"] = proxy_profile

    if part_index == 0:
        safe_name = filename or "file"
        if len(safe_name.encode("utf-8")) > 200:
            ext = os.path.splitext(safe_name)[1]
            safe_name = safe_name[:180] + ext
        payload["name"] = safe_name
        payload["cat"] = category or "other"
        if nonce:
            payload["nonce"] = nonce
        if encryption and encryption != "none":
            payload["enc"] = encryption

        caption = f"VAYOM:v1:{json.dumps(payload, separators=(',', ':'))}"
        caption_bytes = caption.encode("utf-8")
        assert len(caption_bytes) <= 950, f"Part 0 caption exceeded 950 bytes ({len(caption_bytes)} bytes)"
        return caption
    else:
        caption = f"VAYOM:v1:{json.dumps(payload, separators=(',', ':'))}"
        caption_bytes = caption.encode("utf-8")
        assert len(caption_bytes) <= 200, f"Part {part_index} caption exceeded 200 bytes ({len(caption_bytes)} bytes)"
        return caption

class TelegramStorageClient:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        self._lock = asyncio.Lock()
        self._media_cache: dict[tuple[str, int], Any] = {}
        # Architectural Invariant 4: Read vs. Write FloodWait Isolation
        # Separate write rate limits (sendMedia/editMessage) from read rate limits (getFile)
        # so background upload limits never freeze active video streaming or in-memory playback.
        self._write_flood_wait_until: float = 0.0
        self._read_flood_wait_until: float = 0.0
        # Anti-Ban Invariant 4: Limit upload concurrency to 3-4 parallel workers
        self._upload_semaphore = asyncio.Semaphore(3)
        self._catalog_lock = asyncio.Lock()
        self.last_pinned_catalog_msg_id: Optional[int] = None
        self.last_backup_catalog_msg_id: Optional[int] = None
        self.last_saved_catalog_msg_id: Optional[int] = None
        self._load_catalog_state()

    def _catalog_state_path(self) -> Path:
        return settings.DATA_DIR / "catalog_state.json"

    def _load_catalog_state(self):
        try:
            path = self._catalog_state_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                self.last_pinned_catalog_msg_id = data.get("last_pinned_msg_id")
                self.last_backup_catalog_msg_id = data.get("last_backup_msg_id")
                self.last_saved_catalog_msg_id = data.get("last_saved_msg_id")
        except Exception as e:
            logger.warning(f"Could not load catalog state: {e}")

    def _save_catalog_state(self):
        try:
            settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
            path = self._catalog_state_path()
            temp_path = settings.DATA_DIR / f"catalog_state_{uuid.uuid4().hex}.tmp"
            data = {
                "last_pinned_msg_id": self.last_pinned_catalog_msg_id,
                "last_backup_msg_id": self.last_backup_catalog_msg_id,
                "last_saved_msg_id": self.last_saved_catalog_msg_id
            }
            temp_path.write_text(json.dumps(data), encoding="utf-8")
            os.replace(temp_path, path)
        except Exception as e:
            logger.warning(f"Could not persist catalog state: {e}")
            if 'temp_path' in locals() and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def set_flood_wait(self, seconds: int, op_type: str = "write"):
        """Sets or extends the flood wait period in seconds for 'write' or 'read' operations."""
        # B-1: flood pacing uses time.monotonic() so NTP/manual clock jumps
        # can neither collapse nor extend the enforced cooldown.
        target = time.monotonic() + seconds
        if op_type == "read":
            self._read_flood_wait_until = max(self._read_flood_wait_until, target)
        else:
            self._write_flood_wait_until = max(self._write_flood_wait_until, target)

    def set_write_flood_wait(self, seconds: int):
        self.set_flood_wait(seconds, op_type="write")

    def set_read_flood_wait(self, seconds: int):
        self.set_flood_wait(seconds, op_type="read")

    @property
    def write_flood_wait_seconds(self) -> int:
        remaining = self._write_flood_wait_until - time.monotonic()
        return max(0, int(remaining))

    @property
    def read_flood_wait_seconds(self) -> int:
        remaining = self._read_flood_wait_until - time.monotonic()
        return max(0, int(remaining))

    @property
    def flood_wait_seconds(self) -> int:
        """Returns write flood wait duration (for upload queue / task trackers) or max remaining."""
        return max(self.write_flood_wait_seconds, self.read_flood_wait_seconds)

    @property
    def online_status(self) -> str:
        """Returns current connectivity status: 'flood_wait', 'online', or 'reconnecting'."""
        if self.flood_wait_seconds > 0:
            return "flood_wait"
        return "online" if self._is_connected else "reconnecting"

    async def _wait_if_flooded(self, op_type: str = "write"):
        """
        Pauses execution if a FloodWait cooldown is active for op_type ('write' or 'read').
        Ensures write flood-waits (e.g. from bulk uploads) do not block read/stream calls.
        """
        until = self._read_flood_wait_until if op_type == "read" else self._write_flood_wait_until
        # B-1: must read the same monotonic clock the deadline was set with.
        now = time.monotonic()
        if until > now:
            wait_left = until - now
            logger.warning(f"Telegram {op_type.upper()} FloodWait active: pausing request for {wait_left:.2f}s...")
            await asyncio.sleep(wait_left)

    async def _wait_if_write_flooded(self):
        await self._wait_if_flooded(op_type="write")

    async def _wait_if_read_flooded(self):
        await self._wait_if_flooded(op_type="read")

    async def verify_channel_privacy(self, channel_id: str):
        """
        Non-mutating privacy verification per update.md:
        Verify state locally from cached dialog properties before invoking write calls.
        Only invoke UpdateUsernameRequest if a public username actually exists.
        """
        if not self.client or not self._is_connected:
            return
        try:
            target_entity = await self.client.get_entity(int(channel_id) if str(channel_id).lstrip("-").isdigit() else channel_id)
            has_public_username = bool(
                getattr(target_entity, "username", None) or 
                getattr(target_entity, "usernames", None)
            )
            if has_public_username:
                logger.info(f"Storage channel {channel_id} has public username. Privatizing per Anti-Ban privacy invariant...")
                from telethon.tl.functions.channels import UpdateUsernameRequest
                await self.client(UpdateUsernameRequest(channel=target_entity, username=""))
                logger.info(f"Successfully privatized storage channel {channel_id}")
            else:
                logger.info(f"Storage channel {channel_id} verified private locally (zero write mutation calls).")
        except Exception as e:
            logger.warning(f"Could not verify channel privacy for {channel_id}: {e}")

    async def connect(self):
        """Initializes and authenticates the Telethon client (User MTProto or Bot MTProto)."""
        async with self._lock:
            if self._is_connected and self.client:
                if hasattr(self.client, "is_connected") and not self.client.is_connected():
                    logger.info("Client marked connected but socket disconnected. Reconnecting...")
                    try:
                        await self.client.connect()
                        if self.client.is_connected():
                            return
                    except Exception as e:
                        logger.warning(f"Fast reconnect failed: {e}")
                else:
                    return

            settings.load_auth_config()

            if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
                logger.warning("Telegram API ID/Hash not configured. Operating in mock/offline mode.")
                return

            session_file = str(settings.DATA_DIR / settings.SESSION_NAME)
            robust_session = RobustSQLiteSession(session_file)
            self.client = TelegramClient(
                robust_session,
                settings.TELEGRAM_API_ID,
                settings.TELEGRAM_API_HASH
            )

            if settings.AUTH_MODE == "qr":
                # User MTProto Session
                await self.client.connect()
                if not await self.client.is_user_authorized():
                    logger.warning("User MTProto session not authorized. Waiting for QR login.")
                    self._is_connected = False
                    return
                self._is_connected = True
                logger.info("Successfully connected to Telegram MTProto as User (QR Session)!")
                if settings.TELEGRAM_CHANNEL_ID:
                    await self.verify_channel_privacy(settings.TELEGRAM_CHANNEL_ID)
            elif settings.AUTH_MODE in ("bot", "developer") and settings.TELEGRAM_BOT_TOKEN:
                # Bot MTProto Session
                await self.client.start(bot_token=settings.TELEGRAM_BOT_TOKEN)
                self._is_connected = True
                logger.info("Successfully connected to Telegram MTProto as Bot!")
                if settings.TELEGRAM_CHANNEL_ID:
                    await self.verify_channel_privacy(settings.TELEGRAM_CHANNEL_ID)
            else:
                logger.info("No active auth session. Awaiting user login.")
                self._is_connected = False

    async def ensure_connected(self):
        """Ensures active Telegram MTProto connection; auto-reconnects if dropped."""
        if not self._is_connected or not self.client:
            await self.connect()
            return

        if hasattr(self.client, "is_connected") and not self.client.is_connected():
            logger.info("Telegram connection dropped. Restoring MTProto socket...")
            try:
                await self.client.connect()
                if self.client.is_connected():
                    self._is_connected = True
                    return
            except Exception as e:
                logger.warning(f"Reconnection attempt failed: {e}. Performing full reconnect...")
            await self.reconnect()

    async def disconnect(self):
        """Cleanly disconnects the Telegram MTProto client."""
        async with self._lock:
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                self.client = None
            self._is_connected = False
            self._media_cache.clear()
            self._write_flood_wait_until = 0.0
            self._read_flood_wait_until = 0.0
            self._flood_wait_until = 0.0
            logger.info("Disconnected from Telegram MTProto.")

    async def reconnect(self):
        """Disconnects and reconnects with fresh settings."""
        await self.disconnect()
        await self.connect()

    async def upload_part(
        self,
        file_path: Union[str, Path, Any],
        caption: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> dict:
        """
        Uploads a single file part (up to 2000 MB or 4000 MB) to the private channel.
        Supports both local file paths and zero-copy BoundedFileSlice objects.
        Automatically catches and respects Write FloodWait rate limits.
        Returns dict with message_id, channel_id, file_id, access_hash.
        """
        await self.ensure_connected()
        if not self._is_connected or not self.client:
            # Full-fidelity offline mock mode: persists part to mock_blobs
            mock_dir = settings.DATA_DIR / "mock_blobs"
            mock_dir.mkdir(parents=True, exist_ok=True)
            if not hasattr(self, "_mock_msg_seq"):
                self._mock_msg_seq = 1000 + int(time.time() * 10) % 500000
            self._mock_msg_seq += 1
            msg_id = self._mock_msg_seq

            is_slice = hasattr(file_path, "read")
            if is_slice:
                if hasattr(file_path, "seek"):
                    file_path.seek(0)
                with open(mock_dir / f"{msg_id}.bin", "wb") as out_f:
                    while chunk := file_path.read(1024 * 1024):
                        out_f.write(chunk)
                p_sz = getattr(file_path, "slice_size", getattr(file_path, "size", os.path.getsize(mock_dir / f"{msg_id}.bin")))
            else:
                import shutil
                shutil.copy2(file_path, mock_dir / f"{msg_id}.bin")
                p_sz = os.path.getsize(file_path)

            with open(mock_dir / f"{msg_id}.caption", "w", encoding="utf-8") as cf:
                cf.write(caption)
            logger.info(f"Offline mock upload: stored part {msg_id}")
            if progress_callback:
                try:
                    progress_callback(p_sz, p_sz)
                except Exception:
                    pass
            return {
                "message_id": msg_id,
                "channel_id": str(settings.TELEGRAM_CHANNEL_ID or "mock"),
                "file_id": f"mock-{msg_id}",
                "access_hash": "0"
            }

        channel = int(settings.TELEGRAM_CHANNEL_ID)

        # Anti-Ban Invariant 4: Limit upload concurrency to 3-4 parallel workers
        # B-1: bounded retry loop — at most 5 attempts, then fail with a typed error
        # instead of retrying forever on a persistently failing connection.
        for _attempt in range(5):
            # Anti-Ban Invariant 5: Pause if write FloodWait is active BEFORE attempting semaphore
            await self._wait_if_write_flooded()
            try:
                async with self._upload_semaphore:
                    # Adaptive chunk pacing: insert micro-delay between uploads to smooth burst traffic
                    if settings.CHUNK_PACING_MS > 0:
                        await asyncio.sleep(settings.CHUNK_PACING_MS / 1000.0)

                    # 512 KB chunk part size per update.md anti-ban specification
                    is_slice = hasattr(file_path, "read")
                    if is_slice:
                        if hasattr(file_path, "seek"):
                            file_path.seek(0)
                        file_handle = await self.client.upload_file(
                            file_path,
                            part_size_kb=512,
                            file_size=getattr(file_path, "slice_size", getattr(file_path, "size", None)),
                            file_name=getattr(file_path, "name", "part.bin"),
                            progress_callback=progress_callback
                        )
                    else:
                        file_handle = await self.client.upload_file(
                            file_path,
                            part_size_kb=512,
                            progress_callback=progress_callback
                        )

                    message = await self.client.send_file(
                        channel,
                        file_handle,
                        caption=caption,
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
                # Anti-Ban Invariant 5: Catch FLOOD_WAIT_X globally and pause for X + 2 seconds.
                penalty = e.seconds + 2
                self.set_write_flood_wait(penalty)
                logger.warning(f"Telegram Write FloodWait encountered ({e.seconds}s): write pause enforced for {penalty}s (X + 2 rule)...")
                await asyncio.sleep(penalty)
            except Exception as e:
                err_str = str(e)
                if "FLOOD_WAIT_" in err_str:
                    import re
                    match = re.search(r"FLOOD_WAIT_(\d+)", err_str)
                    wait_sec = int(match.group(1)) if match else 10
                    penalty = wait_sec + 2
                    self.set_write_flood_wait(penalty)
                    logger.warning(f"Telegram Write FloodWait text encountered ({wait_sec}s): write pause for {penalty}s...")
                    await asyncio.sleep(penalty)
                else:
                    logger.error(f"Error uploading file part to Telegram: {e}")
                    raise

        raise RuntimeError(f"Upload exceeded maximum retry limit (5) for part: {caption[:40]}")

    async def stream_part_bytes(
        self,
        message_id: int,
        channel_id: Optional[str] = None,
        file_id: Optional[str] = None,
        access_hash: Optional[str] = None,
        offset: int = 0,
        limit: Optional[int] = None,
        chunk_size: int = 512 * 1024
    ) -> AsyncIterator[bytes]:
        """
        Streams a byte slice of a document from Telegram without loading the whole file into RAM.
        Uses in-memory media reference cache with automatic FileReference refresh and retry resilience.
        """
        await self.ensure_connected()
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
            raise FileNotFoundError(f"Mock blob missing for message {message_id}: {blob_path}")

        target_channel_str = str(channel_id or settings.TELEGRAM_CHANNEL_ID)
        cache_key = (target_channel_str, message_id)
        channel = int(target_channel_str)

        async def get_fresh_media():
            for m_attempt in range(3):
                await self.ensure_connected()
                await self._wait_if_read_flooded()
                try:
                    msg = await self.client.get_messages(channel, ids=message_id)
                    if msg and msg.media:
                        if len(self._media_cache) > 500:
                            self._media_cache.pop(next(iter(self._media_cache)))
                        self._media_cache[cache_key] = msg.media
                        return msg.media
                except FloodWaitError as e:
                    penalty = e.seconds + 2
                    self.set_read_flood_wait(penalty)
                    logger.warning(f"Read FloodWait when getting message {message_id}: sleeping {penalty}s (X + 2 rule)")
                    await asyncio.sleep(penalty)
                    continue
                except Exception as e:
                    err_str = str(e)
                    if "FLOOD_WAIT_" in err_str:
                        import re
                        match = re.search(r"FLOOD_WAIT_(\d+)", err_str)
                        wait_sec = int(match.group(1)) if match else 10
                        penalty = wait_sec + 2
                        self.set_read_flood_wait(penalty)
                        logger.warning(f"Read FloodWait text when getting message {message_id}: sleeping {penalty}s...")
                        await asyncio.sleep(penalty)
                        continue
                    logger.warning(f"Error getting message {message_id}: {e}. Reconnecting...")
                    await self.ensure_connected()
                    await asyncio.sleep(0.5)
            raise FileNotFoundError(f"Message {message_id} not found in channel {channel}")

        media = self._media_cache.get(cache_key)
        if not media:
            media = await get_fresh_media()

        bytes_sent = 0
        curr_offset = offset
        max_retries = 5

        for attempt in range(max_retries):
            try:
                await self._wait_if_read_flooded()
                # Accelerated MTProto Download: For multi-chunk streaming and downloads,
                # use a bounded lookahead prefetch pipeline to download the next chunk from MTProto
                # while previous chunks are actively yielded to the HTTP client/browser.
                if limit is None or limit > chunk_size:
                    queue: asyncio.Queue = asyncio.Queue(maxsize=3)
                    _SENTINEL = object()

                    async def _prefetch_producer(start_off: int, start_sent: int):
                        p_off = start_off
                        p_sent = start_sent
                        try:
                            await self._wait_if_read_flooded()
                            async for p_chunk in self.client.iter_download(
                                media,
                                offset=p_off,
                                request_size=chunk_size,
                                chunk_size=chunk_size
                            ):
                                await self._wait_if_read_flooded()
                                if limit is not None and p_sent + len(p_chunk) > limit:
                                    rem = limit - p_sent
                                    await queue.put(p_chunk[:rem])
                                    break
                                await queue.put(p_chunk)
                                p_sent += len(p_chunk)
                                p_off += len(p_chunk)
                        except asyncio.CancelledError:
                            raise
                        except Exception as ex:
                            await queue.put(ex)
                            return
                        finally:
                            await queue.put(_SENTINEL)

                    producer = asyncio.create_task(_prefetch_producer(curr_offset, bytes_sent))
                    try:
                        while True:
                            item = await queue.get()
                            if item is _SENTINEL:
                                break
                            if isinstance(item, Exception):
                                raise item
                            yield item
                            bytes_sent += len(item)
                            curr_offset += len(item)
                            if limit is not None and bytes_sent >= limit:
                                break
                        return
                    finally:
                        if not producer.done():
                            producer.cancel()
                            try:
                                await producer
                            except (asyncio.CancelledError, Exception):
                                pass
                else:
                    # Single chunk request: direct sequential pull
                    async for chunk in self.client.iter_download(
                        media,
                        offset=curr_offset,
                        request_size=chunk_size,
                        chunk_size=chunk_size
                    ):
                        await self._wait_if_read_flooded()
                        if limit is not None and bytes_sent + len(chunk) > limit:
                            remaining = limit - bytes_sent
                            yield chunk[:remaining]
                            bytes_sent += remaining
                            return
                        yield chunk
                        bytes_sent += len(chunk)
                        curr_offset += len(chunk)
                    return

            except FloodWaitError as e:
                penalty = e.seconds + 2
                self.set_read_flood_wait(penalty)
                logger.warning(f"Read FloodWait during iter_download for msg {message_id} at offset {curr_offset}: sleeping {penalty}s (X + 2 rule)...")
                await asyncio.sleep(penalty)
                continue

            except (FileReferenceExpiredError, FilerefUpgradeNeededError) as e:
                logger.warning(f"FileReference expired for msg {message_id} at {curr_offset}: {e}. Refreshing message...")
                self._media_cache.pop(cache_key, None)
                media = await get_fresh_media()
                continue

            except Exception as e:
                err_str = str(e)
                if "FLOOD_WAIT_" in err_str:
                    import re
                    match = re.search(r"FLOOD_WAIT_(\d+)", err_str)
                    wait_sec = int(match.group(1)) if match else 10
                    penalty = wait_sec + 2
                    self.set_read_flood_wait(penalty)
                    logger.warning(f"Read FloodWait text during iter_download for msg {message_id} at {curr_offset}: sleeping {penalty}s...")
                    await asyncio.sleep(penalty)
                    continue

                if "filereference" in err_str.lower() or "file reference" in err_str.lower():
                    logger.warning(f"File reference error for msg {message_id}: {e}. Refreshing message...")
                    self._media_cache.pop(cache_key, None)
                    media = await get_fresh_media()
                    continue

                logger.warning(
                    f"iter_download error for msg {message_id} at {curr_offset} "
                    f"(attempt {attempt + 1}/{max_retries}): {e}. Reconnecting..."
                )
                await self.ensure_connected()
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(0.5)

    async def get_pinned_catalog(self) -> Optional[dict]:
        """
        Retrieves the pinned master catalog (index.json).
        Provides resilient multi-tier fallback:
        1. Primary storage channel.
        2. Secondary backup mirror channel (if configured).
        3. User 'Saved Messages' ('me') mirror (if in QR user session).
        4. Local snapshot backup (vault_catalog_backup.json).
        """
        # 1. Offline mock mode handling
        if not self._is_connected or not self.client:
            mock_dir = settings.DATA_DIR / "mock_blobs"
            cat_file = mock_dir / "pinned_catalog.json"
            if cat_file.exists():
                try:
                    return json.loads(cat_file.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"Failed to read mock pinned catalog: {e}")
            local_backup = settings.DATA_DIR / "vault_catalog_backup.json"
            if local_backup.exists():
                try:
                    return json.loads(local_backup.read_text(encoding="utf-8"))
                except Exception:
                    pass
            return None

        await self._wait_if_read_flooded()

        async def fetch_catalog_from_target(target_chat) -> Optional[dict]:
            try:
                from telethon.tl.functions.channels import GetFullChannelRequest
                pinned_id = None
                if isinstance(target_chat, int) or (isinstance(target_chat, str) and (target_chat.startswith("-") or target_chat.isdigit())):
                    try:
                        full_channel = await self.client(GetFullChannelRequest(int(target_chat)))
                        pinned_id = getattr(full_channel.full_chat, "pinned_msg_id", None)
                    except Exception:
                        pass

                if pinned_id:
                    message = await self.client.get_messages(target_chat, ids=pinned_id)
                    if isinstance(message, list):
                        message = message[0] if message else None
                    if message and message.media and getattr(message.media, "document", None):
                        raw_bytes = await self.client.download_media(message, bytes)
                        if raw_bytes:
                            if str(target_chat) == str(settings.TELEGRAM_CHANNEL_ID):
                                self.last_pinned_catalog_msg_id = pinned_id
                                self._save_catalog_state()
                            return json.loads(raw_bytes.decode("utf-8"))

                # Fallback: Scan recent 10 messages for VAYOM:CATALOG envelope
                messages = await self.client.get_messages(target_chat, limit=10)
                for msg in messages:
                    text = msg.text or msg.message or ""
                    if text.startswith("VAYOM:CATALOG:"):
                        if msg.media and getattr(msg.media, "document", None):
                            raw_bytes = await self.client.download_media(msg, bytes)
                            if raw_bytes:
                                if str(target_chat) == str(settings.TELEGRAM_CHANNEL_ID):
                                    self.last_pinned_catalog_msg_id = msg.id
                                    self._save_catalog_state()
                                return json.loads(raw_bytes.decode("utf-8"))
                        elif "{" in text and text.endswith("}"):
                            try:
                                json_part = text[text.index("{"):]
                                if str(target_chat) == str(settings.TELEGRAM_CHANNEL_ID):
                                    self.last_pinned_catalog_msg_id = msg.id
                                    self._save_catalog_state()
                                return json.loads(json_part)
                            except Exception:
                                pass
                return None
            except Exception as err:
                logger.warning(f"Could not fetch catalog from target {target_chat}: {err}")
                return None

        # Tier 1: Primary Storage Channel
        if settings.TELEGRAM_CHANNEL_ID:
            cat = await fetch_catalog_from_target(settings.TELEGRAM_CHANNEL_ID)
            if cat:
                return cat
            logger.warning(f"Primary channel {settings.TELEGRAM_CHANNEL_ID} catalog missing or unreachable.")

        # Tier 2: Secondary Backup Channel
        if settings.TELEGRAM_BACKUP_CHANNEL_ID:
            logger.info(f"Attempting disaster recovery from backup channel {settings.TELEGRAM_BACKUP_CHANNEL_ID}...")
            cat = await fetch_catalog_from_target(settings.TELEGRAM_BACKUP_CHANNEL_ID)
            if cat:
                logger.info("Successfully recovered master catalog from secondary backup channel!")
                return cat

        # Tier 3: Saved Messages ('me') Mirror in QR mode
        if settings.AUTH_MODE == "qr" and settings.AUTO_MIRROR_SAVED_MESSAGES:
            logger.info("Attempting disaster recovery from Telegram 'Saved Messages' ('me')...")
            cat = await fetch_catalog_from_target("me")
            if cat:
                logger.info("Successfully recovered master catalog from Saved Messages mirror!")
                return cat

        # Tier 4: Local Snapshot Backup
        local_backup = settings.DATA_DIR / "vault_catalog_backup.json"
        if local_backup.exists():
            try:
                logger.info("Recovering master catalog from local persistent snapshot backup.")
                return json.loads(local_backup.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Could not load local snapshot backup: {e}")

        return None

    async def save_and_pin_catalog(self, catalog_data: dict, old_pinned_id: Optional[int] = None) -> int:
        """
        Saves and pins the compacted master catalog (index.json) to the private channel WAL.
        Serializes catalog writes and mirrors via an asyncio lock.
        Automatically mirrors to:
        1. Local persistent snapshot (vault_catalog_backup.json) for offline recovery.
        2. Secondary backup channel (if configured).
        3. Saved Messages ('me') (if in QR user session).
        Removes previous pinned catalog messages to avoid spam and accumulation.
        """
        async with self._catalog_lock:
            # Calculate max_message_id across all parts for delta tail sync recovery
            max_msg_id = int(catalog_data.get("max_message_id") or 0)
            for f in catalog_data.get("files", []):
                for p in f.get("parts", []):
                    mid = p.get("telegram_message_id")
                    if mid and isinstance(mid, int) and mid > max_msg_id:
                        max_msg_id = mid
                proxy = f.get("proxy")
                if isinstance(proxy, dict):
                    for pp in proxy.get("parts", []):
                        mid = pp.get("telegram_message_id")
                        if mid and isinstance(mid, int) and mid > max_msg_id:
                            max_msg_id = mid
            catalog_data["max_message_id"] = max_msg_id

            # Always maintain local snapshot for zero-cloud disaster recovery (atomic write)
            backup_tmp = None
            try:
                settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
                backup_snapshot = settings.DATA_DIR / "vault_catalog_backup.json"
                backup_tmp = settings.DATA_DIR / f"vault_catalog_backup_{uuid.uuid4().hex}.tmp"
                backup_tmp.write_text(json.dumps(catalog_data, indent=2), encoding="utf-8")
                os.replace(backup_tmp, backup_snapshot)
            except Exception as e:
                logger.warning(f"Could not write local catalog snapshot: {e}")
                if backup_tmp and backup_tmp.exists():
                    try:
                        backup_tmp.unlink()
                    except Exception:
                        pass

            # 1. Offline mock mode handling
            if not self._is_connected or not self.client:
                mock_dir = settings.DATA_DIR / "mock_blobs"
                mock_dir.mkdir(parents=True, exist_ok=True)
                cat_file = mock_dir / "pinned_catalog.json"
                cat_tmp = mock_dir / f"pinned_catalog_{uuid.uuid4().hex}.tmp"
                try:
                    cat_tmp.write_text(json.dumps(catalog_data, indent=2), encoding="utf-8")
                    os.replace(cat_tmp, cat_file)
                finally:
                    if cat_tmp.exists():
                        try:
                            cat_tmp.unlink()
                        except Exception:
                            pass
                logger.info("Saved mock pinned catalog index.json")
                self.last_pinned_catalog_msg_id = 999999
                self._save_catalog_state()
                return 999999

            await self._wait_if_write_flooded()
            channel = int(settings.TELEGRAM_CHANNEL_ID)

            # Determine target old pinned message to delete
            target_old_pinned_id = old_pinned_id if old_pinned_id is not None else self.last_pinned_catalog_msg_id
            if target_old_pinned_id is None:
                try:
                    from telethon.tl.functions.channels import GetFullChannelRequest
                    full_channel = await self.client(GetFullChannelRequest(channel))
                    target_old_pinned_id = getattr(full_channel.full_chat, "pinned_msg_id", None)
                except Exception as e:
                    logger.debug(f"Could not query existing pinned message ID from Telegram channel: {e}")

            if target_old_pinned_id is not None:
                if type(target_old_pinned_id) is not int:
                    if isinstance(target_old_pinned_id, str) and (target_old_pinned_id.isdigit() or (target_old_pinned_id.startswith("-") and target_old_pinned_id[1:].isdigit())):
                        target_old_pinned_id = int(target_old_pinned_id)
                    else:
                        target_old_pinned_id = None

            # Use isolated unique directory for catalog file upload to prevent temp collisions
            unique_token = uuid.uuid4().hex
            temp_dir = settings.DATA_DIR / f"catalog_tmp_{unique_token}"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_catalog = temp_dir / "index.json"
            temp_write = temp_dir / f"index_{unique_token}.tmp"

            try:
                # Serialize compacted JSON atomically
                temp_write.write_text(json.dumps(catalog_data, separators=(",", ":")), encoding="utf-8")
                os.replace(temp_write, temp_catalog)

                summary = {
                    "version": "1.0",
                    "updated_at": catalog_data.get("updated_at", ""),
                    "total_files": catalog_data.get("total_files", len(catalog_data.get("files", []))),
                    "total_bytes": catalog_data.get("total_bytes", 0),
                    "max_message_id": max_msg_id
                }
                caption = f"VAYOM:CATALOG:v1:{json.dumps(summary)}"

                # 1. Upload & Pin to Primary Channel
                msg = await self.client.send_file(
                    channel,
                    str(temp_catalog),
                    caption=caption,
                    force_document=True
                )
                try:
                    await self.client.pin_message(channel, msg.id, notify=False)
                    logger.info(f"Pinned new master catalog message {msg.id} in primary channel.")
                except Exception as pin_err:
                    logger.warning(f"Could not pin catalog in primary channel: {pin_err}")

                # Clean up old pinned catalog message from primary channel
                if target_old_pinned_id and target_old_pinned_id != msg.id:
                    try:
                        await self.client.delete_messages(channel, [target_old_pinned_id])
                        logger.info(f"Deleted old pinned catalog message {target_old_pinned_id} from primary channel.")
                    except Exception as err:
                        logger.warning(f"Could not delete old pinned catalog message {target_old_pinned_id}: {err}")

                self.last_pinned_catalog_msg_id = msg.id

                # 2. Mirror to Secondary Backup Channel if configured
                if settings.TELEGRAM_BACKUP_CHANNEL_ID:
                    try:
                        b_chan = int(settings.TELEGRAM_BACKUP_CHANNEL_ID)
                        target_old_b_id = self.last_backup_catalog_msg_id
                        b_msg = await self.client.send_file(
                            b_chan,
                            str(temp_catalog),
                            caption=f"VAYOM:CATALOG:MIRROR:v1:{json.dumps(summary)}",
                            force_document=True
                        )
                        try:
                            await self.client.pin_message(b_chan, b_msg.id, notify=False)
                            logger.info(f"Mirrored master catalog to backup channel {b_chan}")
                        except Exception as b_pin_err:
                            logger.warning(f"Could not pin catalog in backup channel: {b_pin_err}")

                        if target_old_b_id and target_old_b_id != b_msg.id:
                            try:
                                await self.client.delete_messages(b_chan, [target_old_b_id])
                                logger.info(f"Deleted old backup catalog message {target_old_b_id}")
                            except Exception as b_del_err:
                                logger.warning(f"Could not delete old backup catalog message {target_old_b_id}: {b_del_err}")
                        self.last_backup_catalog_msg_id = b_msg.id
                    except Exception as b_err:
                        logger.warning(f"Failed to mirror catalog to backup channel: {b_err}")

                # 3. Mirror to Saved Messages ('me') in QR user mode
                if settings.AUTH_MODE == "qr" and settings.AUTO_MIRROR_SAVED_MESSAGES:
                    try:
                        target_old_s_id = self.last_saved_catalog_msg_id
                        s_msg = await self.client.send_file(
                            "me",
                            str(temp_catalog),
                            caption=f"VAYOM:CATALOG:MIRROR:v1:{json.dumps(summary)}",
                            force_document=True
                        )
                        logger.info("Mirrored master catalog to Telegram Saved Messages ('me').")
                        if target_old_s_id and target_old_s_id != s_msg.id:
                            try:
                                await self.client.delete_messages("me", [target_old_s_id])
                                logger.info(f"Deleted old saved messages catalog {target_old_s_id}")
                            except Exception as s_del_err:
                                logger.warning(f"Could not delete old saved messages catalog {target_old_s_id}: {s_del_err}")
                        self.last_saved_catalog_msg_id = s_msg.id
                    except Exception as s_err:
                        logger.warning(f"Could not mirror catalog to Saved Messages: {s_err}")

                self._save_catalog_state()
                return msg.id
            except FloodWaitError as e:
                penalty = e.seconds + 2
                self.set_write_flood_wait(penalty)
                logger.warning(f"FloodWait when saving pinned catalog: sleeping {penalty}s (X + 2 rule)")
                raise
            except Exception as e:
                logger.error(f"Error pinning master catalog: {e}")
                raise
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def check_channel_health(self) -> dict:
        """
        Verifies access, permissions, and redundancy health of primary & backup channels.
        """
        health = {
            "online": self._is_connected,
            "flood_wait_seconds": self.flood_wait_seconds,
            "max_concurrent_workers": settings.MAX_CONCURRENT_UPLOADS,
            "primary_channel": {
                "id": settings.TELEGRAM_CHANNEL_ID or "NOT_CONFIGURED",
                "healthy": False,
                "title": None,
                "can_post": False,
                "error": None
            },
            "backup_channel": {
                "id": settings.TELEGRAM_BACKUP_CHANNEL_ID or None,
                "configured": bool(settings.TELEGRAM_BACKUP_CHANNEL_ID),
                "healthy": False,
                "error": None
            },
            "saved_messages_mirror": settings.AUTH_MODE == "qr",
            "local_backup": {
                "exists": (settings.DATA_DIR / "vault_catalog_backup.json").exists(),
                "path": str(settings.DATA_DIR / "vault_catalog_backup.json")
            }
        }

        if not self._is_connected or not self.client:
            health["primary_channel"]["error"] = "Telegram client offline"
            return health

        # Check Primary Channel
        if settings.TELEGRAM_CHANNEL_ID:
            try:
                from telethon.tl.functions.channels import GetFullChannelRequest
                c_id = int(settings.TELEGRAM_CHANNEL_ID)
                full = await self.client(GetFullChannelRequest(c_id))
                chat = getattr(full, "chats", [None])[0]
                health["primary_channel"]["healthy"] = True
                health["primary_channel"]["title"] = getattr(chat, "title", "Primary Vault Channel")
                health["primary_channel"]["can_post"] = getattr(chat, "admin_rights", None) is not None or getattr(chat, "creator", False)
            except Exception as e:
                health["primary_channel"]["healthy"] = False
                health["primary_channel"]["error"] = str(e)

        # Check Backup Channel
        if settings.TELEGRAM_BACKUP_CHANNEL_ID:
            try:
                from telethon.tl.functions.channels import GetFullChannelRequest
                b_id = int(settings.TELEGRAM_BACKUP_CHANNEL_ID)
                await self.client(GetFullChannelRequest(b_id))
                health["backup_channel"]["healthy"] = True
            except Exception as e:
                health["backup_channel"]["healthy"] = False
                health["backup_channel"]["error"] = str(e)

        return health

    async def scan_channel_envelopes(self, limit: int = 2000, min_id: Optional[int] = None) -> list[dict]:
        """
        Scans channel message history backward from HEAD.
        Extracts VAYOM:v1: JSON envelopes from captions for zero-loss recovery.
        When min_id is provided, only retrieves newer envelopes with message_id > min_id (Delta Tail Sync).
        Recognizes alphanumeric continuous IDs (e.g. v_...) and legacy UUIDs.
        """
        if not self._is_connected or not self.client:
            mock_dir = settings.DATA_DIR / "mock_blobs"
            envelopes = []
            if mock_dir.exists():
                for cap_file in sorted(mock_dir.glob("*.caption"), reverse=True):
                    try:
                        msg_id = int(cap_file.stem)
                        if min_id is not None and msg_id <= min_id:
                            continue
                        text = cap_file.read_text(encoding="utf-8")
                        if text.startswith("VAYOM:v1:"):
                            data = json.loads(text[len("VAYOM:v1:"):])
                            data["telegram_message_id"] = msg_id
                            envelopes.append(data)
                    except Exception:
                        pass
                for bin_file in sorted(mock_dir.glob("*.bin"), reverse=True):
                    try:
                        msg_id = int(bin_file.stem)
                        if min_id is not None and msg_id <= min_id:
                            continue
                        if not (mock_dir / f"{msg_id}.caption").exists():
                            envelopes.append({
                                "id": f"v_fwd_{msg_id}",
                                "name": f"mock_file_{msg_id}.bin",
                                "part": 0,
                                "total": 1,
                                "offset": 0,
                                "size": bin_file.stat().st_size,
                                "sha256": "",
                                "cat": "other",
                                "mime": "application/octet-stream",
                                "nonce": None,
                                "enc": "none",
                                "telegram_message_id": msg_id
                            })
                    except Exception:
                        pass
            return envelopes

        envelopes = []
        channel = int(settings.TELEGRAM_CHANNEL_ID)

        # 1. Ephemeral probe to discover channel HEAD ID
        await self._wait_if_write_flooded()
        head_id: Optional[int] = None
        try:
            probe = await self.client.send_message(channel, "VAYOM:DISASTER_RECOVERY_HEAD_PROBE")
            head_id = probe.id
            await self.client.delete_messages(channel, [head_id])
        except FloodWaitError as e:
            penalty = e.seconds + 2
            self.set_write_flood_wait(penalty)
            logger.warning(f"FloodWait during channel head probe: sleeping {penalty}s (X + 2 rule)")
            head_id = None
        except Exception as e:
            logger.warning(f"Could not probe channel HEAD, falling back to newest-message iteration: {e}")
            head_id = None

        if head_id is None:
            # E-2: probe failed — iterate the NEWEST messages directly instead of the old
            # head_id=500 fallback, which scanned the oldest IDs of the channel.
            try:
                async for message in self.client.iter_messages(channel, limit=limit, min_id=min_id or 0):
                    env = self._envelope_from_message(message)
                    if env:
                        envelopes.append(env)
            except Exception as e:
                logger.error(f"Newest-message recovery scan failed: {e}")
            return envelopes

        if min_id is not None and min_id >= head_id:
            return []

        # 2. Scan message IDs backwards from HEAD in batches of 100
        start_id = max(min_id + 1 if min_id is not None else 1, head_id - limit)
        for chunk_start in range(head_id - 1, start_id - 1, -100):
            if min_id is not None and chunk_start <= min_id:
                break
            chunk_end = max(start_id, chunk_start - 99)
            batch_ids = [mid for mid in range(chunk_end, chunk_start + 1) if (min_id is None or mid > min_id)]
            if not batch_ids:
                continue

            # E-2: retry the SAME batch up to 3 times on FloodWait instead of silently skipping it
            messages = None
            for fetch_attempt in range(3):
                await self._wait_if_read_flooded()
                try:
                    messages = await self.client.get_messages(channel, ids=batch_ids)
                    break
                except FloodWaitError as e:
                    penalty = e.seconds + 2
                    self.set_read_flood_wait(penalty)
                    logger.warning(f"FloodWait during channel message scan ({fetch_attempt + 1}/3): sleeping {penalty}s (X + 2 rule)")
                    await asyncio.sleep(penalty)
                except Exception as e:
                    logger.error(f"Error fetching message batch {batch_ids[0]}-{batch_ids[-1]}: {e}")
                    messages = None
                    break

            if messages is None:
                logger.error(f"Batch {batch_ids[0]}-{batch_ids[-1]} skipped after 3 flood-wait attempts; it will be retried on the next recovery scan.")
                continue

            for message in messages:
                if min_id is not None and getattr(message, "id", 0) <= min_id:
                    continue
                env = self._envelope_from_message(message)
                if env:
                    envelopes.append(env)

        return envelopes

    def _envelope_from_message(self, message) -> Optional[dict]:
        """Parses a Telegram message into a VAYOM envelope dict, or None if it is not recoverable."""
        if not message:
            return None
        text = message.text or message.message or ""
        # Ignore catalog envelopes and system probe messages
        if text.startswith("VAYOM:CATALOG:v1:") or text.startswith("VAYOM:DISASTER_RECOVERY"):
            return None
        if text.startswith("VAYOM:v1:"):
            try:
                payload = text[len("VAYOM:v1:"):]
                data = json.loads(payload)
                data["telegram_message_id"] = message.id
                return data
            except Exception as err:
                logger.warning(f"Failed to parse caption envelope in message {message.id}: {err}")
                return None
        elif getattr(message, "media", None):
            # Direct "Forward-to-Vault" Ingestion: auto-index raw media forwarded from mobile
            try:
                f_obj = getattr(message, "file", None)
                f_ext = getattr(f_obj, "ext", "") or ""
                fname = getattr(f_obj, "name", None) or f"telegram_{message.id}{f_ext}"
                fsize = getattr(f_obj, "size", 0) or 0
                fmime = getattr(f_obj, "mime_type", None) or mimetypes.guess_type(fname)[0] or "application/octet-stream"

                cat = "other"
                if fmime.startswith("image/"):
                    cat = "image"
                elif fmime.startswith("video/"):
                    cat = "video"
                elif fmime.startswith("audio/"):
                    cat = "audio"
                elif fmime.startswith("application/pdf") or "document" in fmime or "text/" in fmime:
                    cat = "document"
                elif any(fname.lower().endswith(ext) for ext in [".zip", ".tar", ".gz", ".7z", ".rar"]):
                    cat = "archive"

                return {
                    "id": f"v_fwd_{message.id}",
                    "name": fname,
                    "part": 0,
                    "total": 1,
                    "offset": 0,
                    "size": fsize,
                    "sha256": "",
                    "cat": cat,
                    "mime": fmime,
                    "nonce": None,
                    "enc": "none",
                    "telegram_message_id": message.id
                }
            except Exception as m_err:
                logger.debug(f"Could not parse raw media message {message.id}: {m_err}")
                return None
        return None

    async def delete_messages(
        self,
        message_ids: list[int],
        channel_id: Optional[Union[str, int]] = None
    ) -> int:
        """
        Permanently deletes message(s) from Telegram channel.
        Also clears them from in-memory media reference cache.
        Returns the number of messages deleted.
        """
        if not message_ids:
            return 0

        target_channel_str = str(channel_id or settings.TELEGRAM_CHANNEL_ID or "")

        # 1. Clear from in-memory media reference cache
        for mid in message_ids:
            self._media_cache.pop((target_channel_str, mid), None)
            if settings.TELEGRAM_CHANNEL_ID:
                self._media_cache.pop((str(settings.TELEGRAM_CHANNEL_ID), mid), None)

        # 2. Offline mock mode handling
        if not self._is_connected or not self.client:
            mock_dir = settings.DATA_DIR / "mock_blobs"
            deleted_count = 0
            for mid in message_ids:
                bin_file = mock_dir / f"{mid}.bin"
                cap_file = mock_dir / f"{mid}.caption"
                if bin_file.exists():
                    try:
                        bin_file.unlink()
                        deleted_count += 1
                    except Exception:
                        pass
                if cap_file.exists():
                    try:
                        cap_file.unlink()
                    except Exception:
                        pass
            logger.info(f"Offline mock mode: purged {deleted_count} message blobs")
            return deleted_count

        # 3. Live Telegram MTProto deletion
        try:
            channel = int(target_channel_str)
        except Exception:
            channel = target_channel_str

        deleted_count = 0
        # Telegram API allows deleting up to 100 messages per request
        for i in range(0, len(message_ids), 100):
            batch = message_ids[i:i + 100]
            try:
                await self._wait_if_write_flooded()
                await self.client.delete_messages(channel, batch)
                deleted_count += len(batch)
            except FloodWaitError as e:
                penalty = e.seconds + 2
                self.set_write_flood_wait(penalty)
                logger.warning(f"Telegram Write FloodWait during delete: sleeping {penalty}s (X + 2 rule)")
                await asyncio.sleep(penalty)
                try:
                    await self.client.delete_messages(channel, batch)
                    deleted_count += len(batch)
                except Exception as err:
                    logger.error(f"Failed to delete message batch {batch} after floodwait: {err}")
            except Exception as e:
                logger.error(f"Error deleting message batch {batch} from channel {target_channel_str}: {e}")

        logger.info(f"Permanently deleted {deleted_count} messages from channel {target_channel_str}")
        return deleted_count

    async def delete_parts(self, parts: list[dict]) -> int:
        """
        Groups parts by channel ID and deletes them from Telegram MTProto.
        Each part dict should contain 'telegram_message_id' and optional 'telegram_channel_id'.
        """
        if not parts:
            return 0

        channel_groups: dict[str, list[int]] = {}
        for p in parts:
            chan_id = str(p.get("telegram_channel_id") or settings.TELEGRAM_CHANNEL_ID or "")
            msg_id = p.get("telegram_message_id")
            if msg_id:
                try:
                    channel_groups.setdefault(chan_id, []).append(int(msg_id))
                except (ValueError, TypeError):
                    continue

        total_deleted = 0
        for chan_id, msg_ids in channel_groups.items():
            total_deleted += await self.delete_messages(msg_ids, channel_id=chan_id)
        return total_deleted

# Global Singleton storage client
storage_client = TelegramStorageClient()
