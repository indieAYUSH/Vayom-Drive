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
MASTER_PUBLIC_KEY_HEX = "3e057e80901cb033c6f69e97c5308a83ea6114b978ee87b356361626e315cb65"

# Creator Private Key for generating signed keys (used by developer or license issuer)
DEFAULT_PRIVATE_KEY_HEX = "***REMOVED***"


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
        raise InvalidLicenseError("License key is empty or not a string")

    parts = license_key.strip().split(".")
    if len(parts) != 3 or parts[0] != "VAYOM-PRO":
        raise InvalidLicenseError("Malformed license key format (must start with VAYOM-PRO.<payload>.<sig>)")

    payload_b64, sig_b64 = parts[1], parts[2]

    try:
        payload_bytes = base64.urlsafe_b64decode(_pad_base64(payload_b64))
        sig_bytes = base64.urlsafe_b64decode(_pad_base64(sig_b64))
    except Exception as e:
        raise InvalidLicenseError(f"Failed to decode base64 license data: {e}")

    try:
        pub_bytes = bytes.fromhex(public_key_hex)
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_key.verify(sig_bytes, payload_bytes)
    except InvalidSignature:
        raise InvalidLicenseError("Cryptographic signature verification failed; invalid or forged license key")
    except Exception as e:
        raise InvalidLicenseError(f"Public key verification error: {e}")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:
        raise InvalidLicenseError(f"Invalid JSON payload: {e}")

    # Check expiration if set
    expires_at = payload.get("expires_at")
    if expires_at is not None and time.time() > float(expires_at):
        raise ExpiredLicenseError(f"License expired at {expires_at}")

    return payload


def issue_license_key(
    issued_to: str,
    tier: str = "pro",
    features: Optional[List[str]] = None,
    expires_at: Optional[int] = None,
    license_id: Optional[str] = None,
    private_key_hex: str = DEFAULT_PRIVATE_KEY_HEX,
) -> str:
    """
    Issues a cryptographically signed license key using the Ed25519 private key.
    Used by the developer to generate keys for paying customers.
    """
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

    priv_bytes = bytes.fromhex(private_key_hex)
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)
    signature = priv_key.sign(payload_bytes)
    sig_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

    return f"VAYOM-PRO.{payload_b64}.{sig_b64}"
