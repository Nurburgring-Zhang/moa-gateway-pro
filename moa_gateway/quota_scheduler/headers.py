"""Rate-limit header parsing (M2).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):

- ``open-sse/services/rateLimitManager/headers.ts`` — STANDARD_HEADERS /
  ANTHROPIC_HEADERS tables and ``parseResetTime`` (duration strings like
  ``1h30m`` / ``500ms``, plain seconds, unix timestamps >1700000000, ISO
  dates — always returning milliseconds *until* reset).
- ``src/lib/quota/providerQuotaTelemetry.ts`` — ``resetHeaderToIso``
  (>10_000_000_000 → epoch-ms else epoch-seconds) and
  ``parseRateLimitHeaders`` (case-insensitive lookup, numeric coercion,
  QuotaValue + snapshot construction, source="response_headers",
  confidence="high").

``parse_quota_headers`` composes these into a provider-aware pass that reads
BOTH the request and token dimensions in one call — the real ingestion path
used by the monitor after every provider response.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .models import QuotaValue

# OmniRoute rateLimitManager/headers.ts
STANDARD_HEADERS = {
    "limit": "x-ratelimit-limit-requests",
    "remaining": "x-ratelimit-remaining-requests",
    "reset": "x-ratelimit-reset-requests",
    "limitTokens": "x-ratelimit-limit-tokens",
    "remainingTokens": "x-ratelimit-remaining-tokens",
    "resetTokens": "x-ratelimit-reset-tokens",
    "retryAfter": "retry-after",
    "overLimit": "x-ratelimit-over-limit",
}

ANTHROPIC_HEADERS = {
    "limit": "anthropic-ratelimit-requests-limit",
    "remaining": "anthropic-ratelimit-requests-remaining",
    "reset": "anthropic-ratelimit-requests-reset",
    "limitTokens": "anthropic-ratelimit-input-tokens-limit",
    "remainingTokens": "anthropic-ratelimit-input-tokens-remaining",
    "resetTokens": "anthropic-ratelimit-input-tokens-reset",
    "retryAfter": "retry-after",
}

_DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+)s)?(?:(\d+)ms)?$")


# ---------------------------------------------------------------------------
# Reset-time parsing
# ---------------------------------------------------------------------------


def parse_reset_time(value: str | None, now_ms: float | None = None) -> float | None:
    """Port of ``parseResetTime`` → milliseconds UNTIL reset, or None.

    Formats: duration strings ("1h30m", "45s", "500ms"), plain seconds,
    unix timestamps (>1700000000 → seconds since epoch), ISO dates.
    """
    if not value:
        return None
    if now_ms is None:
        import time

        now_ms = time.time() * 1000.0

    text = str(value).strip()
    match = _DURATION_RE.match(text)
    if match and any(match.groups()):
        hours, minutes, seconds, millis = match.groups()
        return (
            (int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)) * 1000
            + int(millis or 0)
        )

    try:
        num = float(text)
    except ValueError:
        num = math.nan
    if math.isfinite(num) and num > 0:
        if num > 1_700_000_000:  # unix timestamp (year ~2023+)
            return max(0.0, num * 1000 - now_ms)
        return num * 1000

    parsed_iso = _parse_iso_ms(text)
    if parsed_iso is not None:
        return max(0.0, parsed_iso - now_ms)
    return None


def _parse_iso_ms(text: str) -> float | None:
    candidate = text
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() * 1000


def reset_header_to_iso(value: str | None) -> str | None:
    """Port of ``resetHeaderToIso``: epoch numbers → ISO; dates re-emitted as
    UTC ISO; anything unparseable → None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    numeric = _number_header(text)
    if numeric is None:
        parsed_iso = _parse_iso_ms(text)
        if parsed_iso is None:
            return None
        return datetime.fromtimestamp(parsed_iso / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.") + f"{int(parsed_iso % 1000):03d}Z"
    milliseconds = numeric if numeric > 10_000_000_000 else numeric * 1000
    return (
        datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S."
        )
        + f"{int(milliseconds % 1000):03d}Z"
    )


# ---------------------------------------------------------------------------
# Header access helpers (case-insensitive)
# ---------------------------------------------------------------------------


def to_plain_headers(headers: Any) -> dict[str, str]:
    """Port of ``toPlainHeaders``: mapping / .items() / .forEach() → lowercase dict."""
    if headers is None:
        return {}
    plain: dict[str, str] = {}
    items = getattr(headers, "items", None)
    if callable(items):
        for key, value in headers.items():
            plain[str(key).lower()] = "" if value is None else str(value)
        return plain
    if isinstance(headers, Mapping):
        for key, value in headers.items():
            plain[str(key).lower()] = "" if value is None else str(value)
        return plain
    return plain


def _header_value(headers: dict[str, str], name: str | None) -> str | None:
    if not name:
        return None
    return headers.get(name.lower())


