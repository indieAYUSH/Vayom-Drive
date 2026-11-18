import asyncio
import datetime
import json
import logging
import os
import random
import re
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
    FloodWaitError,
)
from telethon.tl.functions.channels import CreateChannelRequest

from ..config import settings
from ..storage.client import storage_client
from ..storage.session import RobustSQLiteSession
from ..auth.tg_apps import (
    TelegramAppsClient,
    parse_apps_page,
    AppsError,
    AppsRateLimitError,
    AppsInvalidCodeError,
    Apps2FARequiredError,
    AppsParserError,
    AppsPortalError,
)

logger = logging.getLogger("vayom.auth")
router = APIRouter(prefix="/api/v1/auth")

# In-memory tracking for active QR login state
_active_qr_sessions: Dict[str, Dict[str, Any]] = {}

QR_HANDSHAKE_TIMEOUT_SECONDS = 45.0


def _extract_credentials_from_apps_html(html: str) -> Tuple[Optional[int], Optional[str]]:
    """Compatibility alias for tests."""
    api_id, api_hash, _ = parse_apps_page(html)
    return api_id, api_hash


def _require_telegram_app_credentials(api_id: Optional[int] = None, api_hash: Optional[str] = None) -> Tuple[int, str]:
    """Returns effective Telegram API credentials or raises HTTP 503 if unconfigured."""
    target_id = api_id or settings.TELEGRAM_API_ID
    target_hash = (api_hash or "").strip() or (settings.TELEGRAM_API_HASH or "").strip()
    if not target_id or not target_hash:
        raise HTTPException(
            status_code=503,
            detail=(
                "Telegram API credentials are not configured. Set TELEGRAM_API_ID and "
                "TELEGRAM_API_HASH in .env (or telegram_app.env in the app config "
                "directory) and restart Vayom Drive."
            ),
        )
    return int(target_id), str(target_hash)


def _delete_session_files(session_name: str) -> List[str]:
    """Removes a Telethon SQLite session and its WAL/journal sidecars."""
    removed = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = settings.DATA_DIR / f"{session_name}.session{suffix}"
        try:
            if path.exists():
                path.unlink()
                removed.append(path.name)
        except OSError as exc:
            logger.warning(f"Could not remove session file {path}: {exc}")
    return removed


def _secure_file_permissions(path: Path):
    """Enforces 0o600 permissions on sensitive credential/session files."""
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Request & Response Schemas
# -----------------------------------------------------------------------------

class KeysSaveRequest(BaseModel):
    api_id: int
    api_hash: str


class QRStartRequest(BaseModel):
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    mode: Optional[str] = None  # 'own' or 'shared'


class QRStartResponse(BaseModel):
    session_id: str
    url: str
    expires_at: int


class QRPollResponse(BaseModel):
    status: str  # 'waiting', 'confirmed', '2fa_required', 'expired', 'failed'
    url: Optional[str] = None
    expires_at: Optional[int] = None
    confirmed_at: Optional[float] = None
    user: Optional[Dict[str, Any]] = None
    channels: Optional[List[Dict[str, Any]]] = None
    default_channel_id: Optional[str] = None


class QR2FARequest(BaseModel):
    session_id: str
    password: str


class QRFinalizeRequest(BaseModel):
    session_id: str
    display_name: Optional[str] = None
    action: str = "auto_create"  # 'auto_create' or 'select_existing'
    selected_channel_id: Optional[str] = None


class NASTestRequest(BaseModel):
    vault_root: Optional[str] = None


class NASSaveRequest(BaseModel):
    vault_root: str
    display_name: Optional[str] = "NAS Vault"


class BotLoginRequest(BaseModel):
    bot_token: str
    channel_id: str
    display_name: Optional[str] = "Storage Bot"


class DeveloperLoginRequest(BaseModel):
    api_id: int
    api_hash: str
    bot_token: str
    channel_id: str
    display_name: Optional[str] = "Developer Node"


class ByokSendCodeRequest(BaseModel):
    phone: str


class ByokExtractKeysRequest(BaseModel):
    phone: str
    random_hash: str
    code: str


# -----------------------------------------------------------------------------
# Automated Key Extraction (my.telegram.org)
# -----------------------------------------------------------------------------

_tg_apps_client = TelegramAppsClient()


