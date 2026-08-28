"""URL validation utilities for SSRF prevention (v3.1.1 hardened).

v3.1.0 shipped three separate SSRF guards (utils/url_validator.py,
agent_loop/skills/api_verify.py, routes/mcp.py), each with the same hole:
a hostname that merely *resolves* to an internal address sailed through,
because only literal IP strings were checked. Encoded IP forms
(``http://2130706433/``, ``http://0x7f000001/``, ``http://0177.0.0.1/``)
also bypassed the ``ipaddress.ip_address`` parse and were treated as
ordinary domain names.

This module is now the single source of truth. Before any DNS lookup it
canonicalizes numeric IP literals with inet_aton semantics (decimal / hex /
octal, 1-4 dot-separated parts) so the check is platform independent —
glibc's ``getaddrinfo`` normalizes those forms, but the Windows resolver
does not, and a resolver that answers for arbitrary hostnames would
otherwise turn an encoded loopback literal into a "resolvable domain".
It then resolves every remaining hostname via ``socket.getaddrinfo`` and
rejects the URL if ANY resolved address is loopback, private, link-local,
reserved, multicast or unspecified.
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
        # v3.2.1 (red-team): IPv6 transition mechanisms can embed IPv4
        # addresses the explicit list above never sees.
        "2002::/16",          # 6to4 — [2002:7f00:1::] embeds 127.0.0.1
        "2001:0::/32",        # Teredo
        "192.88.99.0/24",     # 6to4 relay anycast
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


def _canonicalize_numeric_host(host: str) -> ipaddress.IPv4Address | None:
    """Canonicalize a numeric host with inet_aton semantics.

    Accepts decimal/hex/octal parts, 1-4 dot-separated parts, e.g.
    ``2130706433``, ``0x7f000001``, ``0177.0.0.1``, ``127.1``, ``0x7f.0.0.1``.

    Returns the canonical IPv4Address, ``None`` when the host is not a
    numeric literal (a regular DNS name — including bare hex words like
    ``deadbeef``, which inet_aton also treats as a name), and raises
    ``ValueError`` when the host *is* numeric-looking but malformed or
    overflows a 32-bit address (caller must fail closed).
    """
    if not host or not host[0].isdigit() and host[0] not in ("." ,):
        return None

    parts = host.split(".")
    if any(p == "" for p in parts):
        raise ValueError(f"empty component in numeric host: {host}")

    # Parse each component: 0x-prefixed hex, leading-0 octal, else decimal.
    # A component containing non-[0-9] characters (other than a 0x prefix's
    # hex digits) means this is not a numeric host at all.
    values: list[int] = []
    for p in parts:
        if p.startswith("0x") or p.startswith("0X"):
            try:
                values.append(int(p, 16))
            except ValueError:
                raise ValueError(f"bad hex component: {p}") from None
            continue
        if not p.isdigit():
            return None
        if len(p) > 1 and p[0] == "0":
            values.append(int(p, 8))
        else:
            values.append(int(p, 10))

    n = len(values)
    if n == 1:
        total = values[0]
        if total > 0xFFFFFFFF:
            raise ValueError(f"numeric host overflows 32 bits: {host}")
    elif n == 2:
        if values[1] > 0xFFFFFF:
            raise ValueError(f"numeric host overflows 24-bit final part: {host}")
        total = (values[0] << 24) | values[1]
    elif n == 3:
        if values[2] > 0xFFFF:
            raise ValueError(f"numeric host overflows 16-bit final part: {host}")
        total = (values[0] << 24) | (values[1] << 16) | values[2]
    elif n == 4:
        if any(v > 0xFF for v in values):
            raise ValueError(f"octet out of range in {host}")
        total = (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3]
    else:
        raise ValueError(f"too many components in numeric host: {host}")

    return ipaddress.IPv4Address(total)


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
    4. numeric IP literals — canonicalized with inet_aton semantics
       (decimal / hex / octal, 1-4 parts) and checked directly, with no
       DNS round-trip; malformed numeric hosts fail closed;
    5. DNS resolution — EVERY returned address must be public.
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
    # strip one trailing dot (absolute FQDN form) so "metadata.google.internal."
    # still hits the blocklists (v3.2.1 red-team finding)
    if host_l.endswith("."):
        host_l = host_l.rstrip(".")
    if host_l in _BLOCKED_HOSTS or any(host_l.endswith(s) for s in _BLOCKED_SUFFIXES):
        return False, f"blocked internal hostname: {host}"

    # Explicit operator override for trusted internal deployments.
    if os.environ.get(allow_internal_env) == "1":
        return True, ""

    # Platform-independent IP-literal check before any DNS interaction.
    # glibc normalizes encoded literals, the Windows resolver does not —
    # and a resolver that wildcard-answers every name would otherwise turn
    # an encoded loopback literal into a "resolvable public domain".
    try:
        literal = _canonicalize_numeric_host(host_l)
    except ValueError as e:
        return False, f"malformed numeric host: {host} ({e})"
    if literal is not None:
        if _ip_is_dangerous(str(literal)):
            return False, f"blocked address literal: {host} ({literal})"
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
