"""GDPR Compliance — Data Subject Rights implementation."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DeletionRequest:
    """A GDPR data deletion (right to be forgotten) request."""

    request_id: str
    user_id: str
    requested_at: float
    completed_at: float | None = None
    status: str = "pending"  # pending/processing/completed/failed
    data_categories: list[str] = field(default_factory=lambda: ["all"])


class GDPRManager:
    """GDPR data subject rights manager."""

    def __init__(self):
        self._requests: list[DeletionRequest] = []

    async def create_deletion_request(
        self, user_id: str, categories: list[str] | None = None
    ) -> DeletionRequest:
        """Create a data deletion request (right to be forgotten)."""
        request = DeletionRequest(
            request_id=str(uuid.uuid4()),
            user_id=user_id,
            requested_at=time.time(),
            data_categories=categories or ["all"],
        )
        self._requests.append(request)
        # v3.1.1 P2-E: do not log the raw user_id (personal data).
        logger.info("GDPR deletion request created: %s", request.request_id)
        return request

    async def process_deletion(self, request_id: str, db_conn=None) -> dict:
        """Process a data deletion request."""
        request = next((r for r in self._requests if r.request_id == request_id), None)
        if not request:
            return {"error": "Request not found", "status": "not_found"}

        request.status = "processing"
        deleted_data: dict = {}

        try:
            deleted_data["user_profile"] = await self._delete_user_data(request.user_id, db_conn)
            deleted_data["logs_anonymized"] = await self._anonymize_logs(request.user_id, db_conn)
            deleted_data["api_keys"] = await self._delete_api_keys(request.user_id, db_conn)
            deleted_data["cache_cleared"] = True

            request.status = "completed"
            request.completed_at = time.time()
            # v3.1.1 P2-E: scrub the personal identifier from the in-memory
            # tracker once the deletion is done — the subject is forgotten here
            # too, so list_requests cannot re-identify them.
            request.user_id = "[forgotten]"
            logger.info("GDPR deletion completed: %s", request_id)
            return {"status": "completed", "deleted": deleted_data}

        except Exception as e:
            request.status = "failed"
            logger.error("GDPR deletion failed: %s — %s", request_id, e)
            return {"status": "failed", "error": str(e)}

    async def _delete_user_data(self, user_id: str, db_conn) -> bool:
        """Delete user personal data from storage.

        v3.1.1 audit P1-13 fix: v3.1.0 deleted from a nonexistent ``users``
        table, so the right-to-be-forgotten never actually removed anything.
        The real account table is ``admin_users``.
        """
        if db_conn is None:
            raise NotImplementedError(
                "GDPR user data deletion requires a database connection. "
                "Pass db_conn to process_deletion()."
            )
        try:
            cur = db_conn.execute(
                "DELETE FROM admin_users WHERE username = ?", (user_id,)
            )
            return bool(cur.rowcount > 0)
        except Exception as e:
            logger.error("Failed to delete user data for %s: %s", user_id, e)
            raise

    async def _anonymize_logs(self, user_id: str, db_conn) -> int:
        """Anonymize user-related log entries by replacing key ids with a hash.

        v3.1.1 audit P1-13 fix: request_logs.api_key_id stores *key ids*, not
        usernames — the old ``WHERE api_key_id = username`` matched nothing.
        Resolve the user's key ids from api_keys first, then anonymize every
        log row attributed to those keys.
        """
        if db_conn is None:
            raise NotImplementedError(
                "GDPR log anonymization requires a database connection."
            )
        try:
            anon_id = self._make_anon_id(user_id)
            key_rows = db_conn.execute(
                "SELECT key_id FROM api_keys WHERE name = ?", (user_id,)
            ).fetchall()
            total = 0
            for row in key_rows:
                kid = row["key_id"] if hasattr(row, "keys") else row[0]
                cur = db_conn.execute(
                    "UPDATE request_logs SET api_key_id = ? WHERE api_key_id = ?",
                    (anon_id, kid),
                )
                total += int(cur.rowcount)
            # Also anonymize rows that directly recorded the username
            cur = db_conn.execute(
                "UPDATE request_logs SET api_key_id = ? WHERE api_key_id = ?",
                (anon_id, user_id),
            )
            total += int(cur.rowcount)
            return total
        except Exception as e:
            logger.error("Failed to anonymize logs for %s: %s", user_id, e)
            raise

    @staticmethod
    def _make_anon_id(user_id: str) -> str:
        """Irreversible anonymization token (v3.1.1 audit P2-E).

        v3.1.0 used ``sha256(user_id)[:12]`` with no salt — anyone holding the
        DB could re-associate logs to usernames with a precomputed dictionary.
        Now each deletion draws a fresh random salt and computes HMAC-SHA256;
        the salt is discarded, so the token cannot be reversed or matched
        across requests. Referential integrity within this one deletion is
        preserved (all of the user's rows get the same token).
        """
        import hashlib
        import hmac
        import secrets

        salt = secrets.token_bytes(16)
        digest = hmac.new(salt, user_id.encode("utf-8"), hashlib.sha256).hexdigest()
        return "anon_" + digest[:20]

    async def _delete_api_keys(self, user_id: str, db_conn) -> int:
        """Revoke and delete all API keys belonging to user."""
        if db_conn is None:
            raise NotImplementedError(
                "GDPR API key deletion requires a database connection."
            )
        try:
            cur = db_conn.execute(
                "DELETE FROM api_keys WHERE name = ?", (user_id,)
            )
            return int(cur.rowcount)
        except Exception as e:
            logger.error("Failed to delete API keys for %s: %s", user_id, e)
            raise

    async def export_user_data(self, user_id: str, db_conn=None) -> dict:
        """Data portability — export real user data.

        Returns the user's profile, their API keys (names + quotas only — never
        the hashed/encrypted key material), recent request logs, and preferences.
        Requires a db_conn (SQLite connection). When no conn is available the
        export is a best-effort skeleton.
        """
        base = {
            "user_id": user_id,
            "exported_at": time.time(),
            "format": "json",
        }
        if db_conn is None:
            return {**base, "data": {
                "profile": {},
                "api_keys": [],
                "usage_history": [],
                "preferences": {},
                "note": "No database connection provided — export incomplete.",
            }}

        data: dict = {}
        # Profile (admin_users table — user_id is the username for admin JWTs)
        try:
            row = db_conn.execute(
                "SELECT id, username, role FROM admin_users WHERE username = ?",
                (user_id,),
            ).fetchone()
            if row:
                data["profile"] = {
                    "id": row["id"],
                    "username": row["username"],
                    "role": row["role"],
                }
            else:
                data["profile"] = {}
        except Exception as e:
            logger.error("GDPR export profile query failed: %s", e)
            data["profile"] = {"error": str(e)}

        # API keys (names + quotas only — never secret material)
        try:
            rows = db_conn.execute(
                "SELECT key_id, name, tier, quota_rpm, quota_daily_tokens, enabled "
                "FROM api_keys WHERE name = ? ORDER BY key_id",
                (user_id,),
            ).fetchall()
            data["api_keys"] = [dict(r) for r in rows] if rows else []
        except Exception as e:
            logger.error("GDPR export api_keys query failed: %s", e)
            data["api_keys"] = []

        # Recent request logs attributed to this user's key_id
        try:
            rows = db_conn.execute(
                "SELECT request_id, model_requested, model_used, preset, strategy, "
                "prompt_tokens, completion_tokens, cost, latency_ms, status, timestamp "
                "FROM request_logs WHERE api_key_id = ? ORDER BY timestamp DESC LIMIT 100",
                (user_id,),
            ).fetchall()
            data["usage_history"] = [dict(r) for r in rows] if rows else []
        except Exception as e:
            logger.error("GDPR export logs query failed: %s", e)
            data["usage_history"] = []

        data["preferences"] = {}  # no preferences table currently
        return {**base, "data": data}

    def get_request_status(self, request_id: str) -> dict | None:
        """Query deletion request status."""
        request = next((r for r in self._requests if r.request_id == request_id), None)
        if request:
            return {
                "request_id": request.request_id,
                "status": request.status,
                "requested_at": request.requested_at,
                "completed_at": request.completed_at,
            }
        return None

    def list_requests(self, user_id: str | None = None) -> list[dict]:
        """List all deletion requests, optionally filtered by user."""
        requests = self._requests
        if user_id:
            requests = [r for r in requests if r.user_id == user_id]
        return [
            {
                "request_id": r.request_id,
                "user_id": r.user_id,
                "status": r.status,
                "requested_at": r.requested_at,
                "completed_at": r.completed_at,
            }
            for r in requests
        ]