@router.post("/byok/request-code")
async def byok_request_code(body: ByokSendCodeRequest):
    """Requests a web confirmation login code from my.telegram.org for the user's phone number."""
    try:
        random_hash = await _tg_apps_client.send_password(body.phone)
        clean_phone = re.sub(r"[^\d+]", "", body.phone.strip())
        if not clean_phone.startswith("+") and len(clean_phone) >= 10:
            clean_phone = f"+{clean_phone}"
        return {
            "success": True,
            "phone": clean_phone,
            "random_hash": random_hash,
            "message": "Confirmation code sent to your Telegram app.",
        }
    except AppsRateLimitError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    except AppsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except Exception as exc:
        logger.error(f"Error in request-code: {exc}")
        raise HTTPException(status_code=502, detail="Telegram web portal is temporarily unreachable.")


@router.post("/byok/extract-keys")
async def byok_extract_keys(body: ByokExtractKeysRequest):
    """
    Logs in to my.telegram.org with confirmation code, extracts or creates API application,
    saves the credentials locally, and immediately discards session tokens.
    """
    try:
        api_id, api_hash = await _tg_apps_client.login_and_get_keys(
            phone=body.phone,
            random_hash=body.random_hash,
            code=body.code,
        )

        # Save credentials to local auth config
        settings.TELEGRAM_API_ID = api_id
        settings.TELEGRAM_API_HASH = api_hash

        config_data = {}
        if settings.auth_config_file.exists():
            try:
                config_data = json.loads(settings.auth_config_file.read_text(encoding="utf-8"))
            except Exception:
                config_data = {}
        config_data["api_id"] = api_id
        config_data["api_hash"] = api_hash
        settings.save_auth_config(config_data)

        # Mask hash in response for security
        masked_hash = f"...{api_hash[-4:]}"
        return {
            "success": True,
            "api_id": api_id,
            "api_hash": api_hash,  # Preserved for backward compatibility with existing tests
            "api_hash_masked": masked_hash,
            "message": "Successfully extracted Telegram API keys.",
        }
    except AppsRateLimitError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    except AppsInvalidCodeError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    except Apps2FARequiredError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    except AppsParserError as exc:
        raise HTTPException(status_code=422, detail=exc.message)
    except AppsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except Exception as exc:
        logger.error(f"Error in extract-keys: {exc}")
        raise HTTPException(status_code=500, detail="Unexpected error during key extraction. Please enter keys manually.")


# -----------------------------------------------------------------------------
# Manual Key Save
# -----------------------------------------------------------------------------

@router.post("/keys")
async def save_manual_keys(body: KeysSaveRequest):
    """Validates and saves user-provided Telegram API keys locally."""
    if not body.api_id or body.api_id <= 0:
        raise HTTPException(status_code=400, detail="API ID must be a positive number.")

    clean_hash = (body.api_hash or "").strip()
    if len(clean_hash) != 32 or not all(c in "0123456789abcdefABCDEF" for c in clean_hash):
        raise HTTPException(status_code=400, detail="API Hash must be a 32-character hexadecimal string.")

    settings.TELEGRAM_API_ID = body.api_id
    settings.TELEGRAM_API_HASH = clean_hash.lower()

    config_data = {}
    if settings.auth_config_file.exists():
        try:
            config_data = json.loads(settings.auth_config_file.read_text(encoding="utf-8"))
        except Exception:
            config_data = {}
    config_data["api_id"] = body.api_id
    config_data["api_hash"] = clean_hash.lower()
    settings.save_auth_config(config_data)

    return {
        "success": True,
        "api_id": body.api_id,
        "api_hash_masked": f"...{clean_hash[-4:]}",
    }


# -----------------------------------------------------------------------------
# NAS Connection Test & Save
# -----------------------------------------------------------------------------

@router.post("/nas/test")
async def test_nas_connection(body: NASTestRequest):
    """
    Runs the NAS adapter's real check verifying availability, path existence,
    directory type, write permissions, and disk space.
    """
    from ..storage.adapters.nas import NASStorageAdapter

    path_str = (body.vault_root or "").strip() or str(
        getattr(settings, "NAS_VAULT_ROOT", None) or (settings.DATA_DIR / "nas_vault")
    )
    target_path = Path(os.path.realpath(path_str))

    if not target_path.exists():
        return {
            "success": False,
            "error": f"Path not found: '{path_str}' does not exist on disk.",
        }
    if not target_path.is_dir():
        return {
            "success": False,
            "error": f"Path is not a directory: '{path_str}' is a regular file.",
        }
    if not os.access(target_path, os.W_OK):
        return {
            "success": False,
            "error": f"Permission denied: Directory '{path_str}' is not writable.",
        }

    try:
        adapter = NASStorageAdapter(nas_root=target_path)
        health = await adapter.health_check()
        is_online = health.get("status") == "online"
        return {
            "success": is_online,
            "message": "Connection successful" if is_online else "Directory check failed",
            "root": str(target_path),
            "free_bytes": health.get("free_bytes", 0),
            "total_bytes": health.get("total_bytes", 0),
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Connection failed: {exc}",
        }


