"""Key Rotation Management — seamless key versioning."""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class KeyVersion:
    """A single key version record."""

    key_id: str
    key_value: str
    created_at: float
    expires_at: Optional[float]
    is_primary: bool

    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "is_primary": self.is_primary,
        }


class KeyRotationManager:
    """Key rotation manager — supports dual-key transition period."""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = storage_path or os.getenv(
            "MOA_KEY_STORE", "data/key_versions.json"
        )
        self._keys: List[KeyVersion] = []
        self._rotation_interval = int(os.getenv("MOA_KEY_ROTATION_DAYS", "90")) * 86400

    def generate_key(self, purpose: str = "api") -> KeyVersion:
        """Generate a new key version."""
        key = KeyVersion(
            key_id=f"{purpose}-{secrets.token_hex(8)}",
            key_value=secrets.token_urlsafe(32),
            created_at=time.time(),
            expires_at=time.time() + self._rotation_interval,
            is_primary=True,
        )
        # Demote old primary
        for k in self._keys:
            if k.is_primary:
                k.is_primary = False

        self._keys.append(key)
        return key

    def get_primary_key(self) -> Optional[KeyVersion]:
        """Get current primary key."""
        for k in self._keys:
            if k.is_primary and (not k.expires_at or k.expires_at > time.time()):
                return k
        return None

    def get_all_valid_keys(self) -> List[KeyVersion]:
        """Get all valid keys (including grace period)."""
        now = time.time()
        grace_period = 86400  # 24-hour grace
        return [
            k
            for k in self._keys
            if not k.expires_at or k.expires_at + grace_period > now
        ]

    def should_rotate(self) -> bool:
        """Check if key rotation is needed."""
        primary = self.get_primary_key()
        if not primary:
            return True
        remaining = (primary.expires_at or float("inf")) - time.time()
        return remaining < 7 * 86400  # Suggest rotation 7 days before expiry

    def cleanup_expired(self) -> int:
        """Remove expired keys past grace period."""
        now = time.time()
        grace = 7 * 86400  # 7-day grace
        before = len(self._keys)
        self._keys = [
            k for k in self._keys if not k.expires_at or k.expires_at + grace > now
        ]
        return before - len(self._keys)

    def get_status(self) -> dict:
        """Get key rotation status summary."""
        primary = self.get_primary_key()
        return {
            "total_keys": len(self._keys),
            "valid_keys": len(self.get_all_valid_keys()),
            "primary_key_id": primary.key_id if primary else None,
            "primary_expires_at": primary.expires_at if primary else None,
            "rotation_needed": self.should_rotate(),
            "rotation_interval_days": self._rotation_interval // 86400,
        }
