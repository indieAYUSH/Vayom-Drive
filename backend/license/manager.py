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
        Returns the active license status:
        {
            "is_pro": bool,
            "tier": "pro" | "free",
            "features": list[str],
            "issued_to": str | None,
            "expires_at": int | None,
            "license_id": str | None,
        }
        """
        # 1. Developer / Test Override
        dev_override = getattr(settings, "VAYOM_DEV_LICENSE", "").strip().lower()
        if dev_override == "pro":
            return {
                "is_pro": True,
                "tier": "pro",
                "features": ["nas", "s3", "nextcloud", "multi_storage", "unlimited_vault"],
                "issued_to": "Developer Environment",
                "expires_at": None,
                "license_id": "VPRO-DEV-MODE",
            }

        # 2. In-memory cached license verification (A-8: re-check expiry on every call)
        if self._cached_license is not None:
            expires_at = self._cached_license.get("expires_at")
            if expires_at is None or time.time() <= float(expires_at):
                return self._cached_license
            logger.warning("Cached Pro license expired — falling back to free tier.")
            self._cached_license = None

        # 3. Check environment configuration
        env_key = getattr(settings, "VAYOM_LICENSE_KEY", "").strip()
        if env_key:
            try:
                payload = verify_license_key(env_key)
                self._cached_license = {
                    "is_pro": True,
                    "tier": payload.get("tier", "pro"),
                    "features": payload.get("features", []),
                    "issued_to": payload.get("issued_to"),
                    "expires_at": payload.get("expires_at"),
                    "license_id": payload.get("license_id"),
                }
                return self._cached_license
            except Exception as e:
                logger.warning(f"Invalid license key in environment: {e}")

        # 4. Check database system_settings
        try:
            async with get_db() as db:
                cur = await db.execute("SELECT value FROM system_settings WHERE key = 'license_key'")
                row = await cur.fetchone()
                if row and row[0]:
                    db_key = row[0].strip()
                    payload = verify_license_key(db_key)
                    self._cached_license = {
                        "is_pro": True,
                        "tier": payload.get("tier", "pro"),
                        "features": payload.get("features", []),
                        "issued_to": payload.get("issued_to"),
                        "expires_at": payload.get("expires_at"),
                        "license_id": payload.get("license_id"),
                    }
                    return self._cached_license
        except Exception as e:
            logger.debug(f"Could not read license from database: {e}")

        # Default: Free tier
        return {
            "is_pro": False,
            "tier": "free",
            "features": ["telegram"],
            "issued_to": None,
            "expires_at": None,
            "license_id": None,
        }

    async def activate_license(self, license_key: str) -> Dict[str, Any]:
        """
        Verifies and activates a new cryptographic license key, persisting it to SQLite.
        """
        payload = verify_license_key(license_key)

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO system_settings (key, value, updated_at)
                VALUES ('license_key', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (license_key.strip(),)
            )
            await db.commit()

        self._cached_license = {
            "is_pro": True,
            "tier": payload.get("tier", "pro"),
            "features": payload.get("features", []),
            "issued_to": payload.get("issued_to"),
            "expires_at": payload.get("expires_at"),
            "license_id": payload.get("license_id"),
        }
        return self._cached_license

    async def is_backend_allowed(self, backend: Optional[str]) -> bool:
        """
        Checks whether the specified storage backend is allowed under current license tier.
        - Telegram is always allowed for all users.
        - NAS, S3, and Nextcloud require an active Pro license.
        """
        b = (backend or "telegram").strip().lower()
        if b == "telegram":
            return True

        status = await self.get_license_status()
        if not status["is_pro"]:
            return False

        features: List[str] = status.get("features", [])
        return b in features or "multi_storage" in features


# Global singleton instance
license_manager = LicenseManager()