@router.post("/nas/save")
async def save_nas_login(body: NASSaveRequest):
    """
    Saves NAS storage credentials to system_settings and auth_config,
    activating NAS authenticated mode with no Telegram session required.
    """
    from ..storage.adapters.nas import NASStorageAdapter
    from ..storage.manager import register_storage_adapter
    from ..db.connection import get_db

    path_str = (body.vault_root or "").strip()
    if not path_str:
        raise HTTPException(status_code=400, detail="NAS vault path cannot be empty.")

    target_path = Path(os.path.realpath(path_str))
    if not target_path.exists():
        raise HTTPException(status_code=400, detail=f"Path not found: '{path_str}' does not exist.")
    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path '{path_str}' is not a directory.")
    if not os.access(target_path, os.W_OK):
        raise HTTPException(status_code=400, detail=f"Permission denied: '{path_str}' is not writable.")

    settings.NAS_VAULT_ROOT = target_path

    # Persist to system_settings in SQLite
    try:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO system_settings (key, value, updated_at)
                VALUES ('nas_config', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (json.dumps({"vault_root": str(target_path)}),),
            )
            await db.commit()
    except Exception as exc:
        logger.warning(f"Could not write nas_config to system_settings: {exc}")

    display_name = (body.display_name or "").strip() or "NAS Vault"
    config_payload = {
        "auth_mode": "nas",
        "channel_id": "nas_vault",
        "session_name": "vayom_nas",
        "nas_vault_root": str(target_path),
        "user_profile": {
            "display_name": display_name,
            "first_name": "NAS User",
            "is_premium": False,
        },
    }
    settings.save_auth_config(config_payload)

    adapter = NASStorageAdapter(nas_root=target_path)
    register_storage_adapter("nas", adapter)

    return {
        "success": True,
        "auth_mode": "nas",
        "channel_id": "nas_vault",
        "user": config_payload["user_profile"],
    }


# -----------------------------------------------------------------------------
# Status
# -----------------------------------------------------------------------------

@router.get("/status")
async def get_auth_status():
    """Returns authentication, saved keys, and active node configuration status."""
    settings.load_auth_config()

    is_authenticated = False
    if settings.AUTH_MODE == "nas":
        is_authenticated = True
    elif settings.AUTH_MODE in ("qr", "byok_qr", "bot", "developer"):
        is_authenticated = bool(storage_client._is_connected and settings.TELEGRAM_CHANNEL_ID)

    has_saved_keys = bool(settings.TELEGRAM_API_ID and settings.TELEGRAM_API_HASH)
    saved_api_id = settings.TELEGRAM_API_ID if has_saved_keys else None
    saved_api_hash_masked = (
        f"...{settings.TELEGRAM_API_HASH[-4:]}"
        if has_saved_keys and len(settings.TELEGRAM_API_HASH) >= 4
        else None
    )

    dev_mode = (
        os.getenv("VAYOM_DEV_MODE", os.getenv("DEV_MODE", "")).lower() in ("1", "true", "yes")
        or bool(os.getenv("VAYOM_DEV_LICENSE", ""))
    )

    return {
        "is_authenticated": is_authenticated,
        "auth_mode": settings.AUTH_MODE if is_authenticated else "",
        "channel_id": settings.TELEGRAM_CHANNEL_ID if is_authenticated else None,
        "user": settings.USER_PROFILE if is_authenticated else None,
        "has_saved_keys": has_saved_keys,
        "saved_api_id": saved_api_id,
        "saved_api_hash_masked": saved_api_hash_masked,
        "dev_mode_enabled": dev_mode,
    }


# -----------------------------------------------------------------------------
# QR Background Coroutines & Session Handling
# -----------------------------------------------------------------------------

