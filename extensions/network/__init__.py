"""Local Network Access — lets other devices on the local network open BossMod in a browser.

A command-less service extension (manifest ``service`` + ``panel`` +
``settings``). While it is on, the app process runs a second listener on the
chosen port, serving the same app behind the access code the operator chose
in Settings (see ``gate``). The extension's Status section shows the address
and the code; its wording lives in ``status``.

``create`` is inert: nothing binds a port, reads settings or touches the
network until the host calls ``start_service``.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.types import ASGIApp

from core.extensions.contract import ConfigError, ExtensionContext, ServiceStartError
from core.extensions.panel import PanelStatus

from . import status
from .access import LoginThrottle, canonical_code
from .addresses import lan_urls
from .gate import LanGateMiddleware
from .server import LanServer, bind_socket

logger = logging.getLogger(__name__)


class NetworkDefaults(BaseModel):
    """The manifest's ``defaults`` block, validated when the extension is created."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bind_host: str = Field(min_length=1)
    login_max_failures: int = Field(ge=1)
    login_lockout_seconds: int = Field(ge=1)
    stop_timeout_seconds: int = Field(ge=1)
    route_probe_address: str = Field(min_length=1)


@dataclass(frozen=True)
class _Running:
    """The live listener and the settings it was started with."""

    server: LanServer
    port: int
    code: str


class NetworkExtension:
    """The Local Network Access extension (see the module docstring).

    Args:
        ctx: The host context.
        defaults: The validated manifest tunables.
        platform: This computer's ``sys.platform`` value; it picks the
            firewall help the Status section shows.
    """

    def __init__(self, ctx: ExtensionContext, defaults: NetworkDefaults, platform: str) -> None:
        self._ctx = ctx
        self._defaults = defaults
        self._platform = platform
        self._throttle = LoginThrottle(defaults.login_max_failures, defaults.login_lockout_seconds)
        # panel() reads this from a worker thread while the loop starts and
        # stops the listener. It is replaced in one assignment, never edited,
        # so a reader always sees one consistent listener, port and code.
        self._running: _Running | None = None

    # ─── service ───

    async def start_service(self, app: ASGIApp) -> None:
        """Start the network listener on the configured port, behind the configured access code.

        Idempotent: does nothing when it is already running.

        Args:
            app: The app process's ASGI application.

        Raises:
            core.extensions.contract.SettingsIncompleteError: The access code
                was never set; the host tells the operator to fill it in.
            ServiceStartError: The port is unavailable (the message says why).
        """
        if self._running is not None:
            return
        settings = self._ctx.read_settings()
        port = int(settings["port"])
        code = settings["access_code"]
        gate = LanGateMiddleware(
            app,
            code=code,
            throttle=self._throttle,
            protected_prefix=f"/api/extensions/{self._ctx.manifest.id}",
        )
        server = LanServer(
            gate, host=self._defaults.bind_host, port=port, stop_timeout_seconds=self._defaults.stop_timeout_seconds,
        )
        await server.start()
        self._running = _Running(server=server, port=port, code=code)
        logger.info("Network access is listening on port %d", port)

    async def stop_service(self) -> None:
        """Stop the listener. Idempotent."""
        running, self._running = self._running, None
        if running is None:
            return
        await running.server.stop()
        logger.info("Network access stopped")

    def shutdown(self) -> None:
        """Release what the extension holds.

        The listener is stopped by ``stop_service`` (the host calls it first);
        this holds nothing else, so a listener still running here means the
        host skipped it, which is logged rather than hidden.
        """
        if self._running is not None:
            logger.warning("Network access was shut down while its listener was still running")

    # ─── panel ───

    def panel(self) -> PanelStatus:
        """Return the Status section: not running, no network, or how another device connects (see ``status``)."""
        running = self._running
        if running is None:
            return status.stopped()
        urls = lan_urls(running.port, self._defaults.route_probe_address)
        if not urls:
            return status.offline()
        return status.running(urls[0], running.code, running.port, self._platform)

    # ─── settings ───

    def verify_settings(self, values: Mapping[str, str]) -> str:
        """Check that the chosen port is free before it is stored, and say what the save changes.

        The port this extension is already serving on counts as available to
        itself, so saving without changing it does not fail.

        Args:
            values: Every declared field's value (``port``, ``access_code``).

        Returns:
            A one-line success detail: "Port N is available." when it is not
            running or the port changed; then "Signed-in devices will need
            the new code." when it is running and the code changed (case
            aside); "Saved." when neither applies.

        Raises:
            ConfigError: The port is in use or not allowed.
        """
        port = int(values["port"])
        running = self._running
        details: list[str] = []
        if running is None or port != running.port:
            try:
                bind_socket(self._defaults.bind_host, port).close()
            except ServiceStartError as exc:
                raise ConfigError(str(exc)) from exc
            details.append(f"Port {port} is available.")
        if running is not None and canonical_code(values["access_code"]) != canonical_code(running.code):
            details.append("Signed-in devices will need the new code.")
        return " ".join(details) if details else "Saved."


def create(ctx: ExtensionContext) -> NetworkExtension:
    """Create the extension (inert: no settings, port or thread is touched).

    Raises:
        ValueError: The manifest's ``defaults`` are not valid.
    """
    try:
        defaults = NetworkDefaults.model_validate(ctx.manifest.defaults)
    except ValidationError as exc:
        raise ValueError(f"network manifest defaults are invalid: {exc}") from exc
    return NetworkExtension(ctx, defaults, platform=sys.platform)
