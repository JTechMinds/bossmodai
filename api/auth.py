"""BossMod AI — Local API token gate for the localhost desktop app.

A settings-backed token is generated on first run and required on /api
REST routes (header) and the WebSocket (query or header). The desktop UI
receives the token via the index page and attaches it automatically.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from core import config
import db

logger = logging.getLogger(__name__)

LOCAL_API_TOKEN_KEY = "local_api_token"
LOCAL_API_TOKEN_HEADER = "X-BossMod-Token"
LOCAL_API_TOKEN_ENV = "BOSSMOD_LOCAL_API_TOKEN"
_UNAUTHORIZED_DETAIL = "Missing or invalid local API token"


def ensure_local_api_token() -> str:
    """Return the local API token, generating one if missing.

    ``BOSSMOD_LOCAL_API_TOKEN`` overrides the stored setting when set
    (useful for tests and scripted clients).
    """
    env_token = (os.environ.get(LOCAL_API_TOKEN_ENV) or "").strip()
    if env_token:
        return env_token
    return db.ensure_local_api_token()


def extract_token_from_headers(headers: Any) -> str | None:
    """Read the token from ``X-BossMod-Token`` or ``Authorization: Bearer``."""
    token = headers.get(LOCAL_API_TOKEN_HEADER.lower()) or headers.get("x-bossmod-token")
    if token:
        return str(token).strip() or None
    authorization = headers.get("authorization") or headers.get("Authorization")
    if authorization:
        raw = str(authorization).strip()
        prefix = "bearer "
        if raw.lower().startswith(prefix):
            return raw[len(prefix):].strip() or None
    return None


def extract_request_token(request: Request) -> str | None:
    return extract_token_from_headers(request.headers)


def extract_websocket_token(websocket: WebSocket) -> str | None:
    header_token = extract_token_from_headers(websocket.headers)
    if header_token:
        return header_token
    query_token = websocket.query_params.get("token")
    if query_token:
        return str(query_token).strip() or None
    return None


def tokens_match(provided: str | None, expected: str | None = None) -> bool:
    if not provided:
        return False
    actual = expected if expected is not None else ensure_local_api_token()
    if not actual:
        return False
    if len(provided) != len(actual):
        return False
    return secrets.compare_digest(provided, actual)


def request_authorized(request: Request) -> bool:
    return tokens_match(extract_request_token(request))


def websocket_authorized(websocket: WebSocket) -> bool:
    return tokens_match(extract_websocket_token(websocket))


def _header_map(scope: Scope) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers") or []:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        headers[name] = value
    return headers


class LocalApiTokenMiddleware:
    """Require the local API token on HTTP ``/api/*`` routes.

    WebSocket upgrades are left to the ``/api/ws`` endpoint so this
    middleware does not wrap the ASGI websocket cycle.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path") or ""
            if path.startswith("/api"):
                token = extract_token_from_headers(_header_map(scope))
                if not tokens_match(token):
                    response = JSONResponse({"detail": _UNAUTHORIZED_DETAIL}, status_code=401)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def install_local_api_auth(app: Any) -> None:
    """Attach the local API token middleware to a FastAPI/Starlette app."""
    app.add_middleware(LocalApiTokenMiddleware)
    try:
        token = ensure_local_api_token()
        cached = config.get(LOCAL_API_TOKEN_KEY)
        if cached != token and not (os.environ.get(LOCAL_API_TOKEN_ENV) or "").strip():
            config.reload()
    except Exception:
        # DB may not be initialised yet (import time). Lifespan / init_db will retry.
        logger.debug("Local API token not available yet; will generate on init_db", exc_info=True)