async def _auto_refresh_tokens(session_id: str, qr_login, session_end_time: float, wait_done_event: asyncio.Event):
    """Refreshes MTProto token silently before expiration to keep QR valid."""
    consecutive_errors = 0
    max_recreate_retries = 5

    while time.time() < session_end_time and not wait_done_event.is_set():
        session = _active_qr_sessions.get(session_id)
        if not session or session.get("status") != "waiting":
            break

        expires = qr_login.expires
        now = datetime.datetime.now(datetime.timezone.utc)
        if isinstance(expires, datetime.datetime):
            time_to_expiry = (expires - now).total_seconds()
        else:
            time_to_expiry = 25.0

        sleep_duration = max(3.0, time_to_expiry - 3.0)
        try:
            await asyncio.wait_for(wait_done_event.wait(), timeout=sleep_duration)
            break
        except asyncio.TimeoutError:
            pass

        if time.time() >= session_end_time or wait_done_event.is_set():
            break

        try:
            await qr_login.recreate()
            consecutive_errors = 0
            if session_id in _active_qr_sessions:
                _active_qr_sessions[session_id]["url"] = qr_login.url
                logger.info(f"Telegram MTProto QR token refreshed silently for session {session_id}")
        except Exception as e:
            consecutive_errors += 1
            backoff = min(30.0, 3.0 * (2 ** (consecutive_errors - 1)))
            logger.warning(
                f"Error recreating QR token for {session_id} "
                f"({consecutive_errors}/{max_recreate_retries}): {e}. Backing off {backoff:.1f}s"
            )
            if consecutive_errors >= max_recreate_retries:
                logger.error(f"Max recreate retries exceeded for QR session {session_id}")
                if session_id in _active_qr_sessions:
                    _active_qr_sessions[session_id]["status"] = "failed"
                    _active_qr_sessions[session_id]["error"] = "Lost connection to Telegram. Check your internet connection."
                break
            await asyncio.sleep(backoff)


async def _monitor_qr(session_id: str, qr_login, client: TelegramClient, session_end_time: float):
    """Background coroutine awaiting QR scan confirmation, 2FA, or timeout."""
    wait_done_event = asyncio.Event()
    refresh_task = asyncio.create_task(
        _auto_refresh_tokens(session_id, qr_login, session_end_time, wait_done_event)
    )

    try:
        user = None
        while time.time() < session_end_time and not wait_done_event.is_set():
            remaining_time = max(1.0, session_end_time - time.time())
            try:
                user = await qr_login.wait(timeout=remaining_time)
                if user:
                    break
            except SessionPasswordNeededError:
                wait_done_event.set()
                if session_id in _active_qr_sessions:
                    _active_qr_sessions[session_id]["status"] = "2fa_required"
                return
            except Exception as e:
                err_str = str(e)
                if "ImportLoginTokenRequest" in err_str or "re-scanned" in err_str or "expired" in err_str.lower():
                    logger.info("DC migration or token expired during switch, recreating...")
                    await qr_login.recreate()
                    if session_id in _active_qr_sessions:
                        _active_qr_sessions[session_id]["url"] = qr_login.url
                    continue
                logger.warning(f"QR login wait error: {e}")
                raise

        if not user:
            raise asyncio.TimeoutError("QR login session timed out")

        wait_done_event.set()

        channels = []
        default_channel_id = None
        try:
            dialogs = await client.get_dialogs(limit=25)
            for d in dialogs:
                if d.is_channel:
                    c_id = f"-100{d.id}" if not str(d.id).startswith("-100") else str(d.id)
                    channels.append({
                        "id": c_id,
                        "title": d.title,
                        "is_creator": getattr(d.entity, "creator", False),
                    })
                    if "vayom" in d.title.lower() or "vault" in d.title.lower():
                        default_channel_id = c_id
        except Exception as e:
            logger.warning(f"Fast channel check on QR login: {e}")

        if session_id in _active_qr_sessions:
            _active_qr_sessions[session_id].update({
                "status": "confirmed",
                "confirmed_at": time.time(),
                "user": {
                    "id": user.id,
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "username": user.username or "",
                    "is_premium": getattr(user, "premium", False),
                },
                "channels": channels,
                "default_channel_id": default_channel_id,
            })
    except Exception as e:
        wait_done_event.set()
        logger.info(f"QR login session {session_id} ended: {e}")
        if session_id in _active_qr_sessions:
            _active_qr_sessions[session_id]["status"] = "expired"
        try:
            if client and hasattr(client, "is_connected") and client.is_connected():
                await client.disconnect()
        except Exception:
            pass
    finally:
        wait_done_event.set()
        if not refresh_task.done():
            refresh_task.cancel()


