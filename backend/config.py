import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv

import sys
# Load .env file from project root
IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    ROOT_DIR = Path(sys.executable).resolve().parent
    if sys.platform == "win32":
        DEFAULT_DATA_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "Vayom Drive"
    elif sys.platform == "darwin":
        DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "Vayom Drive"
    else:
        DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "vayom-drive"
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent
    DEFAULT_DATA_DIR = ROOT_DIR / "data"

load_dotenv(ROOT_DIR / ".env")
if IS_FROZEN:
    # Packaged installs (AppImage/deb) are read-only; allow an owner config file
    # in the per-user config directory without requiring a writable app bundle.
    if sys.platform == "win32":
        _user_config = Path(os.getenv("APPDATA", str(Path.home()))) / "Vayom Drive" / ".env"
    elif sys.platform == "darwin":
        _user_config = Path.home() / "Library" / "Application Support" / "Vayom Drive" / ".env"
    else:
        _user_config = Path.home() / ".config" / "vayom-drive" / ".env"
    load_dotenv(_user_config, override=False)

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

    # Security & Admin (F-4: No hardcoded default credentials; empty unless explicitly set)
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "")
    ENABLE_SHARE_LINKS: bool = os.getenv("ENABLE_SHARE_LINKS", "false").lower() == "true"
    STREAM_LOOPBACK_TOKEN: str = os.getenv("STREAM_LOOPBACK_TOKEN", "")
    LOCAL_API_TOKEN: str = os.getenv("LOCAL_API_TOKEN", "")
    REQUIRE_API_AUTH: bool = os.getenv("REQUIRE_API_AUTH", "false").lower() == "true"
    CORS_ALLOWED_ORIGINS: List[str] = (
        [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
        if os.getenv("CORS_ALLOWED_ORIGINS")
        else []
    )
    ENCRYPTION_ENABLED: bool = os.getenv("ENCRYPTION_ENABLED", "false").lower() == "true"
    VAULT_PASSPHRASE: str = os.getenv("VAULT_PASSPHRASE", "")
    VAULT_SALT: bytes = os.getenv("VAULT_SALT", "vayom_vault_salt_default").encode("utf-8")

    # Server Binding (A-1: Default bind to loopback 127.0.0.1 instead of 0.0.0.0)
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Storage Paths
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))
    DB_PATH: Path = DATA_DIR / "vayom.db"
    UPLOAD_TEMP_DIR: Path = DATA_DIR / "uploads"
    THUMBNAIL_DIR: Path = DATA_DIR / "thumbnails"
    NAS_VAULT_ROOT: Path = Path(os.getenv("NAS_VAULT_ROOT", str(DATA_DIR / "nas_vault")))
    DEFAULT_STORAGE_BACKEND: str = os.getenv("DEFAULT_STORAGE_BACKEND", "telegram")

    # S3 / MinIO / Cloudflare R2 Storage Settings
    S3_ENDPOINT_URL: Optional[str] = os.getenv("S3_ENDPOINT_URL", "")
    S3_ACCESS_KEY_ID: str = os.getenv("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY: str = os.getenv("S3_SECRET_ACCESS_KEY", "")
    S3_BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "")
    S3_REGION_NAME: str = os.getenv("S3_REGION_NAME", "us-east-1")
    S3_VAULT_PREFIX: str = os.getenv("S3_VAULT_PREFIX", "vayom_vault")

    # Nextcloud WebDAV Storage Settings
    NEXTCLOUD_WEBDAV_URL: str = os.getenv("NEXTCLOUD_WEBDAV_URL", "")
    NEXTCLOUD_USERNAME: str = os.getenv("NEXTCLOUD_USERNAME", "")
    NEXTCLOUD_APP_PASSWORD: str = os.getenv("NEXTCLOUD_APP_PASSWORD", "")
    NEXTCLOUD_VAULT_PREFIX: str = os.getenv("NEXTCLOUD_VAULT_PREFIX", "vayom_vault")

    # Pro Licensing
    VAYOM_LICENSE_KEY: str = os.getenv("VAYOM_LICENSE_KEY", "")
    VAYOM_DEV_LICENSE: str = os.getenv("VAYOM_DEV_LICENSE", "")

    # Workstation Security Settings
    WORKSTATION_ALLOWED_ROOTS: List[str] = (
        [r.strip() for r in os.getenv("WORKSTATION_ALLOWED_ROOTS", "").split(",") if r.strip()]
        if os.getenv("WORKSTATION_ALLOWED_ROOTS")
        else [
            str(Path.home()),
            "/media",
            "/run/media",
            "/mnt",
            "/Volumes",
            tempfile.gettempdir(),
        ]
    )

    # File Slicing Limits (2-MiB aligned boundaries: exact multiples of 2 MiB, 512 KiB, and 4096 bytes)
    STANDARD_PART_BYTES: int = 1887436800  # 900 * 2 MiB (~1.887 GB, stays safely under Telegram 2000 MB limit)
    PREMIUM_PART_BYTES: int = 3879731200   # 1850 * 2 MiB (~3.880 GB, stays safely under Telegram 4000 MB limit)
    MAX_PART_BYTES: int = int(os.getenv("MAX_PART_BYTES", str(STANDARD_PART_BYTES)))

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
        self.NAS_VAULT_ROOT.mkdir(parents=True, exist_ok=True)

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
                    self.MAX_PART_BYTES = self.PREMIUM_PART_BYTES  # Aligned 3,879,731,200 bytes for Telegram Premium users!
                else:
                    self.MAX_PART_BYTES = int(os.getenv("MAX_PART_BYTES", str(self.STANDARD_PART_BYTES)))
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
        self.auth_config_file.parent.mkdir(parents=True, exist_ok=True)
        # A-5: create file directly with 0o600 permissions so credentials are never world-readable
        payload = json.dumps(data, indent=2).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(self.auth_config_file), flags, 0o600)
        try:
            with open(fd, "wb", closefd=True) as f:
                f.write(payload)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            raise
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
        # F-2: logout must not leave a premium-inflated slice limit behind
        self.MAX_PART_BYTES = int(os.getenv("MAX_PART_BYTES", str(self.STANDARD_PART_BYTES)))

settings = Settings()
settings.ensure_directories()
settings.load_auth_config()
