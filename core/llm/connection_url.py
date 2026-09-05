"""Validate AI connection-test URLs (HA-SEC-NEW-01).

Allows https to public hosts and http/https to loopback (local Ollama /
LM Studio). Blocks link-local, metadata, and non-loopback http.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# AWS / GCP / Azure / Alibaba metadata and IPv4/IPv6 link-local.
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("100.100.100.200/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)

_METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.google.com",
    "metadata",
})


class ConnectionUrlError(ValueError):
    """Raised when a connection-test URL is not allowed."""


def validate_connection_test_url(raw: str) -> str:
    """Return a stripped base URL, or raise ``ConnectionUrlError``."""
    url = (raw or "").strip()
    if not url:
        raise ConnectionUrlError("URL is required")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ConnectionUrlError("URL must use http or https")
    if not parsed.hostname:
        raise ConnectionUrlError("URL must include a hostname")
    if parsed.username or parsed.password:
        raise ConnectionUrlError("URL must not include userinfo")

    host = parsed.hostname.lower().rstrip(".")
    if host in _METADATA_HOSTS:
        raise ConnectionUrlError("Metadata / link-local hosts are not allowed")

    ip = _parse_ip(host)
    loopback = _is_loopback_host(host, ip)
    if parsed.scheme == "http" and not loopback:
        raise ConnectionUrlError("http is only allowed for loopback (127.0.0.1, ::1, localhost)")

    if ip is not None and _is_blocked_ip(ip):
        raise ConnectionUrlError("Link-local and cloud-metadata IPs are not allowed")

    return url


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_loopback_host(
    host: str,
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None,
) -> bool:
    if host in {"localhost", "localhost.localdomain"}:
        return True
    return bool(ip and ip.is_loopback)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback:
        return False
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return True
    return any(ip in network for network in _BLOCKED_NETWORKS)