@router.post("/qr/start", response_model=QRStartResponse)
async def start_qr_login(body: Optional[QRStartRequest] = None):
    """
    Initializes Telethon user MTProto client and returns a dynamic tg://login?token= URI.
    Uses separate session files for own keys ('vayom_user_own') vs shared keys ('vayom_user_shared')
    to prevent session collision.
    """
    mode = (body.mode if body else None) or ("own" if (body and body.api_id) else None)
    is_shared = mode == "shared"
    is_byok = bool(body and body.api_id and body.api_hash)

    target_id, target_hash = _require_telegram_app_credentials(
        api_id=body.api_id if body else None,
        api_hash=body.api_hash if body else None,
    )
    session_base = "vayom_user_shared" if is_shared else "vayom_user_own"

    # Disconnect any active storage client before touching session files
    await storage_client.disconnect()

    # Cancel previous pending QR sessions
    for old_id, old_sess in list(_active_qr_sessions.items()):
        try:
            mon_task = old_sess.get("monitor_task")
            if mon_task and not mon_task.done():
                mon_task.cancel()
            old_client = old_sess.get("client")
            if old_client and getattr(old_client, "is_connected", None):
                is_conn = old_client.is_connected()
                if asyncio.iscoroutine(is_conn):
                    is_conn = await is_conn
                if is_conn:
                    await old_client.disconnect()
        except Exception:
            pass
        _active_qr_sessions.pop(old_id, None)

    session_id = str(uuid.uuid4())
    session_file = str(settings.DATA_DIR / session_base)
    session_path = settings.DATA_DIR / f"{session_base}.session"

    if not session_path.exists():
        try:
            s = RobustSQLiteSession(session_file)
            s.set_dc(5, "91.108.56.164", 443)
            s.save()
            s.close()
        except Exception as e:
            logger.warning(f"Could not pre-initialize DC 5: {e}")

    try:
        import sqlite3
        conn = sqlite3.connect(str(session_path))
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.commit()
        conn.close()
        _secure_file_permissions(session_path)
    except Exception as e:
        logger.warning(f"Could not configure WAL mode on {session_path}: {e}")

    client: Optional[TelegramClient] = None
    try:
        robust_session = RobustSQLiteSession(session_file)
        client = TelegramClient(robust_session, int(target_id), str(target_hash))

        async def _handshake():
            await client.connect()
            return await client.qr_login()

        qr_login = await asyncio.wait_for(_handshake(), timeout=QR_HANDSHAKE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        try:
            await storage_client.reconnect()
        except Exception:
            pass
        raise HTTPException(
            status_code=504,
            detail="Timed out connecting to Telegram. Check your internet connection and try again.",
        )
    except (ApiIdInvalidError, ValueError) as exc:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        try:
            await storage_client.reconnect()
        except Exception:
            pass
        logger.warning(f"Telegram rejected credentials: {exc}")
        err_msg = (
            "Telegram rejected the provided API credentials. Verify your API ID and API Hash from my.telegram.org and try again."
            if is_byok else
            "Telegram rejected the configured API credentials. Verify TELEGRAM_API_ID and TELEGRAM_API_HASH and restart Vayom Drive."
        )
        raise HTTPException(
            status_code=503,
            detail=err_msg,
        )
    except FloodWaitError as exc:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        try:
            await storage_client.reconnect()
        except Exception:
            pass
        raise HTTPException(
            status_code=429,
            detail=f"Telegram rate limit reached. Please wait {exc.seconds} seconds before trying again.",
        )
    except Exception as exc:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        try:
            await storage_client.reconnect()
        except Exception:
            pass
        logger.error(f"QR login initialization failed: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Could not reach Telegram. Check your internet connection and try again.",
        )

    session_expires_at = int(time.time()) + settings.QR_SESSION_TTL_SECONDS

    _active_qr_sessions[session_id] = {
        "client": client,
        "qr": qr_login,
        "url": qr_login.url,
        "session_expires_at": session_expires_at,
        "status": "waiting",
        "api_id": target_id,
        "api_hash": target_hash,
        "is_byok": is_byok,
        "session_base": session_base,
        "user": None,
        "channels": None,
        "default_channel_id": None,
    }

    mon_task = asyncio.create_task(_monitor_qr(session_id, qr_login, client, float(session_expires_at)))
    _active_qr_sessions[session_id]["monitor_task"] = mon_task

    return {
        "session_id": session_id,
        "url": qr_login.url,
        "expires_at": session_expires_at,
    }


@router.get("/qr/poll", response_model=QRPollResponse)
async def poll_qr_login(session_id: str):
    """Polls status of ongoing QR login session."""
    session = _active_qr_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="QR session not found or expired")

    if session["status"] in ("waiting", "2fa_required") and time.time() >= session.get("session_expires_at", 0):
        session["status"] = "expired"

    if session["status"] == "expired":
        client = session.get("client")
        if client and hasattr(client, "is_connected") and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
        mon_task = session.get("monitor_task")
        if mon_task and not mon_task.done():
            mon_task.cancel()
        _active_qr_sessions.pop(session_id, None)
        return {
            "status": "expired",
            "url": None,
            "expires_at": int(session["session_expires_at"]) if session.get("session_expires_at") else None,
            "confirmed_at": None,
            "user": None,
            "channels": None,
            "default_channel_id": None,
        }

    return {
        "status": session["status"],
        "url": session.get("url"),
        "expires_at": int(session["session_expires_at"]) if session.get("session_expires_at") else None,
        "confirmed_at": session.get("confirmed_at"),
        "user": session.get("user"),
        "channels": session.get("channels"),
        "default_channel_id": session.get("default_channel_id"),
    }


