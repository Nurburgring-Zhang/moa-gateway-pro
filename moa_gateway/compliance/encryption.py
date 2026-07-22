"""Data Encryption — AES-256-GCM at-rest field-level encryption."""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional


class FieldEncryptor:
    """Field-level encryptor — protects sensitive data at rest."""

    def __init__(self, master_key: Optional[str] = None):
        key_material = (master_key or os.getenv("MOA_ENCRYPTION_KEY", "")).encode()
        if not key_material:
            self._key: Optional[bytes] = None
        else:
            self._key = hashlib.sha256(key_material).digest()  # 32 bytes = AES-256

    @property
    def enabled(self) -> bool:
        return self._key is not None

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a field value, return Base64-encoded ciphertext."""
        if not self.enabled or not plaintext:
            return plaintext

        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            nonce = os.urandom(12)  # 96-bit nonce
            aesgcm = AESGCM(self._key)
            ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
            # Format: ENC:base64(nonce + ciphertext)
            return "ENC:" + base64.b64encode(nonce + ciphertext).decode()
        except ImportError:
            return self._simple_encrypt(plaintext)

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a field value."""
        if not self.enabled or not ciphertext or not ciphertext.startswith("ENC:"):
            return ciphertext

        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            raw = base64.b64decode(ciphertext[4:])
            nonce, ct = raw[:12], raw[12:]
            aesgcm = AESGCM(self._key)
            return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
        except ImportError:
            return self._simple_decrypt(ciphertext)

    def _simple_encrypt(self, text: str) -> str:
        """Fallback XOR encryption when cryptography is unavailable."""
        assert self._key is not None
        key_bytes = self._key
        encrypted = bytes(b ^ key_bytes[i % 32] for i, b in enumerate(text.encode("utf-8")))
        return "ENC:" + base64.b64encode(encrypted).decode()

    def _simple_decrypt(self, text: str) -> str:
        """Fallback XOR decryption."""
        assert self._key is not None
        key_bytes = self._key
        raw = base64.b64decode(text[4:])
        decrypted = bytes(b ^ key_bytes[i % 32] for i, b in enumerate(raw))
        return decrypted.decode("utf-8")


# Global encryptor instance
encryptor = FieldEncryptor()
