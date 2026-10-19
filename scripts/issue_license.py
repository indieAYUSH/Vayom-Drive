#!/usr/bin/env python3
"""
Offline License Issuer CLI for Vayom Drive.
Used exclusively by the software vendor to sign and issue Ed25519 license keys.
Usage:
    python scripts/issue_license.py --user "Customer Name" --tier pro --days 365
"""
import sys
import os
import argparse
import time
from pathlib import Path

# Ensure repo root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.license.crypto import issue_license_key, MASTER_PUBLIC_KEY_HEX, verify_license_key

def main():
    parser = argparse.ArgumentParser(description="Issue cryptographically signed Vayom Drive Pro license keys.")
    parser.add_argument("--user", required=True, help="Customer name or email address.")
    parser.add_argument("--tier", default="pro", choices=["pro", "enterprise"], help="License tier.")
    parser.add_argument("--days", type=int, default=None, help="License validity in days (None for lifetime).")
    parser.add_argument("--key", default=None, help="Ed25519 private key in hex. Defaults to VAYOM_LICENSE_PRIVATE_KEY env.")

    args = parser.parse_args()

    private_key_hex = args.key or os.environ.get("VAYOM_LICENSE_PRIVATE_KEY")
    if not private_key_hex:
        print("ERROR: Private signing key required. Pass --key <HEX> or set VAYOM_LICENSE_PRIVATE_KEY.", file=sys.stderr)
        sys.exit(1)

    expires_at = int(time.time() + args.days * 86400) if args.days else None

    license_str = issue_license_key(
        issued_to=args.user,
        tier=args.tier,
        expires_at=expires_at,
        private_key_hex=private_key_hex
    )

    # Self-verify
    payload = verify_license_key(license_str, public_key_hex=MASTER_PUBLIC_KEY_HEX)

    print("\n=======================================================")
    print("           VAYOM DRIVE SIGNED LICENSE KEY             ")
    print("=======================================================")
    print(f"Issued To  : {payload['issued_to']}")
    print(f"Tier       : {payload['tier'].upper()}")
    print(f"Expires At : {time.ctime(expires_at) if expires_at else 'LIFETIME'}")
    print(f"License ID : {payload['license_id']}")
    print("-------------------------------------------------------")
    print(license_str)
    print("=======================================================\n")

if __name__ == "__main__":
    main()
