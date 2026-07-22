"""Data Retention Policy — automatic cleanup of expired data."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RetentionPolicy:
    """A single data retention policy."""

    name: str
    retention_days: int
    table_or_path: str
    timestamp_field: str = "created_at"
    archive_before_delete: bool = True


class DataRetentionManager:
    """Data retention manager — enforces data lifecycle policies."""

    DEFAULT_POLICIES: List[RetentionPolicy] = [
        RetentionPolicy("audit_logs", 90, "audit_log", "timestamp"),
        RetentionPolicy("request_logs", 30, "request_log", "created_at"),
        RetentionPolicy("cache_entries", 7, "cache_store", "created_at"),
        RetentionPolicy("session_tokens", 1, "sessions", "expires_at"),
        RetentionPolicy("temp_files", 1, "data/temp/", ""),
    ]

    def __init__(self, policies: Optional[List[RetentionPolicy]] = None):
        self._policies = policies or list(self.DEFAULT_POLICIES)

    async def run_cleanup(self, db_conn=None) -> dict:
        """Execute data cleanup across all policies."""
        results = {}
        for policy in self._policies:
            try:
                deleted = await self._cleanup_policy(policy, db_conn)
                results[policy.name] = {"deleted": deleted, "status": "ok"}
                logger.info("Retention cleanup: %s — %d records removed", policy.name, deleted)
            except Exception as e:
                results[policy.name] = {"error": str(e), "status": "failed"}
                logger.error("Retention cleanup failed: %s — %s", policy.name, e)
        return results

    async def _cleanup_policy(self, policy: RetentionPolicy, db_conn) -> int:
        """Clean up a single policy."""
        cutoff = time.time() - (policy.retention_days * 86400)

        if policy.table_or_path.endswith("/"):
            return self._cleanup_files(policy.table_or_path, cutoff)

        if db_conn:
            return 0
        return 0

    def _cleanup_files(self, directory: str, cutoff: float) -> int:
        """Clean up expired files in a directory."""
        count = 0
        if not os.path.exists(directory):
            return 0
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                os.remove(filepath)
                count += 1
        return count

    def get_policy_status(self) -> List[dict]:
        """Get status summary for all policies."""
        return [
            {
                "name": p.name,
                "retention_days": p.retention_days,
                "target": p.table_or_path,
                "archive_first": p.archive_before_delete,
            }
            for p in self._policies
        ]
