import asyncio
import logging
import uuid
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.channels import CreateChannelRequest
from ..config import settings
from ..storage.client import storage_client

logger = logging.getLogger("vayom.auth")
router = APIRouter(prefix="/api/v1/auth")

# In-memory tracking for active QR login state
_active_qr_sessions: Dict[str, Dict[str, Any]] = {}


class QRStartResponse(BaseModel):
    session_id: str
    url: str
    expires_at: int


class QRPollResponse(BaseModel):
    status: str  # 'waiting', 'confirmed', '2fa_required', 'expired'
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


@router.get("/status")
async def get_auth_status():
    """Returns the current authentication and node configuration status."""
    settings.load_auth_config()
    is_authenticated = bool(storage_client._is_connected and settings.TELEGRAM_CHANNEL_ID)

    return {
        "is_authenticated": is_authenticated,
        "auth_mode": settings.AUTH_MODE or ("bot" if is_authenticated else ""),
        "channel_id": settings.TELEGRAM_CHANNEL_ID if is_authenticated else None,
        "user": settings.USER_PROFILE or {
            "display_name": "Vault User",
            "first_name": "Vault",
            "is_premium": False
        } if is_authenticated else None
    }


async def _monitor_qr(session_id: str, qr_login, client: TelegramClient):
    """Background coroutine to await user QR scan confirmation or 2FA challenge."""
    try:
        user = await qr_login.wait(timeout=180)
        # Fetch user's channels with a single lightweight query
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
                        "is_creator": getattr(d.entity, "creator", False)
                    })
                    if "vayom" in d.title.lower() or "vault" in d.title.lower():
                        default_channel_id = c_id
        except Exception as e:
            logger.warning(f"Fast channel check on QR login encountered: {e}")

        _active_qr_sessions[session_id].update({
            "status": "confirmed",
            "user": {
                "id": user.id,
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "username": user.username or "",
                "is_premium": getattr(user, "premium", False)
            },
            "channels": channels,
            "default_channel_id": default_channel_id
        })
    except SessionPasswordNeededError:
        _active_qr_sessions[session_id]["status"] = "2fa_required"
    except Exception as e:
        logger.info(f"QR login session {session_id} expired or cancelled: {e}")
        _active_qr_sessions[session_id]["status"] = "expired"


@router.post("/qr/start", response_model=QRStartResponse)
async def start_qr_login():
    """Initializes Telethon user MTProto client and returns dynamic tg://login?token= URI."""
    session_id = str(uuid.uuid4())
    session_file = str(settings.DATA_DIR / "vayom_user")

    client = TelegramClient(
        session_file,
        settings.TELEGRAM_API_ID,
        settings.TELEGRAM_API_HASH
    )
    await client.connect()

    qr_login = await client.qr_login()

    _active_qr_sessions[session_id] = {
        "client": client,
        "qr": qr_login,
        "status": "waiting",
        "user": None,
        "channels": None,
        "default_channel_id": None
    }

    asyncio.create_task(_monitor_qr(session_id, qr_login, client))

    return {
        "session_id": session_id,
        "url": qr_login.url,
        "expires_at": int(qr_login.expires.timestamp()) if hasattr(qr_login.expires, "timestamp") else int(qr_login.expires)
    }


@router.get("/qr/poll", response_model=QRPollResponse)
async def poll_qr_login(session_id: str):
    """Polls the status of the ongoing QR login session."""
    session = _active_qr_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="QR session not found or expired")

    return {
        "status": session["status"],
        "user": session.get("user"),
        "channels": session.get("channels"),
        "default_channel_id": session.get("default_channel_id")
    }


@router.post("/qr/2fa")
async def submit_qr_2fa(body: QR2FARequest):
    """Submits 2FA cloud password for accounts with two-step verification enabled."""
    session = _active_qr_sessions.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="QR session not found")

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
                        "is_creator": getattr(d.entity, "creator", False)
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
                "is_premium": getattr(user, "premium", False)
            },
            "channels": channels,
            "default_channel_id": default_channel_id
        })
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"2FA verification failed: {e}")


