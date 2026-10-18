import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv

# Load .env file from project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger("vayom.config")

class Settings:
    ROOT_DIR: Path = ROOT_DIR

    # Telegram Base Application Credentials (for MTProto handshake)
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHANNEL_ID: str = ""
    TELEGRAM_BACKUP_CHANNEL_ID: str = os.getenv("TELEGRAM_BACKUP_CHANNEL_ID", "")
    AUTO_MIRROR_SAVED_MESSAGES: bool = True
    MAX_CONCURRENT_UPLOADS: int = int(os.getenv("MAX_CONCURRENT_UPLOADS", "3"))
    CHUNK_PACING_MS: int = int(os.getenv("CHUNK_PACING_MS", "25"))

    # Security & Admin
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "vayom123")
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "vayom_drive_secret_key_change_in_prod")
    STREAM_LOOPBACK_TOKEN: str = os.getenv("STREAM_LOOPBACK_TOKEN", "")
    LOCAL_API_TOKEN: str = os.getenv("LOCAL_API_TOKEN", "")
    REQUIRE_API_AUTH: bool = os.getenv("REQUIRE_API_AUTH", "false").lower() == "true"
    CORS_ALLOWED_ORIGINS: List[str] = (
        [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
        if os.getenv("CORS_ALLOWED_ORIGINS")
        else []
    )
    ENCRYPTION_ENABLED: bool = os.getenv("ENCRYPTION_ENABLED", "false").lower() == "true"

    # Server Binding (A-1: Default bind to loopback 127.0.0.1 instead of 0.0.0.0)
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Storage Paths
    DATA_DIR: Path = ROOT_DIR / os.getenv("DATA_DIR", "data")
    DB_PATH: Path = DATA_DIR / "vayom.db"
    UPLOAD_TEMP_DIR: Path = DATA_DIR / "uploads"
    THUMBNAIL_DIR: Path = DATA_DIR / "thumbnails"

    # Workstation Security Settings
    WORKSTATION_ALLOWED_ROOTS: List[str] = (
        [r.strip() for r in os.getenv("WORKSTATION_ALLOWED_ROOTS", "").split(",") if r.strip()]
        if os.getenv("WORKSTATION_ALLOWED_ROOTS")
        else [
            str(Path.home()),
            "/media",
            "/mnt",
            "/Volumes",
            tempfile.gettempdir(),
        ]
    )

    # File Slicing Limits
    # 1.90 GB default to safely stay under Telegram's 2,000 MB limit
    MAX_PART_BYTES: int = int(os.getenv("MAX_PART_BYTES", "1900000000"))
    ALLOW_LOSSY_VIDEO: bool = os.getenv("ALLOW_LOSSY_VIDEO", "false").lower() == "true"

    # Cloud & Heartbeat Settings
    HEARTBEAT_INTERVAL_SECONDS: int = 600  # 10 minutes
    PING_URL: str = os.getenv("PING_URL", "")

    # Dynamic Authentication & Account State
    AUTH_MODE: str = ""                         # 'qr', 'bot', 'developer'
    USER_PROFILE: dict = {}                     # {'first_name', 'username', 'id', 'is_premium'}
    SESSION_NAME: str = "vayom_bot"
    QR_SESSION_TTL_SECONDS: int = int(os.getenv("QR_SESSION_TTL_SECONDS", "300"))  # 5 minutes expiry

    def ensure_directories(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.UPLOAD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

    _custom_auth_config_file: Optional[Path] = None

    @property
    def auth_config_file(self) -> Path:
        if self._custom_auth_config_file is not None:
            return self._custom_auth_config_file
        return self.DATA_DIR / "auth_config.json"

    @auth_config_file.setter
    def auth_config_file(self, value: Optional[Path]):
        self._custom_auth_config_file = value

    def load_auth_config(self):
        """Loads runtime authentication state from data/auth_config.json or falls back to .env."""
        import json
        if self.auth_config_file.exists():
            # A-5: auth config holds api_hash/bot_token — keep it owner-only readable
            try:
                os.chmod(self.auth_config_file, 0o600)
            except Exception:
                pass
            try:
                data = json.loads(self.auth_config_file.read_text(encoding="utf-8"))
                self.AUTH_MODE = data.get("auth_mode", "")
                self.TELEGRAM_API_ID = int(data.get("api_id") or self.TELEGRAM_API_ID)
                self.TELEGRAM_API_HASH = data.get("api_hash") or self.TELEGRAM_API_HASH
                self.TELEGRAM_BOT_TOKEN = data.get("bot_token") or ""
                self.TELEGRAM_CHANNEL_ID = str(data.get("channel_id") or "")
                self.TELEGRAM_BACKUP_CHANNEL_ID = str(data.get("backup_channel_id") or self.TELEGRAM_BACKUP_CHANNEL_ID)
                self.STREAM_LOOPBACK_TOKEN = str(data.get("stream_loopback_token") or self.STREAM_LOOPBACK_TOKEN)
                self.LOCAL_API_TOKEN = str(data.get("local_api_token") or self.LOCAL_API_TOKEN)
                if not self.LOCAL_API_TOKEN:
                    import secrets
                    self.LOCAL_API_TOKEN = secrets.token_hex(24)
                self.USER_PROFILE = data.get("user_profile") or {}
                self.SESSION_NAME = data.get("session_name") or ("vayom_user" if self.AUTH_MODE == "qr" else "vayom_bot")
                # F-2: premium upgrades slicing to 4 GB, but the limit must fall back
                # to the standard 1.9 GB whenever premium is absent or has expired.
                if self.USER_PROFILE.get("is_premium"):
                    self.MAX_PART_BYTES = 3900000000  # 4 GB chunks for Telegram Premium users!
                else:
                    self.MAX_PART_BYTES = int(os.getenv("MAX_PART_BYTES", "1900000000"))
                return
            except Exception as e:
                # F-3: never silently swallow a corrupt/unreadable config — move it aside
                logger.warning(f"Could not load auth config {self.auth_config_file}: {e}")
                try:
                    bad_path = self.auth_config_file.with_name(self.auth_config_file.name + ".corrupt")
                    self.auth_config_file.rename(bad_path)
                    logger.warning(f"Moved unreadable auth config aside to {bad_path}")
                except Exception:
                    pass

        # Strictly require explicit login! No auto-login from .env
        self.AUTH_MODE = ""
        self.USER_PROFILE = {}
        self.TELEGRAM_BOT_TOKEN = ""
        self.TELEGRAM_CHANNEL_ID = ""
        self.TELEGRAM_BACKUP_CHANNEL_ID = ""
        self.SESSION_NAME = "vayom_bot"

    def save_auth_config(self, data: dict):
        """Persists runtime authentication configuration to data/auth_config.json (owner-only)."""
        import json
        if "local_api_token" not in data and self.LOCAL_API_TOKEN:
            data["local_api_token"] = self.LOCAL_API_TOKEN
        self.auth_config_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # A-5: restrict permissions — the file contains api_hash/bot_token
        try:
            os.chmod(self.auth_config_file, 0o600)
        except Exception:
            pass
        self.load_auth_config()

    def clear_auth_config(self):
        """Clears saved auth config and active session."""
        if self.auth_config_file.exists():
            try:
                self.auth_config_file.unlink()
            except Exception:
                pass
        self.AUTH_MODE = ""
        self.USER_PROFILE = {}
        self.TELEGRAM_BOT_TOKEN = ""
        self.TELEGRAM_CHANNEL_ID = ""
        self.TELEGRAM_BACKUP_CHANNEL_ID = ""
        self.SESSION_NAME = "vayom_bot"
        self.LOCAL_API_TOKEN = ""
        # F-2: logout must not leave a premium-inflated 4 GB slice limit behind
        self.MAX_PART_BYTES = int(os.getenv("MAX_PART_BYTES", "1900000000"))

settings = Settings()
settings.ensure_directories()
settings.load_auth_config()
