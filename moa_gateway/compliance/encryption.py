"""Data Encryption — AES-256-GCM at-rest field-level encryption."""

from __future__ import annotations

import base64
import hashlib
import logging
import os

logger = logging.getLogger(__name__)


class FieldEncryptor:
    """Field-level encryptor — protects sensitive data at rest."""

    def __init__(self, master_key: str | None = None):
        key_material = (master_key or os.getenv("MOA_ENCRYPTION_KEY", "")).encode()  # type: ignore[union-attr]
        if not key_material:
            self._key: bytes | None = None
        else:
            self._key = hashlib.sha256(key_material).digest()  # 32 bytes = AES-256

    @property
    def enabled(self) -> bool:
        return self._key is not None

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a field value, return Base64-encoded ciphertext."""
        if not self.enabled or not plaintext:
            return plaintext

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)  # 96-bit nonce
        aesgcm = AESGCM(self._key)  # type: ignore[arg-type]
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return "ENC:" + base64.b64encode(nonce + ciphertext).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a field value."""
        if not self.enabled or not ciphertext or not ciphertext.startswith("ENC:"):
            return ciphertext

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw = base64.b64decode(ciphertext[4:])
        nonce, ct = raw[:12], raw[12:]
        aesgcm = AESGCM(self._key)  # type: ignore[arg-type]
        return aesgcm.decrypt(nonce, ct, None).decode("utf-8")


# Global encryptor instance
encryptor = FieldEncryptor()
