import os
import base64
import json
import logging
import time
from typing import Optional, Dict, Any, List
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature

logger = logging.getLogger("vayom.license.crypto")

# Master Public Key (32-byte Ed25519 Raw Public Key in Hex)
# Embedded into Vayom Drive for 100% offline license verification
MASTER_PUBLIC_KEY_HEX = os.environ.get(
    "VAYOM_LICENSE_PUBLIC_KEY",
    "3e057e80901cb033c6f69e97c5308a83ea6114b978ee87b356361626e315cb65"
)


class LicenseError(Exception):
    """Base exception for licensing errors."""
    pass


class InvalidLicenseError(LicenseError):
    """Raised when signature verification fails or key format is malformed."""
    pass


class ExpiredLicenseError(LicenseError):
    """Raised when the license validity period has expired."""
    pass


def _pad_base64(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def verify_license_key(license_key: str, public_key_hex: str = MASTER_PUBLIC_KEY_HEX) -> Dict[str, Any]:
    """
    Verifies an offline cryptographic license key using the embedded Ed25519 public key.
    Format: VAYOM-PRO.<payload_b64>.<signature_b64>
    """
    if not license_key or not isinstance(license_key, str):
        raise InvalidLicenseError("License key cannot be empty.")

    parts = license_key.strip().split(".")
    if len(parts) != 3 or parts[0] != "VAYOM-PRO":
        raise InvalidLicenseError("The provided license key is invalid or unrecognized.")

    payload_b64, sig_b64 = parts[1], parts[2]

    try:
        payload_bytes = base64.urlsafe_b64decode(_pad_base64(payload_b64))
        sig_bytes = base64.urlsafe_b64decode(_pad_base64(sig_b64))
    except Exception:
        raise InvalidLicenseError("The provided license key is invalid or unrecognized.")

    try:
        pub_bytes = bytes.fromhex(public_key_hex)
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_key.verify(sig_bytes, payload_bytes)
    except InvalidSignature:
        raise InvalidLicenseError("The provided license key is invalid or unrecognized.")
    except Exception:
        raise InvalidLicenseError("License verification failed.")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        raise InvalidLicenseError("The provided license key is invalid or unrecognized.")

    # Check expiration if set
    expires_at = payload.get("expires_at")
    if expires_at is not None and time.time() > float(expires_at):
        raise ExpiredLicenseError("This license key has expired.")

    return payload


def issue_license_key(
    issued_to: str,
    tier: str = "pro",
    features: Optional[List[str]] = None,
    expires_at: Optional[int] = None,
    license_id: Optional[str] = None,
    private_key_hex: Optional[str] = None,
) -> str:
    """
    Issues a cryptographically signed license key using the Ed25519 private key.
    Used by the developer to generate keys for paying customers.
    Requires private_key_hex or VAYOM_LICENSE_PRIVATE_KEY environment variable.
    """
    signing_key = private_key_hex or os.environ.get("VAYOM_LICENSE_PRIVATE_KEY")
    if not signing_key:
        raise ValueError(
            "Private signing key not provided. Set VAYOM_LICENSE_PRIVATE_KEY or pass private_key_hex to issue licenses."
        )

    if features is None:
        features = ["nas", "s3", "nextcloud", "multi_storage", "unlimited_vault"]

    payload = {
        "license_id": license_id or f"VPRO-{int(time.time())}-{issued_to[:4].upper()}",
        "tier": tier,
        "features": features,
        "issued_to": issued_to,
        "issued_at": int(time.time()),
        "expires_at": expires_at,
    }

    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")

    priv_bytes = bytes.fromhex(signing_key.strip())
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)
    signature = priv_key.sign(payload_bytes)
    sig_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

    return f"VAYOM-PRO.{payload_b64}.{sig_b64}"
