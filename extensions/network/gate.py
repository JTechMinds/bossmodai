"""Local Network Access — the access gate in front of the network listener.

The BossMod app hands its API token to anyone who loads ``/``, which is fine
on the loopback listener and not on a network one. This ASGI middleware wraps
the app for the network listener only and lets nothing through without the
``bossmod_lan`` session cookie, which the login page sets in return for the
access code. After login the app's own API token still gates ``/api``.
"""

from __future__ import annotations

import logging
from html import escape
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from string import Template
from typing import Callable
from urllib.parse import parse_qs

from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from .access import LoginThrottle, codes_match, session_matches, session_value

logger = logging.getLogger(__name__)

LOGIN_PATH = "/lan-login"
STYLE_PATH = "/lan-login/style.css"
COOKIE_NAME = "bossmod_lan"
_STATIC_PREFIX = "/static/"
# The login form is one short field; anything bigger is not a person typing.
_MAX_LOGIN_BODY = 4096
_WS_POLICY_VIOLATION = 1008

_FOLDER = Path(__file__).resolve().parent

COPY = {
    "wrong": "That code isn’t right. Check it and try again.",
    "locked": "Too many wrong codes. Wait {wait} before trying again.",
    "unauthorized": "Sign in with the access code first.",
    "host_only": "This can only be changed on the computer running BossMod.",
    "too_large": "That request was too large.",
}


def _wait_text(seconds: int) -> str:
    """Return a wait as people say it: "1 minute", "5 minutes", "30 seconds"."""
    if seconds >= 60:
        minutes = -(-seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


class LanGateMiddleware:
    """Require the network access code before anything of BossMod is served.

    Without a valid session: ``/lan-login`` (page and form), its stylesheet
    and ``/static/*`` are served; page requests redirect to the login page,
    ``/api`` requests get 401 and WebSocket upgrades are closed. With one,
    requests pass to the app, except those under ``protected_prefix``, which
    only the machine running BossMod may use.

    Args:
        app: The app process's ASGI application.
        code_provider: Returns the current access code; read on every request
            so a rotated code takes effect at once.
        throttle: Locks out a client after repeated wrong codes.
        protected_prefix: A path prefix refused to every session on this
            listener (the extension's own enable, settings and panel routes).
        folder: Where ``login.html`` and ``login.css`` are read from.

    Raises:
        OSError: The login page files cannot be read.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        code_provider: Callable[[], str],
        throttle: LoginThrottle,
        protected_prefix: str,
        folder: Path = _FOLDER,
    ) -> None:
        self.app = app
        self._code = code_provider
        self._throttle = throttle
        self._protected = protected_prefix
        self._page = Template((folder / "login.html").read_text(encoding="utf-8"))
        self._style = (folder / "login.css").read_text(encoding="utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    # ─── helpers ───

    def _signed_in(self, scope: Scope) -> bool:
        return session_matches(_cookie(scope, COOKIE_NAME), self._code())

    def _login_page(self, error: str | None, status: int = 200) -> Response:
        block = (
            '<div class="callout" data-tone="alert" role="alert"><p class="callout-body">'
            f"{escape(error, quote=True)}</p></div>"
        ) if error else ""
        return HTMLResponse(
            self._page.substitute(error_block=block), status_code=status,
            headers={"Cache-Control": "no-store"},
        )

    def _under_protected_prefix(self, path: str) -> bool:
        return path == self._protected or path.startswith(self._protected + "/")

    # ─── http ───

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path") or ""
        method = scope["method"]
        if path == LOGIN_PATH:
            await self._login(scope, receive, send, method)
            return
        if path == STYLE_PATH:
            await Response(self._style, media_type="text/css")(scope, receive, send)
            return
        if path.startswith(_STATIC_PREFIX):
            await self.app(scope, receive, send)
            return
        if not self._signed_in(scope):
            if path == "/api" or path.startswith("/api/"):
                response: Response = JSONResponse({"detail": COPY["unauthorized"]}, status_code=401)
            else:
                response = RedirectResponse(LOGIN_PATH, status_code=303)
            await response(scope, receive, send)
            return
        if self._under_protected_prefix(path):
            await JSONResponse({"detail": COPY["host_only"]}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _login(self, scope: Scope, receive: Receive, send: Send, method: str) -> None:
        if method == "GET" or method == "HEAD":
            response = RedirectResponse("/", status_code=303) if self._signed_in(scope) else self._login_page(None)
        elif method == "POST":
            response = await self._submit(scope, receive)
        else:
            response = PlainTextResponse("Method not allowed", status_code=405, headers={"Allow": "GET, HEAD, POST"})
        await response(scope, receive, send)

    async def _submit(self, scope: Scope, receive: Receive) -> Response:
        client = (scope.get("client") or ("unknown", 0))[0]
        wait = self._throttle.retry_after(client)
        if wait:
            return self._locked(wait)
        body = await _read_body(receive, _MAX_LOGIN_BODY)
        if body is None:
            return PlainTextResponse(COPY["too_large"], status_code=413)
        typed = parse_qs(body.decode("utf-8", errors="replace")).get("code", [""])[0]
        code = self._code()
        if codes_match(typed, code):
            self._throttle.record_success(client)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE_NAME, session_value(code), httponly=True, samesite="strict", path="/")
            logger.info("Network sign-in from %s", client)
            return response
        self._throttle.record_failure(client)
        logger.warning("Wrong network access code from %s", client)
        wait = self._throttle.retry_after(client)
        if wait:
            return self._locked(wait)
        return self._login_page(COPY["wrong"], status=401)

    def _locked(self, wait: int) -> Response:
        page = self._login_page(COPY["locked"].format(wait=_wait_text(wait)), status=429)
        page.headers["Retry-After"] = str(wait)
        return page

    # ─── websocket ───

    async def _websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._signed_in(scope):
            await WebSocket(scope, receive, send).close(code=_WS_POLICY_VIOLATION)
            return
        await self.app(scope, receive, send)


def _cookie(scope: Scope, name: str) -> str | None:
    """Return one cookie's value from a request scope, or ``None`` when absent or unparseable."""
    for header, value in scope.get("headers") or []:
        if header != b"cookie":
            continue
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(value.decode("latin-1"))
        except CookieError:
            return None
        morsel = jar.get(name)
        return morsel.value if morsel is not None else None
    return None


async def _read_body(receive: Receive, limit: int) -> bytes | None:
    """Read a request body; ``None`` when it exceeds ``limit`` bytes."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            # A disconnect: nothing more will come.
            return b"".join(chunks)
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)
