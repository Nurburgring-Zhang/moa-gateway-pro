"""URL validation utilities for SSRF prevention."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from fastapi import HTTPException


def validate_external_url(url: str) -> None:
    """Validate that a URL is safe for external requests (no SSRF).

    Blocks:
    - Non-http(s) schemes
    - Loopback/private/link-local/reserved IP addresses
    - Known internal metadata hostnames
    """
    if not url:
        return

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http/https URLs are allowed")

    host = parsed.hostname or ""

    # Check blocked hostnames
    blocked_hosts = {"localhost", "metadata.google.internal", "metadata.internal"}
    if host.lower() in blocked_hosts:
        raise HTTPException(status_code=400, detail="Blocked host")

    # Check if host is an IP address and validate
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
            raise HTTPException(
                status_code=400, detail="Private/loopback URLs are not allowed"
            )
    except ValueError:
        # Not an IP address (it's a hostname), that's fine
        pass
