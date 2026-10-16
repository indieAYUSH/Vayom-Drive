import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

class Settings:
    ROOT_DIR: Path = ROOT_DIR

    # Telegram Credentials
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "")

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

    def ensure_directories(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.UPLOAD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_directories()
