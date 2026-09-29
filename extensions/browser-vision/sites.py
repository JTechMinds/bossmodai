"""Browser Vision — which site a URL belongs to, for pacing and cooldowns.

Every agent shares the operator's IP, so polite pacing (R33) and bot-check
cooldowns (R34) are kept per site, not per agent. A site key is the URL's
host, lower-cased, without a leading ``www.``; a host belongs to a key when
it equals it or is a subdomain of it (``ratelimited.redfin.com`` belongs to
``redfin.com``).
"""

from __future__ import annotations

from typing import Iterable
from urllib.parse import urlsplit

_WWW = "www."


def site_key(url: str) -> str | None:
    """Return the site key of ``url``, or ``None`` when it has no host.

    ``about:``, ``data:`` and ``file:`` URLs have no host: they reach no
    site, so there is nothing to pace or cool down.
    """
    host = urlsplit(url).hostname
    if not host:
        return None
    host = host.lower()
    return host[len(_WWW):] if host.startswith(_WWW) else host


def belongs_to(key: str, site: str) -> bool:
    """Return whether site key ``key`` is ``site`` or one of its subdomains."""
    return key == site or key.endswith(f".{site}")


def matching_site(key: str, sites: Iterable[str]) -> str | None:
    """Return the broadest of ``sites`` that ``key`` belongs to, or ``None``.

    The broadest (shortest) wins so a subdomain and its parent resolve to
    the same entry whichever was recorded first under the parent.
    """
    found = [site for site in sites if belongs_to(key, site)]
    return min(found, key=len) if found else None