@router.post("/qr/2fa")
async def submit_qr_2fa(body: QR2FARequest):
    """Submits 2FA cloud password for accounts with two-step verification enabled."""
    session = _active_qr_sessions.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="QR session not found")

    if time.time() >= session.get("session_expires_at", 0):
        session["status"] = "expired"
        client = session.get("client")
        if client and hasattr(client, "is_connected") and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
        _active_qr_sessions.pop(body.session_id, None)
        raise HTTPException(status_code=400, detail="QR session has expired. Please scan the QR code again.")

    client: TelegramClient = session["client"]
    try:
        user = await client.sign_in(password=body.password)
        channels = []
        default_channel_id = None
        try:
            dialogs = await client.get_dialogs(limit=25)
            for d in dialogs:
                if d.is_channel:
                    c_id = f"-100{d.id}" if not str(d.id).startswith("-100") else str(d.id)
                    channels.append({
                        "id": c_id,
                        "title": d.title,
                        "is_creator": getattr(d.entity, "creator", False),
                    })
                    if "vayom" in d.title.lower() or "vault" in d.title.lower():
                        default_channel_id = c_id
        except Exception as e:
            logger.warning(f"Channel fetch on 2FA success: {e}")

        session.update({
            "status": "confirmed",
            "user": {
                "id": user.id,
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "username": user.username or "",
                "is_premium": getattr(user, "premium", False),
            },
            "channels": channels,
            "default_channel_id": default_channel_id,
        })
        return {"success": True}
    except PasswordHashInvalidError:
        raise HTTPException(status_code=400, detail="Invalid two-step verification password. Check your password and try again.")
    except FloodWaitError as exc:
        raise HTTPException(status_code=429, detail=f"Telegram rate limit reached. Please wait {exc.seconds} seconds before trying again.")
    except Exception as e:
        logger.error(f"2FA verification failed: {e}")
        raise HTTPException(status_code=400, detail="Incorrect two-step verification password.")