def _number_header(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


# ---------------------------------------------------------------------------
# parseRateLimitHeaders (providerQuotaTelemetry.ts)
# ---------------------------------------------------------------------------


def parse_rate_limit_headers(
    headers: Any,
    provider_id: str,
    connection_id: str,
    mapping: dict[str, str | None],
    captured_at: str | None = None,
) -> dict[str, Any] | None:
    """Port of ``parseRateLimitHeaders`` → ``{"value": QuotaValue,
    "snapshot": dict}`` or None when no mapped header carries data."""
    if captured_at is None:
        captured_at = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
        )
    plain = to_plain_headers(headers)
    limit = _number_header(_header_value(plain, mapping.get("limit")))
    remaining = _number_header(_header_value(plain, mapping.get("remaining")))
    reset_at = reset_header_to_iso(_header_value(plain, mapping.get("reset")))
    retry_after_seconds = _number_header(_header_value(plain, mapping.get("retryAfter")))

    if limit is None and remaining is None and not reset_at and retry_after_seconds is None:
        return None

    dimension = mapping.get("dimension") or "rate_limit"
    value = QuotaValue(
        dimension=dimension,
        limit=limit,
        remaining=remaining,
        reset_at=reset_at,
        unit=mapping.get("unit"),
        source="response_headers",
        confidence="high",
    )
    snapshot: dict[str, Any] = {
        "provider_id": provider_id,
        "connection_id": connection_id,
        "captured_at": captured_at,
        "source": "response_headers",
    }
    if limit is not None:
        snapshot["request_limit"] = limit
    if remaining is not None:
        snapshot["requests_remaining"] = remaining
    if reset_at:
        snapshot["reset_at"] = reset_at
    if retry_after_seconds is not None:
        snapshot["retry_after_seconds"] = retry_after_seconds
    return {"value": value, "snapshot": snapshot}


# ---------------------------------------------------------------------------
# Provider-aware composite parsing (requests + tokens dimensions)
# ---------------------------------------------------------------------------


def headers_for_provider(provider: str) -> dict[str, str]:
    """Anthropic uses its own header family; everyone else the standard set."""
    return ANTHROPIC_HEADERS if (provider or "").lower().startswith("anthropic") else STANDARD_HEADERS


def parse_quota_headers(
    headers: Any,
    provider_id: str,
    connection_id: str = "",
    now_ms: float | None = None,
) -> tuple[list[QuotaValue], dict[str, Any] | None]:
    """Parse BOTH rate-limit dimensions from a provider response.

    Returns ``(values, snapshot)``: ``values`` holds one QuotaValue per
    dimension actually present in the response headers; ``snapshot`` is the
    flat request-dimension snapshot (None when nothing matched).

    ``x-ratelimit-over-limit`` (standard family) is honoured: a truthy value
    with no explicit remaining count implies remaining=0 for the request
    dimension — the provider is telling us the budget is spent.
    """
    table = headers_for_provider(provider_id)
    values: list[QuotaValue] = []

    request_mapping = {
        "limit": table.get("limit"),
        "remaining": table.get("remaining"),
        "reset": table.get("reset"),
        "retryAfter": table.get("retryAfter"),
        "dimension": "requests",
        "unit": "requests",
    }
    parsed = parse_rate_limit_headers(headers, provider_id, connection_id, request_mapping)
    snapshot = None
    if parsed is not None:
        value: QuotaValue = parsed["value"]
        plain = to_plain_headers(headers)
        over_limit_raw = _header_value(plain, table.get("overLimit"))
        if over_limit_raw is not None and over_limit_raw.strip().lower() in ("true", "1", "yes"):
            if value.remaining is None or value.remaining > 0:
                value = value.model_copy(update={"remaining": 0.0})
        values.append(value)
        snapshot = parsed["snapshot"]

    if table.get("limitTokens") or table.get("remainingTokens"):
        token_mapping = {
            "limit": table.get("limitTokens"),
            "remaining": table.get("remainingTokens"),
            "reset": table.get("resetTokens"),
            "retryAfter": None,
            "dimension": "tokens",
            "unit": "tokens",
        }
        parsed_tokens = parse_rate_limit_headers(
            headers, provider_id, connection_id, token_mapping
        )
        if parsed_tokens is not None:
            values.append(parsed_tokens["value"])

    return values, snapshot


def retry_after_ms(headers: Any, now_ms: float | None = None) -> float | None:
    """Milliseconds to wait per ``retry-after`` (seconds or HTTP date)."""
    plain = to_plain_headers(headers)
    raw = plain.get("retry-after")
    if raw is None:
        return None
    numeric = _number_header(raw)
    if numeric is not None:
        return max(0.0, numeric * 1000)
    return parse_reset_time(raw, now_ms)
