import os
import secrets
import struct
from typing import Tuple, Dict, Any, Optional
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

def generate_file_nonce() -> str:
    """Generates a fresh random 8-byte hex nonce (16 hex chars) per file."""
    return secrets.token_hex(8)

def build_ctr_counter(nonce_hex: str, aligned_byte_offset: int) -> bytes:
    """
    Computes the 16-byte counter block for AES-256-CTR at any 16-byte aligned file offset.
    Adheres strictly to Invariant 1 from update.md Section 0:
    - Bytes 0..7: 8-byte unique file nonce
    - Bytes 8..15: 64-bit Big-Endian AES block index (aligned_byte_offset // 16)
    """
    if aligned_byte_offset % 16 != 0:
        raise ValueError(f"AES-CTR offset ({aligned_byte_offset}) must be a multiple of 16 bytes.")

    clean_hex = nonce_hex.strip().lower()
    if len(clean_hex) != 16:
        raise ValueError(f"Nonce hex string must be exactly 16 hex characters (8 bytes), got: {nonce_hex}")

    nonce_bytes = bytes.fromhex(clean_hex)
    block_index = aligned_byte_offset // 16
    return nonce_bytes + struct.pack(">Q", block_index)

def derive_key_pbkdf2(
    passphrase: str,
    salt: bytes,
    iterations: int = 100_000,
    key_length: int = 32
) -> bytes:
    """
    Derives a 256-bit AES key from a passphrase using PBKDF2-HMAC-SHA256.
    Follows Web Crypto API subtle.deriveKey parameters.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=key_length,
        salt=salt,
        iterations=iterations,
        backend=default_backend()
    )
    return kdf.derive(passphrase.encode("utf-8"))

def aes_ctr_encrypt(
    plaintext: bytes,
    key: bytes,
    nonce_hex: str,
    aligned_byte_offset: int = 0
) -> bytes:
    """
    Encrypts a chunk with AES-256-CTR starting at aligned_byte_offset.
    Preserves exact 1:1 length: len(plaintext) == len(ciphertext).
    """
    if len(key) != 32:
        raise ValueError(f"AES-256 requires a 32-byte key, got {len(key)} bytes.")
    counter_block = build_ctr_counter(nonce_hex, aligned_byte_offset)
    cipher = Cipher(algorithms.AES(key), modes.CTR(counter_block), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()

def aes_ctr_decrypt(
    ciphertext: bytes,
    key: bytes,
    nonce_hex: str,
    aligned_byte_offset: int = 0
) -> bytes:
    """
    Decrypts an AES-256-CTR chunk starting at aligned_byte_offset.
    In CTR mode, decryption is identical to keystream XOR.
    """
    return aes_ctr_encrypt(ciphertext, key, nonce_hex, aligned_byte_offset)

def aes_gcm_encrypt(
    plaintext: bytes,
    key: bytes,
    associated_data: Optional[bytes] = None
) -> Tuple[bytes, bytes, bytes]:
    """
    Encrypts data with AES-256-GCM.
    Returns (nonce_12_bytes, ciphertext, tag_16_bytes).
    Used for tamper-proof index catalog and metadata payloads.
    """
    if len(key) != 32:
        raise ValueError(f"AES-256-GCM requires a 32-byte key, got {len(key)} bytes.")
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    # aesgcm.encrypt in cryptography appends the 16-byte tag to the ciphertext
    encrypted = aesgcm.encrypt(nonce, plaintext, associated_data)
    ciphertext = encrypted[:-16]
    tag = encrypted[-16:]
    return nonce, ciphertext, tag

def aes_gcm_decrypt(
    ciphertext: bytes,
    key: bytes,
    nonce: bytes,
    tag: bytes,
    associated_data: Optional[bytes] = None
) -> bytes:
    """
    Decrypts and verifies an AES-256-GCM payload.
    """
    if len(key) != 32:
        raise ValueError(f"AES-256-GCM requires a 32-byte key, got {len(key)} bytes.")
    aesgcm = AESGCM(key)
    full_payload = ciphertext + tag
    return aesgcm.decrypt(nonce, full_payload, associated_data)
