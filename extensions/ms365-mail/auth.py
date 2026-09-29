"""Microsoft 365 Mailbox — app-only access tokens from Microsoft Entra.

The OAuth 2.0 client-credentials grant is one form POST to
``{authority}/{tenant}/oauth2/v2.0/token``; this module makes it over plain
``httpx`` and caches the answer, so no auth library is needed.

The secret is sent only in the POST body, which is never logged, and it never
appears in an error message: errors are built from Azure's
``error_description`` (the ``AADSTS…`` text), with the secret redacted in the
unlikely case the text echoes it.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import quote

import httpx

_TOKEN_PATH = "oauth2/v2.0/token"
_REDACTED = "[secret]"


class GraphAuthError(Exception):
    """Getting a token failed; the message is Azure's description (never the secret).

    Args:
        message: Azure's ``error_description`` (secret redacted), or what was
            wrong with the answer.
        codes: Azure's numeric ``error_codes`` (the ``AADSTS`` numbers), for
            matching without reading the description. Empty when the failure
            did not come with any (a malformed answer, or a body without the
            array).
    """

    def __init__(self, message: str, codes: tuple[int, ...] = ()) -> None:
        super().__init__(message)
        self.codes = codes


class GraphUnreachable(Exception):
    """Microsoft's endpoint could not be reached (DNS, TLS, timeout, reset)."""


@dataclass(frozen=True)
class AccessToken:
    """An app-only bearer token.

    Attributes:
        token: The bearer token.
        expires_at: When it expires, in ``clock()`` seconds.
    """

    token: str
    expires_at: float


class TokenProvider(Protocol):
    """Hands out app-only tokens for one set of credentials."""

    def acquire(self, tenant_id: str, client_id: str, secret: str) -> AccessToken:
        """Return a token that is valid for at least the refresh margin.

        Raises:
            GraphAuthError: Azure refused, or answered something unusable.
            GraphUnreachable: The token endpoint could not be reached.
        """
        ...


class RestTokenProvider:
    """Client-credentials tokens over REST, cached per credential triple.

    Args:
        client: The HTTP client (its timeout and transport apply).
        authority: e.g. ``https://login.microsoftonline.com`` (manifest default).
        scope: e.g. ``https://graph.microsoft.com/.default`` (manifest default).
        refresh_margin_s: A cached token is replaced this long before it expires.
        clock: Seconds; injectable for tests.
    """

    def __init__(
        self,
        client: httpx.Client,
        authority: str,
        scope: str,
        refresh_margin_s: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._authority = authority.rstrip("/")
        self._scope = scope
        self._margin = refresh_margin_s
        self._clock = clock
        self._cache: dict[tuple[str, str, str], AccessToken] = {}
        self._lock = threading.Lock()

    def acquire(self, tenant_id: str, client_id: str, secret: str) -> AccessToken:
        """Return a cached token, or POST for a new one once the margin is reached.

        Raises:
            GraphAuthError: A non-2xx answer (Azure's ``error_description``
                and ``error_codes``), a body that is not JSON, or a 2xx
                without ``access_token`` or ``expires_in``.
            GraphUnreachable: Transport failure or timeout.
        """
        key = (tenant_id, client_id, hashlib.sha256(secret.encode("utf-8")).hexdigest())
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and self._clock() < cached.expires_at - self._margin:
                return cached
            token = self._request(tenant_id, client_id, secret)
            self._cache[key] = token
            return token

    def _request(self, tenant_id: str, client_id: str, secret: str) -> AccessToken:
        url = f"{self._authority}/{quote(tenant_id, safe='')}/{_TOKEN_PATH}"
        started = self._clock()
        try:
            response = self._client.post(url, data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
                "scope": self._scope,
            })
        except httpx.HTTPError as exc:
            raise GraphUnreachable(_redact(f"token endpoint: {exc}", secret)) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphAuthError(f"token endpoint returned HTTP {response.status_code} with a body that is not JSON") from exc
        if not isinstance(payload, dict):
            raise GraphAuthError(f"token endpoint returned HTTP {response.status_code} with an unexpected body")
        if not response.is_success:
            description = payload.get("error_description") or payload.get("error")
            if not isinstance(description, str) or not description.strip():
                raise GraphAuthError(f"token endpoint returned HTTP {response.status_code} with no error description")
            raise GraphAuthError(_redact(description.strip(), secret), _error_codes(payload))
        access = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(access, str) or not access:
            raise GraphAuthError("token endpoint answered without an access_token")
        seconds = _seconds(expires_in)
        if seconds is None:
            raise GraphAuthError("token endpoint answered without a usable expires_in")
        return AccessToken(token=access, expires_at=started + seconds)


def _error_codes(payload: dict[str, object]) -> tuple[int, ...]:
    # Azure documents error_codes as a list of STS numbers; anything else is
    # not that list, so no code is claimed (the message still carries the text).
    raw = payload.get("error_codes")
    if not isinstance(raw, list):
        return ()
    return tuple(code for code in raw if isinstance(code, int) and not isinstance(code, bool))


def _seconds(value: object) -> int | None:
    # Azure's v2 endpoint sends an integer; a digit string is the v1 shape.
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, _REDACTED) if secret else text
