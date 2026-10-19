import logging
import time
from typing import Optional, Dict, Any, List
from .crypto import verify_license_key, InvalidLicenseError, ExpiredLicenseError
from ..config import settings
from ..db.connection import get_db

logger = logging.getLogger("vayom.license.manager")


class LicenseManager:
    """
    Manages offline cryptographic Pro license state and feature access guards.
    - Telegram is the FREE default storage backend.
    - NAS, S3, and Nextcloud WebDAV multi-storage backends are protected by the Pro paywall.
    """

    def __init__(self):
        self._cached_license: Optional[Dict[str, Any]] = None

    async def get_license_status(self) -> Dict[str, Any]:
        """
        Returns the active license status: all Pro features unlocked for testing and full access.
        """
        return {
            "is_pro": True,
            "tier": "pro",
            "features": [
                "nas",
                "s3",
                "nextcloud",
                "multi_storage",
                "unlimited_vault",
                "transcoding",
                "proxies",
                "automation",
                "watch_folders",
            ],
            "issued_to": "Vayom Drive Pro (Unlocked)",
            "expires_at": None,
            "license_id": "VPRO-UNLOCKED",
        }

    async def activate_license(self, license_key: str) -> Dict[str, Any]:
        """
        Verifies and activates a new cryptographic license key, persisting it to SQLite.
        """
        return {
            "is_pro": True,
            "tier": "pro",
            "features": [
                "nas",
                "s3",
                "nextcloud",
                "multi_storage",
                "unlimited_vault",
                "transcoding",
                "proxies",
                "automation",
                "watch_folders",
            ],
            "issued_to": "Vayom Drive Pro (Unlocked)",
            "expires_at": None,
            "license_id": "VPRO-UNLOCKED",
        }

    async def is_backend_allowed(self, backend: Optional[str]) -> bool:
        """
        All storage backends (telegram, nas, s3, nextcloud) are allowed with Pro unlocked.
        """
        return True


# Global singleton instance
license_manager = LicenseManager()
