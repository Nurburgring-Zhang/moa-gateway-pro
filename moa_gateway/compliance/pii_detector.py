"""PII Detection and Redaction — protect personal privacy data."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class PIIMatch:
    """A detected PII instance."""

    type: str
    value: str
    start: int
    end: int
    masked: str


class PIIDetector:
    """Personal Identifiable Information detector with 9+ patterns."""

    PATTERNS: dict[str, str] = {
        "email": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b",
        "phone_cn": r"\b1[3-9]\d{9}\b",
        "phone_intl": r"\+[1-9]\d{6,14}\b",
        "credit_card": r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b",
        "ssn_us": r"\b\d{3}-\d{2}-\d{4}\b",
        "id_card_cn": r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",  # noqa: E501
        "ip_address": r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",  # noqa: E501
        "api_key": r"\b(?:sk|pk|api)[_\-][A-Za-z0-9]{20,}\b",
        "jwt_token": r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
    }

    def __init__(self, enabled_types: List[str] | None = None):
        self._types = enabled_types or list(self.PATTERNS.keys())

    def detect(self, text: str) -> List[PIIMatch]:
        """Detect PII in text."""
        matches: List[PIIMatch] = []
        for pii_type in self._types:
            pattern = self.PATTERNS.get(pii_type)
            if not pattern:
                continue
            for m in re.finditer(pattern, text):
                matches.append(
                    PIIMatch(
                        type=pii_type,
                        value=m.group(),
                        start=m.start(),
                        end=m.end(),
                        masked=self._mask(pii_type, m.group()),
                    )
                )
        return matches

    def redact(self, text: str) -> str:
        """Redact PII from text."""
        matches = sorted(self.detect(text), key=lambda m: m.start, reverse=True)
        result = text
        for match in matches:
            result = result[: match.start] + match.masked + result[match.end :]
        return result

    def has_pii(self, text: str) -> bool:
        """Quick check if text contains PII."""
        return len(self.detect(text)) > 0

    def _mask(self, pii_type: str, value: str) -> str:
        """Generate masked value based on type."""
        if pii_type == "email":
            parts = value.split("@")
            return parts[0][:2] + "***@" + parts[1] if len(parts) == 2 else "***"
        elif pii_type in ("phone_cn", "phone_intl"):
            return value[:3] + "****" + value[-4:]
        elif pii_type == "credit_card":
            return value[:4] + "****" + value[-4:]
        elif pii_type in ("ssn_us", "id_card_cn"):
            return value[:3] + "***" + value[-3:]
        elif pii_type == "ip_address":
            parts = value.rsplit(".", 1)
            return parts[0] + ".xxx" if len(parts) == 2 else "xxx.xxx.xxx.xxx"
        elif pii_type in ("api_key", "jwt_token"):
            return value[:8] + "..." + value[-4:]
        return "***"


# Global detector instance
pii_detector = PIIDetector()