@router.post("/qr/finalize")
async def finalize_qr_login(body: QRFinalizeRequest):
    """
    Finalizes user onboarding after QR scan:
    - Sets user display name (keeps Telegram name or custom name)
    - Automatically creates 'Vayom Vault' channel (Sign-up) or attaches to selected channel (Sign-in)
    - Persists config and activates storage client.
    """
    session = _active_qr_sessions.get(body.session_id)
    if not session or session.get("status") != "confirmed":
        raise HTTPException(status_code=400, detail="QR session is not in confirmed state")

    client: TelegramClient = session["client"]
    user_data = session["user"]
    display_name = body.display_name or user_data.get("first_name") or "Vault User"

    channel_id = body.selected_channel_id
    if body.action == "auto_create" or not channel_id:
        try:
            # 1 single clean API call to create the private channel
            res = await client(CreateChannelRequest(
                title=f"{display_name}'s Vayom Vault",
                about="Private Cloud Storage powered by Telegram & Vayom Drive",
                megagroup=False
            ))
            created_chat = res.chats[0]
            channel_id = f"-100{created_chat.id}"
            logger.info(f"Auto-created private vault channel: {channel_id}")
        except Exception as e:
            logger.error(f"Failed to auto-create channel: {e}")
            raise HTTPException(status_code=500, detail=f"Could not auto-create channel: {e}")

    # Persist runtime configuration
    config_payload = {
        "auth_mode": "qr",
        "session_name": "vayom_user",
        "api_id": settings.TELEGRAM_API_ID,
        "api_hash": settings.TELEGRAM_API_HASH,
        "channel_id": str(channel_id),
        "user_profile": {
            "display_name": display_name,
            "first_name": user_data.get("first_name"),
            "username": user_data.get("username"),
            "id": user_data.get("id"),
            "is_premium": user_data.get("is_premium")
        }
    }
    settings.save_auth_config(config_payload)

    # Disconnect the temporary qr client and connect the singleton storage client
    try:
        await client.disconnect()
    except Exception:
        pass
    _active_qr_sessions.pop(body.session_id, None)

    await storage_client.reconnect()
    return {
        "success": True,
        "channel_id": channel_id,
        "user": config_payload["user_profile"]
    }


@router.post("/bot")
async def login_with_bot(body: BotLoginRequest):
    """
    Connects to Telegram using a dedicated Bot Token and Channel ID.
    Validates credentials and connects node.
    """
    temp_client = TelegramClient(
        str(settings.DATA_DIR / "vayom_bot"),
        settings.TELEGRAM_API_ID,
        settings.TELEGRAM_API_HASH
    )
    try:
        await temp_client.start(bot_token=body.bot_token)
        # Test channel access
        try:
            chan_int = int(body.channel_id)
            await temp_client.get_entity(chan_int)
        except Exception as err:
            logger.warning(f"Could not verify channel entity during bot login: {err}")
        await temp_client.disconnect()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Bot Token or Telegram error: {e}")

    config_payload = {
        "auth_mode": "bot",
        "session_name": "vayom_bot",
        "bot_token": body.bot_token,
        "channel_id": str(body.channel_id),
        "user_profile": {
            "display_name": body.display_name or "Storage Bot",
            "first_name": "Bot",
            "is_premium": False
        }
    }
    settings.save_auth_config(config_payload)
    await storage_client.reconnect()
    return {"success": True, "channel_id": body.channel_id}


@router.post("/developer")
async def login_with_developer(body: DeveloperLoginRequest):
    """
    Sovereign Developer Mode: Configures custom API_ID, API_HASH, Bot Token, and Channel ID.
    """
    temp_client = TelegramClient(
        str(settings.DATA_DIR / "vayom_bot"),
        body.api_id,
        body.api_hash
    )
    try:
        await temp_client.start(bot_token=body.bot_token)
        await temp_client.disconnect()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to connect with custom credentials: {e}")

    config_payload = {
        "auth_mode": "developer",
        "session_name": "vayom_bot",
        "api_id": body.api_id,
        "api_hash": body.api_hash,
        "bot_token": body.bot_token,
        "channel_id": str(body.channel_id),
        "user_profile": {
            "display_name": body.display_name or "Developer Node",
            "first_name": "Custom MTProto",
            "is_premium": False
        }
    }
    settings.save_auth_config(config_payload)
    await storage_client.reconnect()
    return {"success": True, "channel_id": body.channel_id}


@router.post("/logout")
async def logout():
    """Logs out of the active node, disconnects MTProto, and clears stored auth config."""
    settings.clear_auth_config()
    await storage_client.disconnect()
    return {"success": True}
