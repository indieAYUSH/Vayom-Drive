import os
import io
import time
import json
import hashlib
import secrets
import struct
from pathlib import Path
from typing import Tuple, Dict, Any, Optional, Union
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
    Seamlessly handles both 16-byte aligned and unaligned offsets.
    """
    if len(key) != 32:
        raise ValueError(f"AES-256 requires a 32-byte key, got {len(key)} bytes.")
    if not plaintext:
        return b""
    offset_within_block = aligned_byte_offset % 16
    aligned_offset = aligned_byte_offset - offset_within_block

    counter_block = build_ctr_counter(nonce_hex, aligned_offset)
    cipher = Cipher(algorithms.AES(key), modes.CTR(counter_block), backend=default_backend())
    encryptor = cipher.encryptor()

    if offset_within_block == 0:
        return encryptor.update(plaintext) + encryptor.finalize()
    else:
        dummy = b"\x00" * offset_within_block + plaintext
        out = encryptor.update(dummy) + encryptor.finalize()
        return out[offset_within_block:]

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


class EncryptedFileSlice(io.IOBase):
    """
    Zero-copy virtual encrypted slice of a file on local disk or an underlying slice.
    Yields AES-256-CTR encrypted bytes on read(), preserving exact 1:1 length:
    len(plaintext) == len(ciphertext).
    Ensures zero disk allocation during upload of multi-gigabyte files.
    """
    def __init__(
        self,
        source: Union[str, Path, Any],
        key: bytes,
        nonce_hex: str,
        byte_offset: int = 0,
        slice_size: Optional[int] = None,
        name: Optional[str] = None
    ):
        if len(key) != 32:
            raise ValueError(f"AES-256 requires a 32-byte key, got {len(key)} bytes.")
        self.key = key
        self.nonce_hex = nonce_hex.strip().lower()
        self.byte_offset = byte_offset
        self._source = source
        self._file: Optional[Any] = None
        self._is_underlying_slice = hasattr(source, "read")

        if self._is_underlying_slice:
            self.slice_size = (
                slice_size
                if slice_size is not None
                else getattr(source, "slice_size", getattr(source, "size", len(source)))
            )
            self._name = name or getattr(source, "name", "encrypted.part")
            if hasattr(source, "byte_offset") and byte_offset == 0:
                self.byte_offset = source.byte_offset
        else:
            path = Path(source).resolve()
            total_file_size = os.path.getsize(path)
            self.slice_size = slice_size if slice_size is not None else (total_file_size - byte_offset)
            self._name = name or f"{path.stem}.part_{byte_offset // max(1, self.slice_size)}{path.suffix}"
            self._is_standalone_chunk = total_file_size < (self.byte_offset + self.slice_size)

        self._pos = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def size(self) -> int:
        return self.slice_size

    def __len__(self) -> int:
        return self.slice_size

    @property
    def mode(self) -> str:
        return "rb"

    def open(self):
        if self._is_underlying_slice:
            if hasattr(self._source, "open"):
                self._source.open()
            if hasattr(self._source, "seek"):
                self._source.seek(self._pos)
        else:
            if self._file is None or self._file.closed:
                self._file = open(self._source, "rb")
                file_target = self._pos if self._is_standalone_chunk else (self.byte_offset + self._pos)
                self._file.seek(file_target)
        return self

    def close(self):
        if self._is_underlying_slice:
            if hasattr(self._source, "close"):
                self._source.close()
        else:
            if self._file and not self._file.closed:
                self._file.close()
                self._file = None

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def read(self, size: int = -1) -> bytes:
        if self._pos >= self.slice_size:
            return b""

        if size < 0:
            to_read = self.slice_size - self._pos
        else:
            to_read = min(size, self.slice_size - self._pos)

        if to_read <= 0:
            return b""

        # Read plaintext bytes from underlying source
        if self._is_underlying_slice:
            if hasattr(self._source, "seek") and hasattr(self._source, "tell"):
                if self._source.tell() != self._pos:
                    self._source.seek(self._pos)
            plaintext = self._source.read(to_read)
        else:
            if self._file is None or self._file.closed:
                self.open()
            file_target = self._pos if self._is_standalone_chunk else (self.byte_offset + self._pos)
            if self._file.tell() != file_target:
                self._file.seek(file_target)
            plaintext = self._file.read(to_read)

        if not plaintext:
            return b""

        # Compute absolute offset for AES-CTR keystream
        abs_pos = self.byte_offset + self._pos
        aligned_offset = (abs_pos // 16) * 16
        offset_within = abs_pos % 16

        # Encrypt plaintext with AES-256-CTR
        if offset_within == 0:
            ciphertext = aes_ctr_encrypt(plaintext, self.key, self.nonce_hex, aligned_offset)
        else:
            dummy = b"\x00" * offset_within + plaintext
            full_cipher = aes_ctr_encrypt(dummy, self.key, self.nonce_hex, aligned_offset)
            ciphertext = full_cipher[offset_within:]

        self._pos += len(plaintext)
        return ciphertext

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            new_pos = offset
        elif whence == os.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == os.SEEK_END:
            new_pos = self.slice_size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        self._pos = max(0, min(new_pos, self.slice_size))
        if self._is_underlying_slice:
            if hasattr(self._source, "seek"):
                self._source.seek(self._pos)
        else:
            if self._file and not self._file.closed:
                self._file.seek(self.byte_offset + self._pos)
        return self._pos

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False


def compute_slice_sha256(slice_reader: Any) -> str:
    """Computes SHA-256 over an open file-like or slice reader and resets position to 0."""
    h = hashlib.sha256()
    if hasattr(slice_reader, "seek"):
        slice_reader.seek(0)
    while chunk := slice_reader.read(1024 * 1024):
        h.update(chunk)
    if hasattr(slice_reader, "seek"):
        slice_reader.seek(0)
    return h.hexdigest()


class VaultKeyManager:
    """
    Manages active vault encryption key in memory and local 0600 storage.
    Enables background workstation watch folders to encrypt automatically on startup
    while providing zero-knowledge privacy with respect to Telegram cloud storage.
    """
    def __init__(self):
        self._active_key: Optional[bytes] = None

    def _config_path(self) -> Path:
        from ..config import settings
        return settings.DATA_DIR / "vault_config.json"

    def _key_path(self) -> Path:
        from ..config import settings
        return settings.DATA_DIR / ".vault_key"

    def load_stored_key(self) -> bool:
        """Loads persistently saved 0600 key on node startup if available."""
        try:
            kp = self._key_path()
            if kp.exists():
                raw = kp.read_text(encoding="utf-8").strip()
                if len(raw) == 64:
                    self._active_key = bytes.fromhex(raw)
                    return True
        except Exception:
            pass
        return False

    def init_vault(self, passphrase: str, remember_device: bool = True) -> Dict[str, Any]:
        """Initializes a new vault with passphrase, generates salt and exportable recovery key."""
        salt = os.urandom(16)
        key = derive_key_pbkdf2(passphrase, salt)
        verify_hash = hashlib.sha256(key + b":vayom_verify_v1").hexdigest()
        recovery_key = key.hex()

        from ..config import settings
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config_data = {
            "version": "1.0",
            "salt": salt.hex(),
            "verify_hash": verify_hash,
            "created_at": int(time.time())
        }
        self._config_path().write_text(json.dumps(config_data, indent=2), encoding="utf-8")

        self._active_key = key
        if remember_device:
            self._save_key_file(recovery_key)

        return {
            "unlocked": True,
            "recovery_key": recovery_key,
            "salt": salt.hex()
        }

    def _save_key_file(self, hex_key: str):
        kp = self._key_path()
        kp.write_text(hex_key, encoding="utf-8")
        try:
            os.chmod(kp, 0o600)
        except Exception:
            pass

    def unlock(
        self,
        passphrase: Optional[str] = None,
        recovery_key: Optional[str] = None,
        remember_device: bool = True
    ) -> bool:
        """Unlocks vault via master passphrase or 64-hex recovery key."""
        cp = self._config_path()
        if cp.exists():
            cfg = json.loads(cp.read_text(encoding="utf-8"))
            salt = bytes.fromhex(cfg["salt"])
            expected_hash = cfg["verify_hash"]

            if recovery_key:
                clean_rk = recovery_key.strip()
                if len(clean_rk) != 64:
                    raise ValueError("Recovery key must be 64 hexadecimal characters (32 bytes).")
                candidate_key = bytes.fromhex(clean_rk)
            elif passphrase:
                candidate_key = derive_key_pbkdf2(passphrase, salt)
            else:
                raise ValueError("Must provide either passphrase or recovery key.")

            cand_hash = hashlib.sha256(candidate_key + b":vayom_verify_v1").hexdigest()
            if cand_hash != expected_hash:
                raise ValueError("Incorrect passphrase or recovery key.")

            self._active_key = candidate_key
            if remember_device:
                self._save_key_file(candidate_key.hex())
            return True
        else:
            # Fallback for unconfigured vaults: derive key with default salt
            from ..config import settings
            if recovery_key:
                clean_rk = recovery_key.strip()
                if len(clean_rk) != 64:
                    raise ValueError("Key must be 64 hexadecimal characters.")
                self._active_key = bytes.fromhex(clean_rk)
            elif passphrase:
                salt = getattr(settings, "VAULT_SALT", b"vayom_vault_salt_default")
                self._active_key = derive_key_pbkdf2(passphrase, salt)
            else:
                raise ValueError("Must provide passphrase or recovery key.")

            if remember_device and self._active_key:
                self._save_key_file(self._active_key.hex())
            return True

    def set_key(self, key: bytes):
        if len(key) != 32:
            raise ValueError(f"Vault key must be 32 bytes, got {len(key)}")
        self._active_key = key

    def get_key(self) -> Optional[bytes]:
        if self._active_key:
            return self._active_key
        # Check local 0600 storage file
        if self.load_stored_key():
            return self._active_key
        try:
            from ..config import settings
            if getattr(settings, "VAULT_PASSPHRASE", None):
                salt = getattr(settings, "VAULT_SALT", b"vayom_vault_salt_default")
                return derive_key_pbkdf2(settings.VAULT_PASSPHRASE, salt)
        except Exception:
            pass
        return None

    def is_unlocked(self) -> bool:
        return self.get_key() is not None

    def clear_key(self):
        """Clears key from memory and removes 0600 key file if present."""
        self._active_key = None
        try:
            kp = self._key_path()
            if kp.exists():
                kp.unlink()
        except Exception:
            pass

    def get_status(self) -> Dict[str, Any]:
        from ..config import settings
        return {
            "is_initialized": self._config_path().exists(),
            "unlocked": self.is_unlocked(),
            "remember_device": self._key_path().exists(),
            "encryption_enabled": getattr(settings, "ENCRYPTION_ENABLED", False)
        }


vault_manager = VaultKeyManager()

