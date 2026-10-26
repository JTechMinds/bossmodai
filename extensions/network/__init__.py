"""Local Network Access — lets other devices on the local network open BossMod in a browser.

A command-less service extension (manifest ``service`` + ``panel`` +
``settings``). While it is on, the app process runs a second listener on the
chosen port, serving the same app behind an access-code gate (see ``gate``).
The operator sees the address and the access code on the extension's card.

``create`` is inert: nothing binds a port, reads a file or touches the
network until the host calls ``start_service``.
"""

from __future__ import annotations

import logging
import threading
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.types import ASGIApp

from core.extensions.contract import ConfigError, ExtensionContext, PanelError, ServiceStartError
from core.extensions.panel import PanelAction, PanelFact, PanelStatus

from .access import AccessCodeStore, LoginThrottle
from .addresses import lan_urls
from .gate import LanGateMiddleware
from .server import LanServer, bind_socket

logger = logging.getLogger(__name__)

_CODE_FILE = "access_code"
_NEW_CODE_ACTION = "new_code"


class NetworkDefaults(BaseModel):
    """The manifest's ``defaults`` block, validated when the extension is created."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bind_host: str = Field(min_length=1)
    login_max_failures: int = Field(ge=1)
    login_lockout_seconds: int = Field(ge=1)
    code_length: int = Field(ge=4)
    stop_timeout_seconds: int = Field(ge=1)
    route_probe_address: str = Field(min_length=1)


class NetworkExtension:
    """The Local Network Access extension (see the module docstring).

    Args:
        ctx: The host context.
        defaults: The validated manifest tunables.
    """

    def __init__(self, ctx: ExtensionContext, defaults: NetworkDefaults) -> None:
        self._ctx = ctx
        self._defaults = defaults
        self._store = AccessCodeStore(ctx.data_dir / _CODE_FILE, ctx.seal, ctx.unseal, defaults.code_length)
        self._throttle = LoginThrottle(defaults.login_max_failures, defaults.login_lockout_seconds)
        # The code is read by the listener on every request (loop thread) and
        # replaced by a panel action (a worker thread).
        self._code_lock = threading.Lock()
        self._code: str | None = None
        self._server: LanServer | None = None
        self._port: int | None = None

    # ─── service ───

    async def start_service(self, app: ASGIApp) -> None:
        """Start the network listener on the configured port.

        Idempotent: does nothing when it is already running.

        Args:
            app: The app process's ASGI application.

        Raises:
            ServiceStartError: The port is unavailable (the message says why).
            OSError: The access code file cannot be read or written.
        """
        if self._server is not None:
            return
        port = int(self._ctx.read_settings()["port"])
        self._ensure_code()
        gate = LanGateMiddleware(
            app,
            code_provider=self._current_code,
            throttle=self._throttle,
            protected_prefix=f"/api/extensions/{self._ctx.manifest.id}",
        )
        server = LanServer(
            gate, host=self._defaults.bind_host, port=port, stop_timeout_seconds=self._defaults.stop_timeout_seconds,
        )
        await server.start()
        self._server, self._port = server, port
        logger.info("Network access is listening on port %d", port)

    async def stop_service(self) -> None:
        """Stop the listener. Idempotent."""
        server, self._server, self._port = self._server, None, None
        if server is None:
            return
        await server.stop()
        logger.info("Network access stopped")

    def shutdown(self) -> None:
        """Release what the extension holds.

        The listener is stopped by ``stop_service`` (the host calls it first);
        this holds nothing else, so a listener still running here means the
        host skipped it, which is logged rather than hidden.
        """
        if self._server is not None:
            logger.warning("Network access was shut down while its listener was still running")

    # ─── panel ───

    def panel(self) -> PanelStatus:
        """Return the card's status: address, access code and the new-code action while running."""
        if self._server is None or self._port is None:
            return PanelStatus(message="Network access isn’t running.")
        urls = lan_urls(self._port, self._defaults.route_probe_address)
        facts = [PanelFact(label="Address", value=url, copyable=True) for url in urls]
        facts.append(PanelFact(label="Access code", value=self._current_code(), copyable=True, secret=True))
        message = (
            "Other devices can open BossMod at the address below. Use this only on a network you trust: "
            "the connection isn’t encrypted."
            if urls else
            "No network connection found. Connect this computer to a network to share BossMod."
        )
        return PanelStatus(
            message=message,
            facts=facts,
            actions=[PanelAction(key=_NEW_CODE_ACTION, label="Generate new code")],
        )

    def panel_action(self, key: str, values: Mapping[str, str]) -> None:
        """Run a panel action.

        ``new_code`` replaces the access code, which signs every device out.

        Raises:
            PanelError: The action is unknown, or the new code could not be saved.
        """
        if key != _NEW_CODE_ACTION:
            raise PanelError("That action isn’t available.")
        try:
            with self._code_lock:
                self._code = self._store.rotate()
        except OSError as exc:
            logger.error("Could not save a new network access code", exc_info=True)
            raise PanelError(f"Couldn’t save the new code: {exc.strerror or exc}.") from exc
        logger.info("A new network access code was generated")

    # ─── settings ───

    def verify_settings(self, values: Mapping[str, str]) -> str:
        """Check that the chosen port is free before it is stored.

        The port this extension is already serving on counts as available to
        itself, so saving without changing it does not fail.

        Returns:
            A one-line success detail.

        Raises:
            ConfigError: The port is in use or not allowed.
        """
        port = int(values["port"])
        if self._server is not None and port == self._port:
            return f"Keeping port {port}."
        try:
            bind_socket(self._defaults.bind_host, port).close()
        except ServiceStartError as exc:
            raise ConfigError(str(exc)) from exc
        return f"Port {port} is available."

    # ─── code ───

    def _ensure_code(self) -> None:
        with self._code_lock:
            if self._code is None:
                self._code = self._store.load_or_create()

    def _current_code(self) -> str:
        with self._code_lock:
            if self._code is None:
                raise RuntimeError("the access code was read before the service started")
            return self._code


def create(ctx: ExtensionContext) -> NetworkExtension:
    """Create the extension (inert: no file, port or thread is touched).

    Raises:
        ValueError: The manifest's ``defaults`` are not valid.
    """
    try:
        defaults = NetworkDefaults.model_validate(ctx.manifest.defaults)
    except ValidationError as exc:
        raise ValueError(f"network manifest defaults are invalid: {exc}") from exc
    return NetworkExtension(ctx, defaults)