@router.post("/qr/finalize")
async def finalize_qr_login(body: QRFinalizeRequest):
    """
    Finalizes user onboarding after QR scan:
    - Creates or binds to vault channel
    - Persists auth config and activates storage client
    """
    session = _active_qr_sessions.get(body.session_id)
    if not session or session.get("status") != "confirmed":
        raise HTTPException(status_code=400, detail="QR session is not in confirmed state")

    create_lock = session.setdefault("create_lock", asyncio.Lock())
    async with create_lock:
        if session.get("is_finalized"):
            return session["finalized_result"]

        client: TelegramClient = session["client"]
        user_data = session["user"]
        display_name = body.display_name or user_data.get("first_name") or "Vault User"

        channel_id = body.selected_channel_id
        if body.action == "auto_create" or not channel_id:
            if session.get("created_channel_id"):
                channel_id = session["created_channel_id"]
            else:
                confirmed_at = session.get("confirmed_at", 0)
                elapsed = time.time() - confirmed_at
                if elapsed < 10.0:
                    wait_needed = 10.0 - elapsed
                    logger.info(f"Enforcing safety idle window before channel creation ({wait_needed:.2f}s)...")
                    await asyncio.sleep(wait_needed)

                try:
                    res = await client(CreateChannelRequest(
                        title=f"{display_name}'s Vayom Vault",
                        about="Private Cloud Storage powered by Telegram & Vayom Drive",
                        megagroup=False,
                    ))
                    created_chat = res.chats[0]
                    channel_id = f"-100{created_chat.id}"
                    session["created_channel_id"] = channel_id
                    logger.info(f"Auto-created private vault channel: {channel_id}")
                except Exception as e:
                    logger.error(f"Failed to auto-create channel: {e}")
                    raise HTTPException(status_code=500, detail="Could not auto-create storage channel. Please verify channel permissions.")

        # Ensure channel is private
        try:
            target_entity = await client.get_entity(int(channel_id) if str(channel_id).lstrip("-").isdigit() else channel_id)
            has_public_username = bool(
                getattr(target_entity, "username", None) or
                getattr(target_entity, "usernames", None)
            )
            if has_public_username:
                from telethon.tl.functions.channels import UpdateUsernameRequest
                await client(UpdateUsernameRequest(channel=target_entity, username=""))
                logger.info(f"Successfully privatized channel {channel_id}")
        except Exception as priv_err:
            logger.warning(f"Privacy check encountered: {priv_err}")

        target_api_id = session.get("api_id") or settings.TELEGRAM_API_ID
        target_api_hash = session.get("api_hash") or settings.TELEGRAM_API_HASH
        is_byok = bool(session.get("is_byok"))
        session_base = session.get("session_base", "vayom_user_own" if is_byok else "vayom_user_shared")

        config_payload = {
            "auth_mode": "byok_qr" if is_byok else "qr",
            "session_name": session_base,
            "api_id": target_api_id,
            "api_hash": target_api_hash,
            "channel_id": str(channel_id),
            "user_profile": {
                "display_name": display_name,
                "first_name": user_data.get("first_name"),
                "username": user_data.get("username"),
                "id": user_data.get("id"),
                "is_premium": user_data.get("is_premium"),
            },
        }
        settings.save_auth_config(config_payload)
        _secure_file_permissions(settings.DATA_DIR / f"{session_base}.session")

        try:
            await client.disconnect()
        except Exception:
            pass

        result = {
            "success": True,
            "channel_id": channel_id,
            "user": config_payload["user_profile"],
        }

        try:
            await storage_client.reconnect()
        except Exception as reconnect_err:
            logger.error(f"Storage client reconnect failed during QR finalization: {reconnect_err}")
            raise HTTPException(
                status_code=503,
                detail="Could not connect to Telegram after setup. Your vault channel is preserved — retry to finish onboarding.",
            )

        session["is_finalized"] = True
        session["finalized_result"] = result
        _active_qr_sessions.pop(body.session_id, None)

        return result


# -----------------------------------------------------------------------------
# Bot & Developer Mode Sign-in
# -----------------------------------------------------------------------------

async def _validate_channel_for_bot(client: TelegramClient, channel_id_raw: Any) -> str:
    chan_str = str(channel_id_raw or "").strip()
    if not chan_str:
        raise ValueError("Channel ID cannot be empty.")
    try:
        chan_int = int(chan_str)
    except ValueError:
        raise ValueError(f"Channel ID must be numeric (e.g. -100xxxxxxxxxx), received '{chan_str}'.")

    try:
        entity = await client.get_entity(chan_int)
    except Exception as err:
        raise ValueError(f"Could not reach or resolve storage channel {chan_int}: {err}")

    if getattr(entity, "broadcast", False):
        is_creator = getattr(entity, "creator", False)
        admin_rights = getattr(entity, "admin_rights", None)
        can_post = is_creator or (admin_rights is not None and getattr(admin_rights, "post_messages", True))
        if not can_post:
            raise ValueError(f"Bot lacks administrator posting permissions in broadcast channel '{chan_str}'.")

    return str(chan_int)


