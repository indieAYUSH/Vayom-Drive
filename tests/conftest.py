import asyncio
import os
import pytest
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

# Ephemeral test-only signing keypair. The production vendor private key must
# never be stored in the repository, test suite, or CI configuration.
_TEST_SIGNING_KEY = ed25519.Ed25519PrivateKey.generate()
os.environ["VAYOM_LICENSE_PRIVATE_KEY"] = _TEST_SIGNING_KEY.private_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PrivateFormat.Raw,
    encryption_algorithm=serialization.NoEncryption(),
).hex()
os.environ["VAYOM_LICENSE_PUBLIC_KEY"] = _TEST_SIGNING_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
).hex()

from backend.config import settings
from backend.db.connection import init_db

@pytest.fixture(autouse=True)
def isolate_test_db(monkeypatch, tmp_path):
    """
    Isolates all test runs to a disposable temporary SQLite database.
    Prevents tests from clearing or mutating data/vayom.db.
    """
    data_dir = tmp_path / "vayom_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    test_db = data_dir / "test_vayom.db"
    
    monkeypatch.setattr(settings, "DATA_DIR", data_dir)
    monkeypatch.setattr(settings, "DB_PATH", test_db)
    monkeypatch.setattr(settings, "UPLOAD_TEMP_DIR", data_dir / "uploads")
    monkeypatch.setattr(settings, "THUMBNAIL_DIR", data_dir / "thumbnails")
    monkeypatch.setattr(settings, "AUTH_MODE", "bot")
    # Most unit tests intentionally exercise the storage client without a live
    # Telegram session. Production keeps this false, so upload routes reject
    # mock-backed "successes"; the explicit regression test for that behavior
    # overrides this fixture back to false.
    monkeypatch.setattr(settings, "ALLOW_OFFLINE_MOCK_UPLOADS", True)
    monkeypatch.setattr(settings, "TELEGRAM_CHANNEL_ID", "-100123456789")
    monkeypatch.setattr(settings, "TELEGRAM_BACKUP_CHANNEL_ID", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "mock_bot_token")

    settings.ensure_directories()
    from backend.storage.client import storage_client
    storage_client.last_pinned_catalog_msg_id = None
    storage_client.last_backup_catalog_msg_id = None
    storage_client.last_saved_catalog_msg_id = None

    asyncio.run(init_db())
    yield


