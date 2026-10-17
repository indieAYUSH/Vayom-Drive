import os
import time
import asyncio
import json
import logging
from typing import Optional, Callable, AsyncIterator, Any, Union
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageMediaDocument
from ..config import settings
from .session import RobustSQLiteSession

logger = logging.getLogger("vayom.storage")

class TelegramStorageClient:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        self._lock = asyncio.Lock()
        self._media_cache: dict[tuple[str, int], Any] = {}
        self._flood_wait_until: float = 0.0
        # Anti-Ban Invariant 4: Limit upload concurrency to 3-4 parallel workers
        self._upload_semaphore = asyncio.Semaphore(3)

    def set_flood_wait(self, seconds: int):
        """Sets or extends the flood wait period in seconds."""
        self._flood_wait_until = max(self._flood_wait_until, time.time() + seconds)

    @property
    def flood_wait_seconds(self) -> int:
        """Returns the remaining flood wait duration in seconds, or 0 if not waiting."""
        remaining = self._flood_wait_until - time.time()
        return max(0, int(remaining))

    @property
    def online_status(self) -> str:
        """Returns current connectivity status: 'flood_wait', 'online', or 'reconnecting'."""
        if self.flood_wait_seconds > 0:
            return "flood_wait"
        return "online" if self._is_connected else "reconnecting"

    async def _wait_if_flooded(self):
        """
        Anti-Ban Invariant 5: Strict Global FloodWait Handling.
        If any request triggered a FloodWait, pause execution globally for X + 2 seconds
        so Telegram is not repeatedly spammed under an active rate limit.
        """
        now = time.time()
        if self._flood_wait_until > now:
            wait_left = self._flood_wait_until - now
            logger.warning(f"Global FloodWait active: pausing request for {wait_left:.2f}s...")
            await asyncio.sleep(wait_left)

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
            self._flood_wait_until = 0.0
            logger.info("Disconnected from Telegram MTProto.")

    async def reconnect(self):
        """Disconnects and reconnects with fresh settings."""
        await self.disconnect()
        await self.connect()

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
            if progress_callback:
                try:
                    p_sz = os.path.getsize(file_path)
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
        while True:
            # Anti-Ban Invariant 5: Pause if global FloodWait is active BEFORE attempting semaphore
            await self._wait_if_flooded()
            try:
                async with self._upload_semaphore:
                    # Adaptive chunk pacing: insert micro-delay between uploads to smooth burst traffic
                    if settings.CHUNK_PACING_MS > 0:
                        await asyncio.sleep(settings.CHUNK_PACING_MS / 1000.0)

                    # 512 KB chunk part size per update.md anti-ban specification
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
                # Notice semaphore was released upon exiting `async with`, preventing worker deadlock!
                penalty = e.seconds + 2
                self.set_flood_wait(penalty)
                logger.warning(f"Telegram FloodWait encountered ({e.seconds}s): global pause enforced for {penalty}s (X + 2 rule)...")
                await asyncio.sleep(penalty)
            except Exception as e:
                err_str = str(e)
                if "FLOOD_WAIT_" in err_str:
                    import re
                    match = re.search(r"FLOOD_WAIT_(\d+)", err_str)
                    wait_sec = int(match.group(1)) if match else 10
                    penalty = wait_sec + 2
                    self.set_flood_wait(penalty)
                    logger.warning(f"Telegram FloodWait text encountered ({wait_sec}s): global pause for {penalty}s...")
                    await asyncio.sleep(penalty)
                else:
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
            await self._wait_if_flooded()
            channel = int(target_channel_str)
            try:
                message = await self.client.get_messages(channel, ids=message_id)
            except FloodWaitError as e:
                penalty = e.seconds + 2
                self.set_flood_wait(penalty)
                logger.warning(f"FloodWait when getting message {message_id}: sleeping {penalty}s (X + 2 rule)")
                await asyncio.sleep(penalty)
                message = await self.client.get_messages(channel, ids=message_id)
            if not message or not message.media:
                raise FileNotFoundError(f"Message {message_id} not found in channel {channel}")
            media = message.media
            if len(self._media_cache) > 500:
                self._media_cache.pop(next(iter(self._media_cache)))
            self._media_cache[cache_key] = media

        await self._wait_if_flooded()
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

        await self._wait_if_flooded()

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
                            return json.loads(raw_bytes.decode("utf-8"))

                # Fallback: Scan recent 10 messages for VAYOM:CATALOG envelope
                messages = await self.client.get_messages(target_chat, limit=10)
                for msg in messages:
                    text = msg.text or msg.message or ""
                    if text.startswith("VAYOM:CATALOG:"):
                        if msg.media and getattr(msg.media, "document", None):
                            raw_bytes = await self.client.download_media(msg, bytes)
                            if raw_bytes:
                                return json.loads(raw_bytes.decode("utf-8"))
                        elif "{" in text and text.endswith("}"):
                            try:
                                json_part = text[text.index("{"):]
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
        Automatically mirrors to:
        1. Local persistent snapshot (vault_catalog_backup.json) for offline recovery.
        2. Secondary backup channel (if configured).
        3. Saved Messages ('me') (if in QR user session).
        """
        # Always maintain local snapshot for zero-cloud disaster recovery
        try:
            backup_snapshot = settings.DATA_DIR / "vault_catalog_backup.json"
            backup_snapshot.write_text(json.dumps(catalog_data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not write local catalog snapshot: {e}")

        # 1. Offline mock mode handling
        if not self._is_connected or not self.client:
            mock_dir = settings.DATA_DIR / "mock_blobs"
            mock_dir.mkdir(parents=True, exist_ok=True)
            cat_file = mock_dir / "pinned_catalog.json"
            cat_file.write_text(json.dumps(catalog_data, indent=2), encoding="utf-8")
            logger.info("Saved mock pinned catalog index.json")
            return 999999

        await self._wait_if_flooded()
        channel = int(settings.TELEGRAM_CHANNEL_ID)
        temp_catalog = settings.DATA_DIR / "index.json"
        try:
            # Serialize compacted JSON
            temp_catalog.write_text(json.dumps(catalog_data, separators=(",", ":")), encoding="utf-8")
            summary = {
                "version": "1.0",
                "updated_at": catalog_data.get("updated_at", ""),
                "total_files": catalog_data.get("total_files", len(catalog_data.get("files", []))),
                "total_bytes": catalog_data.get("total_bytes", 0)
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

            if old_pinned_id and old_pinned_id != msg.id:
                try:
                    await self.client.delete_messages(channel, [old_pinned_id])
                except Exception as err:
                    logger.warning(f"Could not delete old pinned catalog message {old_pinned_id}: {err}")

            # 2. Mirror to Secondary Backup Channel if configured
            if settings.TELEGRAM_BACKUP_CHANNEL_ID:
                try:
                    b_chan = int(settings.TELEGRAM_BACKUP_CHANNEL_ID)
                    b_msg = await self.client.send_file(
                        b_chan,
                        str(temp_catalog),
                        caption=f"VAYOM:CATALOG:MIRROR:v1:{json.dumps(summary)}",
                        force_document=True
                    )
                    await self.client.pin_message(b_chan, b_msg.id, notify=False)
                    logger.info(f"Mirrored master catalog to backup channel {b_chan}")
                except Exception as b_err:
                    logger.warning(f"Failed to mirror catalog to backup channel: {b_err}")

            # 3. Mirror to Saved Messages ('me') in QR user mode
            if settings.AUTH_MODE == "qr" and settings.AUTO_MIRROR_SAVED_MESSAGES:
                try:
                    await self.client.send_file(
                        "me",
                        str(temp_catalog),
                        caption=f"VAYOM:CATALOG:MIRROR:v1:{json.dumps(summary)}",
                        force_document=True
                    )
                    logger.info("Mirrored master catalog to Telegram Saved Messages ('me').")
                except Exception as s_err:
                    logger.warning(f"Could not mirror catalog to Saved Messages: {s_err}")

            return msg.id
        except FloodWaitError as e:
            penalty = e.seconds + 2
            self.set_flood_wait(penalty)
            logger.warning(f"FloodWait when saving pinned catalog: sleeping {penalty}s (X + 2 rule)")
            raise
        except Exception as e:
            logger.error(f"Error pinning master catalog: {e}")
            raise
        finally:
            if temp_catalog.exists():
                try:
                    temp_catalog.unlink()
                except Exception:
                    pass

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

    async def scan_channel_envelopes(self, limit: int = 2000) -> list[dict]:
        """
        Scans channel message history backward from HEAD.
        Extracts VAYOM:v1: JSON envelopes from captions for zero-loss recovery.
        Recognizes alphanumeric continuous IDs (e.g. v_...) and legacy UUIDs.
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
        await self._wait_if_flooded()
        head_id = 1
        try:
            probe = await self.client.send_message(channel, "VAYOM:DISASTER_RECOVERY_HEAD_PROBE")
            head_id = probe.id
            await self.client.delete_messages(channel, [head_id])
        except FloodWaitError as e:
            penalty = e.seconds + 2
            self.set_flood_wait(penalty)
            logger.warning(f"FloodWait during channel head probe: sleeping {penalty}s (X + 2 rule)")
            head_id = 500
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

            await self._wait_if_flooded()
            try:
                messages = await self.client.get_messages(channel, ids=batch_ids)
                for message in messages:
                    if not message:
                        continue
                    text = message.text or message.message or ""
                    # Ignore catalog envelopes and system probe messages
                    if text.startswith("VAYOM:CATALOG:v1:") or text.startswith("VAYOM:DISASTER_RECOVERY"):
                        continue
                    if text.startswith("VAYOM:v1:"):
                        try:
                            payload = text[len("VAYOM:v1:"):]
                            data = json.loads(payload)
                            data["telegram_message_id"] = message.id
                            envelopes.append(data)
                        except Exception as err:
                            logger.warning(f"Failed to parse caption envelope in message {message.id}: {err}")
            except FloodWaitError as e:
                penalty = e.seconds + 2
                self.set_flood_wait(penalty)
                logger.warning(f"FloodWait during channel message scan: sleeping {penalty}s (X + 2 rule)")
                await asyncio.sleep(penalty)
            except Exception as e:
                logger.error(f"Error fetching message batch {batch_ids[0]}-{batch_ids[-1]}: {e}")

        return envelopes

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
                await self._wait_if_flooded()
                await self.client.delete_messages(channel, batch)
                deleted_count += len(batch)
            except FloodWaitError as e:
                penalty = e.seconds + 2
                self.set_flood_wait(penalty)
                logger.warning(f"Telegram FloodWait during delete: sleeping {penalty}s (X + 2 rule)")
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
