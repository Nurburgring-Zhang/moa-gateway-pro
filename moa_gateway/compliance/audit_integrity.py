"""Audit Log Integrity — HMAC signature chain for tamper detection."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Optional


class AuditIntegrity:
    """Audit log tamper protection using HMAC chain."""

    def __init__(self, signing_key: Optional[str] = None):
        key = signing_key or os.getenv("MOA_AUDIT_SIGNING_KEY", "audit-default-key")
        self._key = key.encode()
        self._last_hash: Optional[str] = None  # Hash of previous entry

    def sign_entry(self, entry: dict) -> dict:
        """Add integrity signature to an audit entry."""
        entry["_seq"] = int(time.time() * 1000000)  # Microsecond sequence
        entry["_prev_hash"] = self._last_hash or "GENESIS"

        # Compute hash of current entry
        payload = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        current_hash = hmac.new(
            self._key, payload.encode(), hashlib.sha256
        ).hexdigest()

        entry["_hash"] = current_hash
        self._last_hash = current_hash
        return entry

    def verify_entry(self, entry: dict, prev_hash: Optional[str] = None) -> bool:
        """Verify audit entry integrity."""
        entry_copy = dict(entry)
        stored_hash = entry_copy.pop("_hash", None)
        if not stored_hash:
            return False

        # Verify chain link
        if prev_hash is not None and entry_copy.get("_prev_hash") != prev_hash:
            return False

        payload = json.dumps(entry_copy, sort_keys=True, ensure_ascii=False)
        expected_hash = hmac.new(
            self._key, payload.encode(), hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(stored_hash, expected_hash)

    def verify_chain(self, entries: list) -> tuple[bool, int, int]:
        """Verify audit log chain integrity.

        Returns (is_valid, valid_count, total_count).
        """
        prev_hash: Optional[str] = None
        valid_count = 0

        for entry in entries:
            if self.verify_entry(dict(entry), prev_hash):
                valid_count += 1
                prev_hash = entry["_hash"]
            else:
                return (False, valid_count, len(entries))

        return (True, valid_count, len(entries))
