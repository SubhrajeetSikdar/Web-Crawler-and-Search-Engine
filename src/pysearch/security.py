"""SSRF protection: decide whether a URL's host is a public internet address."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_BLOCKED_NAMES = frozenset({"localhost", "metadata.google.internal", "metadata"})
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")
# Legacy numeric IPv4 spellings (2130706433, 0x7f.1, 017700000001) that resolvers accept
# but ipaddress does not parse. They have no legitimate use in a crawl, so reject them.
_NUMERIC_HOST = re.compile(r"(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+))*")


def is_public_ip(address: str) -> bool:
    """True only for globally routable unicast addresses.

    Rejects loopback, RFC 1918 private ranges, link-local (incl. the 169.254.169.254
    cloud metadata endpoint), CGNAT, multicast, reserved and unspecified addresses.
    """
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def host_is_public(host: str | None) -> bool:
    """Static check of a hostname or IP literal (no DNS lookups)."""
    if not host:
        return False
    host = host.strip("[]").lower().rstrip(".")
    try:
        return is_public_ip(host)
    except ValueError:
        pass
    if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES):
        return False
    if _NUMERIC_HOST.fullmatch(host):
        return False
    return "." in host  # single-label names are intranet hosts


def url_is_public(url: str) -> bool:
    try:
        return host_is_public(urlsplit(url).hostname)
    except ValueError:
        return False
