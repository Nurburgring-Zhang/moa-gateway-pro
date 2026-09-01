"""URL validation utilities for SSRF prevention (v3.1.1 hardened).

v3.1.0 shipped three separate SSRF guards (utils/url_validator.py,
agent_loop/skills/api_verify.py, routes/mcp.py), each with the same hole:
a hostname that merely *resolves* to an internal address sailed through,
because only literal IP strings were checked. Encoded IP forms
(``http://2130706433/``, ``http://0x7f000001/``, ``http://0177.0.0.1/``)
also bypassed the ``ipaddress.ip_address`` parse and were treated as
ordinary domain names.

This module is the single source of truth. Encoded IP literals —
single-integer, octal, hex and short-dotted IPv4, plus every IPv6 text
form — are recognized and normalized platform-independently BEFORE any
DNS lookup and judged directly against the blocked-range list. Relying on
``socket.getaddrinfo`` for that normalization is unsafe: its handling of
encoded forms is platform-specific (glibc applies full inet_aton(3)
semantics and maps ``2130706433`` to 127.0.0.1, while Windows resolved
the same host to a public address, letting the request through).
Ordinary domain names are still resolved via ``socket.getaddrinfo`` and
the URL is rejected if ANY resolved address is loopback, private,
link-local, reserved, multicast or unspecified. Hostnames that are
neither a valid IP literal nor a plausible domain name fail closed.
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

# Deprecated IPv4-compatible IPv6 addresses (::/96, RFC 4291 section 2.5.5.1).
# Python's ipaddress flags report them as global/public, but the embedded
# IPv4 address is what a stack would actually reach (``::7f00:1`` IS
# 127.0.0.1). No legitimate public target lives in this deprecated range,
# so block it wholesale.
_IPV4_COMPAT_V6 = ipaddress.ip_network("::/96")


def _ip_is_dangerous(ip_str: str) -> bool:
    """True if the address must not be reached from server-side requests."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → fail closed

    # IPv4-mapped IPv6 (::ffff:127.0.0.1) — judge the embedded IPv4 too.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    # IPv4-compatible IPv6 (::x.x.x.x) — deprecated range, see above.
    if isinstance(ip, ipaddress.IPv6Address) and ip in _IPV4_COMPAT_V6:
        return True

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


# ---------------------------------------------------------------------------
# Platform-independent IP-literal normalization (audit P0 follow-up).
#
# socket.getaddrinfo() interprets encoded IPv4 forms (single integer,
# octal, hex, short dotted) differently per platform: glibc applies full
# inet_aton(3) semantics, while Windows resolved http://2130706433/
# (decimal 127.0.0.1) to a public address in the wild — letting the
# request through. We therefore recognize and normalize IP literals
# ourselves, before any DNS lookup, using the broadest (inet_aton)
# interpretation any real HTTP stack applies.
# ---------------------------------------------------------------------------

_DEC_DIGITS = frozenset("0123456789")
_OCT_DIGITS = frozenset("01234567")
_HEX_DIGITS = frozenset("0123456789abcdef")

# Characters that can never appear in a DNS name / IDNA label. Anything
# not listed here is left to the DNS path, which still rejects every
# dangerous resolved address — permissiveness here cannot open a bypass.
_DOMAIN_FORBIDDEN = frozenset(" \t\r\n\f\v[]%\\'\"<>^`{|}~!$&()*+,;=:@#?/")


def _parse_aton_part(part: str) -> int:
    """Parse one inet_aton(3) address part: ``0x`` hex, ``0`` octal, decimal.

    Raises ValueError for empty or malformed parts. Mirrors glibc
    semantics: a leading ``0`` (without ``x``) selects octal, so ``09``
    is invalid, and unprefixed hex is not accepted.
    """
    if not part:
        raise ValueError("empty address part")
    p = part.lower()
    if p.startswith("0x"):
        digits = p[2:]
        if not digits or any(c not in _HEX_DIGITS for c in digits):
            raise ValueError(f"invalid hex address part {part!r}")
        return int(digits, 16)
    if len(p) > 1 and p[0] == "0":
        digits = p[1:]
        if any(c not in _OCT_DIGITS for c in digits):
            raise ValueError(f"invalid octal address part {part!r}")
        return int(digits, 8)
    if any(c not in _DEC_DIGITS for c in p):
        raise ValueError(f"invalid address part {part!r}")
    return int(p, 10)


def _inet_aton(host: str) -> str:
    """Normalize an inet_aton-style IPv4 literal to canonical dotted form.

    Accepts 1-4 dot-separated parts of decimal / octal / hex digits::

        1 part  -> 32-bit address   2130706433, 0x7f000001, 017700000001
        2 parts -> 8 + 24 bits      0x7f.1
        3 parts -> 8 + 8 + 16 bits  127.0.1
        4 parts -> 4 x 8 bits       0177.0.0.1

    Raises ValueError for anything outside that grammar (including
    out-of-range parts), which callers turn into a fail-closed reject.
    """
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"expected 1-4 address parts, got {len(parts)}")
    values = [_parse_aton_part(p) for p in parts]
    widths = [8] * (len(values) - 1) + [32 - 8 * (len(values) - 1)]
    addr = 0
    for value, width in zip(values, widths):
        if value >= 1 << width:
            raise ValueError(f"address part {value} exceeds {width}-bit field")
        addr = (addr << width) | value
    return str(ipaddress.IPv4Address(addr))


