"""Security Configuration Baseline Check — SOC2 compliance."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


@dataclass
class CheckResult:
    """Result of a single security baseline check."""

    name: str
    passed: bool
    severity: str  # critical/high/medium/low/info
    message: str
    remediation: str = ""


class SecurityBaselineChecker:
    """SOC2 security configuration baseline checker — 10 checks."""

    def run_all_checks(self) -> List[CheckResult]:
        """Run all security baseline checks."""
        return [
            self._check_jwt_secret(),
            self._check_encryption_key(),
            self._check_admin_password(),
            self._check_debug_mode(),
            self._check_cors_config(),
            self._check_tls_config(),
            self._check_log_level(),
            self._check_rate_limiting(),
            self._check_session_timeout(),
            self._check_key_rotation(),
        ]

    def summary(self) -> dict:
        """Run checks and return summary."""
        results = self.run_all_checks()
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        critical = [r for r in results if not r.passed and r.severity == "critical"]
        return {
            "total_checks": len(results),
            "passed": passed,
            "failed": failed,
            "critical_failures": len(critical),
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "severity": r.severity,
                    "message": r.message,
                    "remediation": r.remediation,
                }
                for r in results
            ],
        }

    def _check_jwt_secret(self) -> CheckResult:
        secret = os.getenv("MOA_JWT_SECRET", "")
        if not secret:
            return CheckResult(
                "jwt_secret", False, "critical",
                "JWT secret not configured",
                "Set MOA_JWT_SECRET env var with 32+ char secret",
            )
        if len(secret) < 32:
            return CheckResult(
                "jwt_secret", False, "high",
                f"JWT secret too short ({len(secret)} chars)",
                "Use 32+ character secret",
            )
        return CheckResult("jwt_secret", True, "info", "JWT secret configured correctly")

    def _check_encryption_key(self) -> CheckResult:
        key = os.getenv("MOA_ENCRYPTION_KEY", "")
        if not key:
            return CheckResult(
                "encryption_key", False, "high",
                "Encryption key not set — data at rest not encrypted",
                "Set MOA_ENCRYPTION_KEY for field-level encryption",
            )
        return CheckResult("encryption_key", True, "info", "Encryption key configured")

    def _check_admin_password(self) -> CheckResult:
        pwd = os.getenv("MOA_ADMIN_PASSWORD", "")
        if not pwd or len(pwd) < 12:
            return CheckResult(
                "admin_password", False, "critical",
                "Admin password weak or missing",
                "Use 12+ char complex password",
            )
        return CheckResult("admin_password", True, "info", "Admin password meets requirements")

    def _check_debug_mode(self) -> CheckResult:
        debug = os.getenv("MOA_DEBUG", "false").lower()
        if debug in ("true", "1", "yes"):
            return CheckResult(
                "debug_mode", False, "high",
                "Debug mode enabled in production",
                "Set MOA_DEBUG=false",
            )
        return CheckResult("debug_mode", True, "info", "Debug mode disabled")

    def _check_cors_config(self) -> CheckResult:
        cors = os.getenv("MOA_CORS_ORIGINS", "*")
        if cors == "*":
            return CheckResult(
                "cors", False, "medium",
                "CORS allows all origins",
                "Restrict to specific domains",
            )
        return CheckResult("cors", True, "info", "CORS properly restricted")

    def _check_tls_config(self) -> CheckResult:
        cert = os.getenv("MOA_TLS_CERT", "")
        if not cert:
            return CheckResult(
                "tls", False, "medium",
                "TLS not configured (relying on reverse proxy)",
                "Configure TLS or ensure reverse proxy handles it",
            )
        return CheckResult("tls", True, "info", "TLS configured")

    def _check_log_level(self) -> CheckResult:
        level = os.getenv("MOA_LOG_LEVEL", "INFO").upper()
        if level == "DEBUG":
            return CheckResult(
                "log_level", False, "medium",
                "Debug logging may expose sensitive data",
                "Set MOA_LOG_LEVEL=INFO or WARNING in production",
            )
        return CheckResult("log_level", True, "info", "Log level appropriate")

    def _check_rate_limiting(self) -> CheckResult:
        limit = os.getenv("PROXY_RATE_LIMIT", "")
        if not limit:
            return CheckResult(
                "rate_limit", False, "medium",
                "Rate limiting not explicitly configured",
                "Set PROXY_RATE_LIMIT to prevent abuse",
            )
        return CheckResult("rate_limit", True, "info", "Rate limiting configured")

    def _check_session_timeout(self) -> CheckResult:
        timeout = os.getenv("MOA_SESSION_TIMEOUT", "3600")
        try:
            val = int(timeout)
            if val > 86400:
                return CheckResult(
                    "session_timeout", False, "medium",
                    f"Session timeout too long ({val}s)",
                    "Set to <= 24h (86400s)",
                )
        except ValueError:
            pass
        return CheckResult("session_timeout", True, "info", "Session timeout acceptable")

    def _check_key_rotation(self) -> CheckResult:
        days = os.getenv("MOA_KEY_ROTATION_DAYS", "")
        if not days:
            return CheckResult(
                "key_rotation", False, "medium",
                "Key rotation not configured",
                "Set MOA_KEY_ROTATION_DAYS=90",
            )
        return CheckResult("key_rotation", True, "info", "Key rotation configured")
