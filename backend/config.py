import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

class Settings:
    ROOT_DIR: Path = ROOT_DIR

    # Telegram Base Application Credentials (for MTProto handshake)
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHANNEL_ID: str = ""

    # Security & Admin
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "vayom123")
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "vayom_drive_secret_key_change_in_prod")
    ENCRYPTION_ENABLED: bool = os.getenv("ENCRYPTION_ENABLED", "false").lower() == "true"

    # Server Binding
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Storage Paths
    DATA_DIR: Path = ROOT_DIR / os.getenv("DATA_DIR", "data")
    DB_PATH: Path = DATA_DIR / "vayom.db"
    UPLOAD_TEMP_DIR: Path = DATA_DIR / "uploads"
    THUMBNAIL_DIR: Path = DATA_DIR / "thumbnails"

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

    @property
    def auth_config_file(self) -> Path:
        return self.DATA_DIR / "auth_config.json"

    def load_auth_config(self):
        """Loads runtime authentication state from data/auth_config.json or falls back to .env."""
        import json
        if self.auth_config_file.exists():
            try:
                data = json.loads(self.auth_config_file.read_text(encoding="utf-8"))
                self.AUTH_MODE = data.get("auth_mode", "")
                self.TELEGRAM_API_ID = int(data.get("api_id") or self.TELEGRAM_API_ID)
                self.TELEGRAM_API_HASH = data.get("api_hash") or self.TELEGRAM_API_HASH
                self.TELEGRAM_BOT_TOKEN = data.get("bot_token") or ""
                self.TELEGRAM_CHANNEL_ID = str(data.get("channel_id") or "")
                self.USER_PROFILE = data.get("user_profile") or {}
                self.SESSION_NAME = data.get("session_name") or ("vayom_user" if self.AUTH_MODE == "qr" else "vayom_bot")
                if self.USER_PROFILE.get("is_premium"):
                    self.MAX_PART_BYTES = 3900000000  # 4 GB chunks for Telegram Premium users!
                return
            except Exception as e:
                pass

        # Strictly require explicit login! No auto-login from .env
        self.AUTH_MODE = ""
        self.USER_PROFILE = {}
        self.TELEGRAM_BOT_TOKEN = ""
        self.TELEGRAM_CHANNEL_ID = ""
        self.SESSION_NAME = "vayom_bot"

    def save_auth_config(self, data: dict):
        """Persists runtime authentication configuration to data/auth_config.json."""
        import json
        self.auth_config_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
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
        self.SESSION_NAME = "vayom_bot"

settings = Settings()
settings.ensure_directories()
settings.load_auth_config()