def _alternate_decimal_dangerous(host: str) -> bool:
    """Judge the all-decimal reading of an ambiguous leading-zero literal.

    Parts like ``010`` are interpreted as octal by glibc inet_aton (the
    primary reading used by ``_inet_aton``) but as *decimal* by other
    stacks (.NET ``IPAddress.Parse``, legacy Windows parsers). If the
    host contains such parts and the all-decimal reading is a valid
    address, it must be judged too — the literal is blocked when EITHER
    interpretation reaches a dangerous range. Returns False when there is
    no leading-zero ambiguity or the decimal reading is not a valid
    address (i.e. no stack could reach it that way).
    """
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return False
    ambiguous = False
    values = []
    for part in parts:
        p = part.lower()
        if not p or any(c not in _DEC_DIGITS for c in p):
            return False  # hex/non-numeric part -> no decimal reading exists
        if len(p) > 1 and p[0] == "0":
            ambiguous = True
        values.append(int(p, 10))
    if not ambiguous:
        return False
    widths = [8] * (len(values) - 1) + [32 - 8 * (len(values) - 1)]
    addr = 0
    for value, width in zip(values, widths):
        if value >= 1 << width:
            return False  # decimal reading out of range -> unreachable
        addr = (addr << width) | value
    return _ip_is_dangerous(str(ipaddress.IPv4Address(addr)))


def _ip_literal_attempt(host: str) -> bool:
    """True when every dot-separated label starts with an ASCII digit.

    Every inet_aton encoding (decimal / octal / hex, single or dotted)
    starts with a digit. Real domains may contain digit-initial labels
    (``360.com``), but then at least one label does not start with a
    digit — so only all-digit-initial hosts are treated as IP-literal
    attempts and held to the inet_aton grammar.
    """
    return all(part and part[0] in _DEC_DIGITS for part in host.split("."))


def _looks_like_domain(host: str) -> bool:
    """Conservative DNS-name shape check.

    Deliberately permissive on what it accepts as "resolvable" (the DNS
    path still validates every returned address); strict on characters
    and label geometry that can never be a hostname.
    """
    if not host or len(host) > 253:
        return False
    if any(c in _DOMAIN_FORBIDDEN or ord(c) < 0x20 for c in host):
        return False
    for label in host.split("."):
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
    return True


def _normalize_host_ip(host: str) -> tuple[str | None, str | None]:
    """Classify a hostname without consulting getaddrinfo.

    Returns:
      ``(ip, None)``     — host is an IP literal in some classic encoding,
                           normalized to canonical IPv4/IPv6 text;
      ``(None, None)``   — plausible DNS name, must go through resolution;
      ``(None, reason)`` — neither a valid IP literal nor a plausible
                           domain name; caller must fail closed.
    """
    h = host.strip().rstrip(".")  # trailing-dot / FQDN-root tricks
    if not h:
        return None, "empty hostname"

    if ":" in h:
        # urlparse already stripped the brackets of an IPv6 literal, and a
        # colon can never appear in a DNS name. Covers compressed forms,
        # ::1, ::ffff:127.0.0.1, ::ffff:7f00:1, zone-indexed link-local.
        try:
            return str(ipaddress.IPv6Address(h)), None
        except ValueError:
            return None, f"invalid IPv6 literal: {host}"

    # Strict dotted-quad IPv4 (fast path for the common case).
    try:
        return str(ipaddress.IPv4Address(h)), None
    except ValueError:
        pass

    # Encoded IPv4: single integer / octal / hex / short dotted forms.
    if _ip_literal_attempt(h):
        try:
            return _inet_aton(h), None
        except ValueError as e:
            return None, f"invalid encoded IP literal {host!r}: {e}"

    if _looks_like_domain(h):
        return None, None
    return None, (
        f"hostname {host!r} is neither a valid IP literal nor a domain name"
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
    4. platform-independent IP-literal normalization — encoded IPv4 forms
       (single integer, octal, hex, short dotted) and IPv6 literals are
       normalized and judged directly against the blocked-range list,
       without getaddrinfo (whose encoded-IP handling is platform-
       specific); hosts that are neither a valid IP literal nor a
       plausible domain name fail closed;
    5. DNS resolution for ordinary domain names — EVERY returned address
       must be public (guards against DNS-rebinding).
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

    # Lowercase + strip FQDN trailing dot so name-based blocklist entries
    # ("localhost", ".internal") cannot be evaded with "LOCALHOST." etc.
    host_l = host.lower().rstrip(".")

    if host_l in _BLOCKED_HOSTS or any(host_l.endswith(s) for s in _BLOCKED_SUFFIXES):
        return False, f"blocked internal hostname: {host}"

    # Explicit operator override for trusted internal deployments.
    if os.environ.get(allow_internal_env) == "1":
        return True, ""

    # Platform-independent IP-literal check BEFORE any DNS lookup. Closes
    # the encoded-IP bypass class (decimal / octal / hex / short dotted
    # IPv4 and all IPv6 text forms) that getaddrinfo normalizes
    # differently per platform.
    ip_literal, ip_error = _normalize_host_ip(host_l)
    if ip_error is not None:
        return False, ip_error
    if ip_literal is not None:
        if _ip_is_dangerous(ip_literal):
            return False, (
                f"hostname {host} is a blocked IP literal ({ip_literal})"
            )
        if _alternate_decimal_dangerous(host_l):
            return False, (
                f"hostname {host} is an ambiguous octal/decimal IP literal "
                f"whose decimal reading is a blocked address"
            )
        # Canonical public IP literal — safe without any resolution.
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
