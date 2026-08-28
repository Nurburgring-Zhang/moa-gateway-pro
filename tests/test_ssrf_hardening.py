"""SSRF hardening regression tests (v3.2.1 F1).

The v3.1.1 guard relied on ``socket.getaddrinfo`` normalizing encoded IP
literals (decimal / hex / octal). That normalization is a *glibc* behavior:
the Windows resolver does not normalize, and a resolver that wildcard-answers
arbitrary hostnames turns ``http://2130706433/`` into a "resolvable public
domain". These tests pin the platform-independent inet_aton-style
canonicalization in ``moa_gateway.utils.url_validator``.

DNS is mocked in every test that reaches the resolution path, so the suite
is deterministic on any OS / network. The guard logic under test is the real
implementation — only the resolver boundary is stubbed.
"""

from __future__ import annotations

import socket

import pytest

from moa_gateway.utils.url_validator import (
    _canonicalize_numeric_host,
    _ip_is_dangerous,
    is_safe_external_url,
)

import ipaddress


# ---------------------------------------------------------------------------
# Resolver stub: make every DNS path deterministic without changing guard logic
# ---------------------------------------------------------------------------

_PUBLIC_ANSWER = [
    (socket.AddressFamily.AF_INET, socket.SocketKind.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
]


@pytest.fixture(autouse=True)
def _hermetic_dns(monkeypatch):
    """Stub getaddrinfo: names ending in .example.com resolve to a public IP,
    anything else raises gaierror (NXDOMAIN). Override per-test via
    ``monkeypatch`` as needed."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host.endswith(".example.com"):
            return _PUBLIC_ANSWER
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known (stub)")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ---------------------------------------------------------------------------
# Encoded IP literals must be blocked WITHOUT any DNS round-trip
# ---------------------------------------------------------------------------

class TestEncodedIpLiteralsBlocked:
    @pytest.mark.parametrize(
        "url",
        [
            "http://2130706433/",        # decimal
            "http://0x7f000001/",        # hex single
            "http://0X7F000001/",        # hex uppercase
            "http://0177.0.0.1/",        # octal dotted
            "http://0x7f.0.0.1/",        # hex component
            "http://127.1/",             # 2-part short form
            "http://127.0.0x1/",         # mixed hex tail
            "http://169.254.169.254/",
            "http://100.64.0.1/",        # CGNAT / cloud metadata
            "http://0/",
        ],
    )
    def test_blocked_without_dns(self, url, monkeypatch):
        """DNS must never be consulted for numeric literals."""
        def explode(host, port, *a, **kw):
            raise AssertionError(f"DNS consulted for literal host: {host!r}")

        monkeypatch.setattr(socket, "getaddrinfo", explode)
        ok, reason = is_safe_external_url(url)
        assert not ok, f"{url} must be blocked"
        assert reason

    @pytest.mark.parametrize(
        "url",
        [
            "http://4294967296/",     # 2^32 overflow → fail closed
            "http://999.1.1.1/",      # octet overflow → fail closed
            "http://127..0.1/",       # empty component
            "http://1.2.3.4.5/",      # too many parts
            "http://0177.0.0.09/",    # invalid octal digit
        ],
    )
    def test_malformed_numeric_hosts_fail_closed(self, url):
        ok, reason = is_safe_external_url(url)
        assert not ok
        assert "malformed" in reason

    def test_mapped_ipv6_loopback_blocked(self, monkeypatch):
        # "::ffff:127.0.0.1" is an IPv6 literal → resolver stub NXDOMAINs it,
        # so pin the guard at the resolution-result level instead.
        assert _ip_is_dangerous("::ffff:127.0.0.1") is True
        assert _ip_is_dangerous("::1") is True

    @pytest.mark.parametrize(
        "addr",
        [
            "2002:7f00:1::",        # 6to4 embedding 127.0.0.1
            "2002:a9fe:c9fe::",     # 6to4 embedding 169.254.201.254
            "2001:0:0:0:0:0:7f00:1",  # Teredo embedding loopback
            "192.88.99.1",          # 6to4 relay anycast
        ],
    )
    def test_ipv6_transition_ranges_blocked(self, addr):
        assert _ip_is_dangerous(addr) is True

    def test_trailing_dot_metadata_host_blocked(self):
        ok, reason = is_safe_external_url("https://metadata.google.internal./x")
        assert not ok
        assert reason


# ---------------------------------------------------------------------------
# Public literals and real names stay allowed
# ---------------------------------------------------------------------------

class TestAllowed:
    def test_public_ipv4_literal_allowed_dns_free(self, monkeypatch):
        def explode(host, port, *a, **kw):
            raise AssertionError(f"DNS consulted for literal host: {host!r}")

        monkeypatch.setattr(socket, "getaddrinfo", explode)
        ok, reason = is_safe_external_url("http://93.184.216.34/x")
        assert ok, reason

    def test_public_name_resolving_to_public_ip(self):
        ok, reason = is_safe_external_url("https://api.openai.example.com/v1")
        assert ok, reason

    def test_unresolvable_name_blocked(self):
        ok, reason = is_safe_external_url("http://no-such-host.invalid/x")
        assert not ok
        assert "resolve" in reason

    def test_bare_hex_word_is_a_dns_name_not_a_literal(self):
        """inet_aton treats a bare ``deadbeef`` as a name (no 0x prefix);
        the guard must match that semantic, not silently parse it as hex."""
        assert _canonicalize_numeric_host("deadbeef") is None


# ---------------------------------------------------------------------------
# Canonicalizer unit behavior
# ---------------------------------------------------------------------------

class TestCanonicalizeNumericHost:
    @pytest.mark.parametrize(
        "host,expected",
        [
            ("2130706433", "127.0.0.1"),
            ("0x7f000001", "127.0.0.1"),
            ("0177.0.0.1", "127.0.0.1"),
            ("0x7f.0.0.1", "127.0.0.1"),
            ("127.1", "127.0.0.1"),
            ("127.0.0x1", "127.0.0.1"),
            ("8.8.8.8", "8.8.8.8"),
            ("1.2", "1.0.0.2"),
            ("1.2.3", "1.2.0.3"),
        ],
    )
    def test_parses(self, host, expected):
        assert str(_canonicalize_numeric_host(host)) == expected

    @pytest.mark.parametrize(
        "host",
        ["api.openai.com", "deadbeef", "example.com", "1e5", "host-01", ""],
    )
    def test_non_numeric_returns_none(self, host):
        assert _canonicalize_numeric_host(host) is None

    @pytest.mark.parametrize(
        "host",
        ["4294967296", "999.1.1.1", "127..0.1", "1.2.3.4.5", ".1.2.3", "0x", "0xzz"],
    )
    def test_malformed_raises(self, host):
        with pytest.raises(ValueError):
            _canonicalize_numeric_host(host)