@router.post("/bot")
async def login_with_bot(body: BotLoginRequest):
    """Connects to Telegram using a dedicated Bot Token and Channel ID."""
    _require_telegram_app_credentials()

    await storage_client.disconnect()
    _delete_session_files("vayom_bot")

    temp_client = None
    try:
        temp_client = TelegramClient(
            str(settings.DATA_DIR / "vayom_bot"),
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH,
        )
        await temp_client.start(bot_token=body.bot_token)
        verified_channel_id = await _validate_channel_for_bot(temp_client, body.channel_id)
        await temp_client.disconnect()
    except Exception as e:
        if temp_client is not None:
            try:
                await temp_client.disconnect()
            except Exception:
                pass
        try:
            await storage_client.reconnect()
        except Exception:
            pass
        logger.error(f"Bot login failed: {e}")
        raise HTTPException(status_code=400, detail=f"Bot login failed: {e}")

    config_payload = {
        "auth_mode": "bot",
        "session_name": "vayom_bot",
        "bot_token": body.bot_token,
        "channel_id": verified_channel_id,
        "user_profile": {
            "display_name": body.display_name or "Storage Bot",
            "first_name": "Bot",
            "is_premium": False,
        },
    }
    settings.save_auth_config(config_payload)
    _secure_file_permissions(settings.DATA_DIR / "vayom_bot.session")
    await storage_client.reconnect()
    return {"success": True, "channel_id": verified_channel_id}


@router.post("/developer")
async def login_with_developer(body: DeveloperLoginRequest):
    """Developer Mode: Configures custom API_ID, API_HASH, Bot Token, and Channel ID."""
    if not body.api_id or not body.api_hash.strip():
        raise HTTPException(
            status_code=400,
            detail="API ID and API Hash are required for developer mode.",
        )

    await storage_client.disconnect()
    _delete_session_files("vayom_bot")

    temp_client: Optional[TelegramClient] = None
    try:
        temp_client = TelegramClient(
            str(settings.DATA_DIR / "vayom_bot"),
            body.api_id,
            body.api_hash.strip(),
        )
        await temp_client.start(bot_token=body.bot_token)
        verified_channel_id = await _validate_channel_for_bot(temp_client, body.channel_id)
        await temp_client.disconnect()
    except HTTPException:
        if temp_client is not None:
            try:
                await temp_client.disconnect()
            except Exception:
                pass
        try:
            await storage_client.reconnect()
        except Exception:
            pass
        raise
    except Exception as e:
        if temp_client is not None:
            try:
                await temp_client.disconnect()
            except Exception:
                pass
        try:
            await storage_client.reconnect()
        except Exception:
            pass
        logger.error(f"Developer login failed: {e}")
        raise HTTPException(status_code=400, detail=f"Developer login failed: {e}")

    config_payload = {
        "auth_mode": "developer",
        "session_name": "vayom_bot",
        "api_id": body.api_id,
        "api_hash": body.api_hash,
        "bot_token": body.bot_token,
        "channel_id": verified_channel_id,
        "user_profile": {
            "display_name": body.display_name or "Developer Node",
            "first_name": "Custom MTProto",
            "is_premium": False,
        },
    }
    settings.save_auth_config(config_payload)
    _secure_file_permissions(settings.DATA_DIR / "vayom_bot.session")
    await storage_client.reconnect()
    return {"success": True, "channel_id": verified_channel_id}


# -----------------------------------------------------------------------------
# Logout
# -----------------------------------------------------------------------------

@router.post("/logout")
async def logout():
    """Logs out of active node, revokes Telegram session when possible, and cleans up local state."""
    active_client = getattr(storage_client, "client", None)
    if active_client is not None:
        try:
            if hasattr(active_client, "is_connected") and active_client.is_connected():
                await active_client.log_out()
        except Exception as exc:
            logger.warning(f"Telegram server-side logout failed; continuing local cleanup: {exc}")

    for old_id, old_sess in list(_active_qr_sessions.items()):
        try:
            mon_task = old_sess.get("monitor_task")
            if mon_task and not mon_task.done():
                mon_task.cancel()
            old_client = old_sess.get("client")
            if old_client and getattr(old_client, "is_connected", None):
                is_conn = old_client.is_connected()
                if asyncio.iscoroutine(is_conn):
                    is_conn = await is_conn
                if is_conn:
                    await old_client.disconnect()
        except Exception:
            pass
        _active_qr_sessions.pop(old_id, None)

    settings.clear_auth_config()
    await storage_client.disconnect()

    removed = []
    for name in ("vayom_user_own", "vayom_user_shared", "vayom_user", "vayom_bot", "vayom_nas"):
        removed.extend(_delete_session_files(name))

    return {"success": True, "sessions_removed": removed}
