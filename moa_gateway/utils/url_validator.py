"""URL validation utilities for SSRF prevention (v3.1.1 hardened).

v3.1.0 shipped three separate SSRF guards (utils/url_validator.py,
agent_loop/skills/api_verify.py, routes/mcp.py), each with the same hole:
a hostname that merely *resolves* to an internal address sailed through,
because only literal IP strings were checked. Encoded IP forms
(``http://2130706433/``, ``http://0x7f000001/``, ``http://0177.0.0.1/``)
also bypassed the ``ipaddress.ip_address`` parse and were treated as
ordinary domain names.

This module is now the single source of truth. It resolves every hostname
via ``socket.getaddrinfo`` (which normalizes decimal/hex/octal IP forms as
well) and rejects the URL if ANY resolved address is loopback, private,
link-local, reserved, multicast or unspecified.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_BLOCKED_HOSTS = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.internal",
    "metadata",
})

# Hostnames that must never be resolved/connected regardless of DNS answers.
_BLOCKED_SUFFIXES = (".local", ".internal")

# v3.1.1 second-round (audit P1-B): explicit IANA special-purpose ranges.
# Python's ipaddress flags are version-dependent — in 3.12 the RFC 6598 CGNAT
# block 100.64.0.0/10 is neither is_private nor is_reserved, yet it contains
# the Alibaba Cloud metadata endpoint 100.100.100.200. Fail closed against the
# full IANA list instead of trusting flag combinations.
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        # IPv4
        "0.0.0.0/8",          # "this" network
        "10.0.0.0/8",         # RFC1918
        "100.64.0.0/10",      # RFC6598 CGNAT (cloud metadata lives here)
        "127.0.0.0/8",        # loopback
        "169.254.0.0/16",     # link-local / AWS-GCP metadata
        "172.16.0.0/12",      # RFC1918
        "192.0.0.0/24",       # IETF protocol assignments
        "192.0.2.0/24",       # TEST-NET-1
        "192.168.0.0/16",     # RFC1918
        "198.18.0.0/15",      # benchmarking
        "198.51.100.0/24",    # TEST-NET-2
        "203.0.113.0/24",     # TEST-NET-3
        "240.0.0.0/4",        # reserved / future use
        # IPv6
        "::1/128",            # loopback
        "::/128",             # unspecified
        "::ffff:0:0/96",      # IPv4-mapped (checked again as mapped v4)
        "64:ff9b::/96",       # NAT64
        "64:ff9b:1::/48",     # local-use NAT64
        "100::/64",           # discard-only
        "2001:db8::/32",      # documentation
        "fc00::/7",           # unique local
        "fe80::/10",          # link-local
        "fec0::/10",          # deprecated site-local
        "ff00::/8",           # multicast
    )
)


def _ip_is_dangerous(ip_str: str) -> bool:
    """True if the address must not be reached from server-side requests."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → fail closed

    # IPv4-mapped IPv6 (::ffff:127.0.0.1) — judge the embedded IPv4 too.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    if any(ip in net for net in _BLOCKED_NETWORKS):
        return True

    # Belt and braces: keep the flag checks as well (covers ranges the
    # explicit list might miss on future Python versions).
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_safe_external_url(
    url: str,
    *,
    allow_internal_env: str = "MOA_ALLOW_SSRF_INTERNAL",
) -> tuple[bool, str]:
    """Validate a URL for server-side requests. Returns (is_safe, reason).

    Checks, in order:
    1. scheme is http/https;
    2. hostname present and not a known-internal name;
    3. explicit env override for trusted internal deployments;
    4. DNS resolution — EVERY returned address must be public. This also
       normalizes encoded IP literals (decimal / hex / octal), closing the
       v3.1.0 bypass class.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "invalid URL format"

    if parsed.scheme not in ("http", "https"):
        return False, f"unsupported protocol: {parsed.scheme or 'none'}"

    host = parsed.hostname
    if not host:
        return False, "no hostname in URL"

    host_l = host.lower()
    if host_l in _BLOCKED_HOSTS or any(host_l.endswith(s) for s in _BLOCKED_SUFFIXES):
        return False, f"blocked internal hostname: {host}"

    # Explicit operator override for trusted internal deployments.
    if os.environ.get(allow_internal_env) == "1":
        return True, ""

    # Resolve and check every address the name maps to.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return False, f"hostname does not resolve: {host} ({e})"
    except Exception as e:  # pragma: no cover - defensive
        return False, f"hostname resolution error: {e}"

    seen: set[str] = set()
    for info in infos:
        addr = info[4][0]
        if addr in seen:
            continue
        seen.add(addr)
        if _ip_is_dangerous(addr):
            return False, f"hostname {host} resolves to blocked address {addr}"

    if not seen:
        return False, f"hostname {host} resolved to no addresses"

    return True, ""


def validate_external_url(url: str) -> None:
    """Raise HTTPException(400) if *url* is not safe for external requests.

    Backward-compatible wrapper used by multimodal routes (video edit,
    image URLs, world-model scene refs, ...). Empty URLs pass — callers
    treat them as "no remote input".
    """
    if not url:
        return
    ok, reason = is_safe_external_url(url)
    if not ok:
        raise HTTPException(status_code=400, detail=f"URL rejected: {reason}")
