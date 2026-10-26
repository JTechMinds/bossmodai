"""Local Network Access — the address other devices on the local network should type."""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Callable

logger = logging.getLogger(__name__)

# A UDP connect() sends nothing; the port only has to be a valid one.
_PROBE_PORT = 9


def outbound_ipv4(probe_address: str) -> str:
    """Return this computer's source address for reaching ``probe_address``.

    Opens a UDP socket and connect()s it, which only asks the OS which
    interface it would route through; no packet is sent.

    Raises:
        OSError: There is no route (no network connection).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((probe_address, _PROBE_PORT))
        return sock.getsockname()[0]


def lan_urls(
    port: int,
    probe_address: str,
    probe: Callable[[str], str] = outbound_ipv4,
) -> list[str]:
    """Return the ``http://`` address other devices can type to reach this computer.

    Only the primary outbound IPv4 address: listing every interface would
    show the virtual bridges Docker and VM tools create, which no other
    device can use, next to the one address that matters.

    Args:
        port: The port the listener serves on.
        probe_address: A routable address that is never contacted, used only
            to find which interface the OS would send through.
        probe: Maps ``probe_address`` to this computer's source address;
            injected so tests need no network.

    Returns:
        A one-item list, or an empty list when there is no usable network
        connection (no route, or a loopback, link-local or unspecified
        result); the caller says so rather than guessing an address.
    """
    try:
        found = probe(probe_address)
    except OSError as exc:
        logger.debug("No outbound route found via %s: %s", probe_address, exc)
        return []
    address = ipaddress.ip_address(found)
    if address.is_loopback or address.is_link_local or address.is_unspecified:
        logger.debug("The outbound address %s is not reachable from another device", address)
        return []
    return [f"http://{address}:{port}"]
