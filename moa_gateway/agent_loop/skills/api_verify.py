"""API verification skill — verify endpoint availability and response quality.

Inspired by Paseo's paseo-loop verify-check mechanism (shell command verification)
and verify-prompt mechanism (LLM judgment verification). Adapted for
moa-gateway-pro's ToolExecutor interface.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import time
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Validate URL to prevent SSRF attacks.

    Returns (is_safe, error_message).
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "Invalid URL format"

    # Only allow http/https protocols
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported protocol: {parsed.scheme}"

    hostname = parsed.hostname
    if not hostname:
        return False, "No hostname in URL"

    # Block cloud metadata endpoints
    metadata_hosts = {"169.254.169.254", "metadata.google.internal", "metadata"}
    if hostname.lower() in metadata_hosts:
        return False, f"Blocked metadata endpoint: {hostname}"

    # Check if hostname is an IP address
    try:
        ip = ipaddress.ip_address(hostname)
        # Block private, loopback, link-local, reserved, multicast
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False, f"Blocked internal IP: {hostname}"
        if ip.is_reserved or ip.is_multicast:
            return False, f"Blocked reserved IP: {hostname}"
    except ValueError:
        # It is a domain name, not an IP - allow it
        pass

    return True, ""


async def api_verify(
    endpoint_id: str = "",
    test_prompt: str = "Hello",
    expect_json: bool = True,
    url: str = "",
    method: str = "POST",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    expected_status: int = 200,
    expected_fields: list[str] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    timeout: float = 30.0,
    base_url: str = "",
) -> dict[str, Any]:
    """Verify an API endpoint's availability and response quality.

    Validation dimensions (inspired by Paseo worker/verifier loop):
    - Connectivity: can we reach the endpoint?
    - Correctness: does the response format match expectations?
    - Latency: is the response time within acceptable bounds?
    - Content quality: does the response contain meaningful content?

    Args:
        endpoint_id: Endpoint ID to verify (resolves via gateway chat API).
                     If empty, ``url`` must be provided.
        test_prompt: Prompt to send for testing (default "Hello").
        expect_json: Whether to validate JSON response format.
        url: Direct URL to verify (overrides endpoint_id resolution).
        method: HTTP method (default POST for chat endpoints).
        headers: Custom request headers.
        body: Custom request body (defaults to chat completion format).
        expected_status: Expected HTTP status code (default 200).
        expected_fields: Dot-path fields expected in the JSON response.
        assertions: List of assertion dicts:
            {"field": "data.id", "op": "eq|ne|gt|lt|contains|exists", "value": Any}
        timeout: Request timeout in seconds.
        base_url: Gateway base URL (defaults to MOA_GATEWAY_URL env).

    Returns:
        dict with keys:
        - success: bool — whether all checks passed
        - endpoint_id: str — the endpoint that was verified
        - latency_ms: float — measured latency
        - details: str — human-readable summary
        - checks: list[dict] — individual check results
        - status_code: int — HTTP status code received
        - response_body: str — truncated response body
    """
    import httpx

    # --- Resolve target URL ---
    if url:
        target_url = url
    elif endpoint_id:
        gw = base_url or os.environ.get(
            "MOA_GATEWAY_URL", "http://127.0.0.1:8910"
        )
        target_url = f"{gw}/v1/chat/completions"
    else:
        return {
            "success": False,
            "endpoint_id": endpoint_id,
            "latency_ms": 0.0,
            "details": "No endpoint_id or url provided",
            "checks": [],
        }

    # --- Build request ---
    req_headers = headers or {"Content-Type": "application/json"}
    req_body = body
    if not req_body and method.upper() in ("POST", "PUT", "PATCH"):
        req_body = {
            "model": endpoint_id or "auto",
            "messages": [{"role": "user", "content": test_prompt}],
        }

    # --- SSRF protection ---
    # Only validate user-supplied URLs (url or base_url params).
    # Internal gateway URLs (env var or default 127.0.0.1) are trusted.
    _ssrf_url = url or base_url
    if _ssrf_url:
        is_safe, ssrf_error = _is_safe_url(_ssrf_url)
        if not is_safe:
            return {
                "success": False,
                "endpoint_id": endpoint_id,
                "latency_ms": 0.0,
                "details": f"URL validation failed: {ssrf_error}",
                "checks": [{"name": "url_safety", "passed": False, "error": ssrf_error}],
            }

    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    start = time.time()

    # --- Execute request ---
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.request(
                method,
                target_url,
                headers=req_headers,
                json=req_body,
            )
    except httpx.ConnectError as exc:
        latency = (time.time() - start) * 1000
        checks.append(
            {"name": "connectivity", "passed": False, "error": str(exc)}
        )
        errors.append(f"Connection failed: {exc}")
        return {
            "success": False,
            "endpoint_id": endpoint_id,
            "latency_ms": round(latency, 2),
            "details": f"Connection error: {exc}",
            "checks": checks,
        }
    except httpx.TimeoutException as exc:
        latency = (time.time() - start) * 1000
        checks.append(
            {"name": "connectivity", "passed": False, "error": "timeout"}
        )
        errors.append(f"Request timed out after {timeout}s")
        return {
            "success": False,
            "endpoint_id": endpoint_id,
            "latency_ms": round(latency, 2),
            "details": f"Timeout: {exc}",
            "checks": checks,
        }
    except Exception as exc:  # noqa: BLE001
        latency = (time.time() - start) * 1000
        checks.append(
            {"name": "connectivity", "passed": False, "error": str(exc)}
        )
        errors.append(f"Request failed: {exc}")
        return {
            "success": False,
            "endpoint_id": endpoint_id,
            "latency_ms": round(latency, 2),
            "details": f"Request error: {exc}",
            "checks": checks,
        }

    latency_ms = (time.time() - start) * 1000

    # --- Check 1: Connectivity (we got a response) ---
    checks.append(
        {"name": "connectivity", "passed": True, "latency_ms": round(latency_ms, 2)}
    )

    # --- Check 2: Status code ---
    status_ok = response.status_code == expected_status
    checks.append(
        {
            "name": "status_code",
            "passed": status_ok,
            "expected": expected_status,
            "actual": response.status_code,
        }
    )
    if not status_ok:
        errors.append(
            f"Status code mismatch: expected {expected_status}, "
            f"got {response.status_code}"
        )

    # --- Check 3: JSON format ---
    resp_json: Any = None
    if expect_json:
        try:
            resp_json = response.json()
            checks.append({"name": "json_format", "passed": True})
        except Exception:  # noqa: BLE001
            checks.append({"name": "json_format", "passed": False})
            errors.append("Response is not valid JSON")

    # --- Check 4: Latency ---
    latency_threshold = 10_000  # 10 seconds
    latency_ok = latency_ms < latency_threshold
    checks.append(
        {
            "name": "latency",
            "passed": latency_ok,
            "latency_ms": round(latency_ms, 2),
            "threshold_ms": latency_threshold,
        }
    )
    if not latency_ok:
        errors.append(
            f"Latency too high: {latency_ms:.0f}ms > {latency_threshold}ms"
        )

    # --- Check 5: Content quality ---
    content = ""
    if resp_json is not None:
        if isinstance(resp_json, dict):
            if "choices" in resp_json:
                choices = resp_json.get("choices", [])
                if choices and isinstance(choices, list):
                    content = (
                        choices[0].get("message", {}).get("content", "")
                        if isinstance(choices[0], dict)
                        else ""
                    )
            elif "final_content" in resp_json:
                content = resp_json.get("final_content", "")
            elif "content" in resp_json:
                content = resp_json.get("content", "")
            else:
                content = str(resp_json)[:500]
        elif isinstance(resp_json, str):
            content = resp_json

    content_ok = len(content) > 0
    checks.append(
        {
            "name": "content_quality",
            "passed": content_ok,
            "content_length": len(content),
            "content_preview": content[:200] if content else "",
        }
    )
    if not content_ok:
        errors.append("Response has no meaningful content")

    # --- Check 6: Expected fields ---
    if expected_fields and resp_json is not None:
        for field in expected_fields:
            exists = _field_exists(resp_json, field)
            checks.append(
                {"name": f"field_exists:{field}", "passed": exists}
            )
            if not exists:
                errors.append(f"Missing field: {field}")

    # --- Check 7: Assertions ---
    if assertions and resp_json is not None:
        for assertion in assertions:
            field = assertion.get("field", "")
            op = assertion.get("op", "eq")
            expected_val = assertion.get("value")
            actual_val = _get_field(resp_json, field)
            passed = _eval_assertion(op, actual_val, expected_val)
            checks.append(
                {
                    "name": f"assert:{field} {op} {expected_val}",
                    "passed": passed,
                    "actual": actual_val,
                }
            )
            if not passed:
                errors.append(
                    f"Assertion failed: {field} {op} {expected_val}, "
                    f"got {actual_val}"
                )

    success = len(errors) == 0
    if success:
        details = f"All {len(checks)} checks passed"
    else:
        details = f"{len(errors)} error(s): {'; '.join(errors)}"

    return {
        "success": success,
        "endpoint_id": endpoint_id,
        "latency_ms": round(latency_ms, 2),
        "details": details,
        "checks": checks,
        "status_code": response.status_code,
        "response_body": response.text[:2000],
    }


# --- Helper functions ---


def _get_field(data: Any, path: str) -> Any:
    """Get a nested field via dot-path like 'data.items.0.id'.

    Supports dict keys and list indices. Does NOT use jsonpath_ng
    to avoid an extra dependency.
    """
    # Normalize: convert [0] to .0
    normalized = path.replace("[", ".").replace("]", "")
    parts = normalized.split(".")
    current = data
    for part in parts:
        if not part:
            continue
        if part.lstrip("-").isdigit():
            idx = int(part)
            if isinstance(current, list) and -len(current) <= idx < len(current):
                current = current[idx]
            else:
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _field_exists(data: Any, path: str) -> bool:
    """Check whether a dot-path field exists in the data."""
    return _get_field(data, path) is not None


def _eval_assertion(op: str, actual: Any, expected: Any) -> bool:
    """Evaluate a single assertion against actual and expected values."""
    ops = {
        "eq": lambda a, e: a == e,
        "ne": lambda a, e: a != e,
        "gt": lambda a, e: a is not None and _safe_compare(a, e, ">"),
        "lt": lambda a, e: a is not None and _safe_compare(a, e, "<"),
        "gte": lambda a, e: a is not None and _safe_compare(a, e, ">="),
        "lte": lambda a, e: a is not None and _safe_compare(a, e, "<="),
        "contains": lambda a, e: a is not None and e in a,
        "exists": lambda a, e: a is not None,
        "not_exists": lambda a, e: a is None,
    }
    return ops.get(op, lambda a, e: False)(actual, expected)


def _safe_compare(a: Any, b: Any, operator: str) -> bool:
    """Safely compare two values, returning False on type errors."""
    try:
        if operator == ">":
            return a > b
        elif operator == "<":
            return a < b
        elif operator == ">=":
            return a >= b
        elif operator == "<=":
            return a <= b
    except TypeError:
        return False
    return False
