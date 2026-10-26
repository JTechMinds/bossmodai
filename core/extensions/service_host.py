"""BossMod AI — the app process's host for extension services (manifest ``service``).

An extension with ``service: true`` runs something long-lived in the app
process (a listener, a poller) while the operator has it on. The runtime
worker has ``agent_hooks`` for its side of an extension; this is the app
process's counterpart. One idempotent :meth:`ExtensionServiceHost.reconcile`
makes what is running match the enabled set: the API calls it after the
enable toggle, the lifespan calls it at boot, and :meth:`stop_all` runs at
shutdown. Failures are recorded for the card, never swallowed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from starlette.types import ASGIApp

from core.extensions.contract import ServiceStartError, SupportsService
from core.extensions.loader import ExtensionLoadError, load_extension, unload_extension
from core.extensions.registry import Discovery, ExtensionEntry, enabled_ids, get_discovery

logger = logging.getLogger(__name__)


class ExtensionServiceHost:
    """Starts and stops enabled ``service`` extensions in this process.

    Args:
        discovery: Returns this process's extension discovery.
        enabled: Returns the ids the operator has on, read live. May raise
            :class:`core.extensions.registry.ExtensionSettingError`, which
            propagates: a corrupt enabled set is not something to guess past.
    """

    def __init__(
        self,
        discovery: Callable[[], Discovery] = get_discovery,
        enabled: Callable[[], frozenset[str]] = enabled_ids,
    ) -> None:
        self._discovery = discovery
        self._enabled = enabled
        self._app: ASGIApp | None = None
        self._running: dict[str, SupportsService] = {}
        self._errors: dict[str, str] = {}
        # Serialises every start/stop: the enable route, a settings-driven
        # restart and shutdown can overlap, and a service must never be
        # started twice or stopped half-way by another caller.
        self._lock = asyncio.Lock()

    def attach(self, app: ASGIApp) -> None:
        """Hand the host the ASGI app each service receives in ``start_service``."""
        self._app = app

    def error(self, ext_id: str) -> str | None:
        """Return why the extension's service is not running, or ``None`` when it is, or is off."""
        return self._errors.get(ext_id)

    def is_running(self, ext_id: str) -> bool:
        """Return whether the extension's service is running now."""
        return ext_id in self._running

    async def reconcile(self) -> dict[str, str]:
        """Make the running services match the enabled set.

        Starts every enabled, valid ``service`` extension that is not running
        and stops every running one that is no longer enabled (``stop_service``,
        then ``shutdown``, then unload). Safe to call any number of times.

        Returns:
            ``{extension id: operator-safe message}`` for each start that
            failed in this pass. A failed start stays recorded (see
            :meth:`error`) until a later pass starts it or it is turned off.

        Raises:
            RuntimeError: A service needs starting but :meth:`attach` has
                not been called.
            core.extensions.registry.ExtensionSettingError: The enabled set
                is unreadable.
        """
        async with self._lock:
            return await self._reconcile_locked()

    async def restart(self, ext_id: str) -> str | None:
        """Stop the extension's service if it runs, then start it again with fresh settings.

        Does nothing when the extension is not an enabled, valid service.

        Returns:
            The start failure's message, or ``None`` when it started (or
            there was nothing to start).

        Raises:
            RuntimeError: The service needs starting but :meth:`attach` has
                not been called.
            core.extensions.registry.ExtensionSettingError: The enabled set
                is unreadable.
        """
        async with self._lock:
            if ext_id in self._running:
                await self._stop(ext_id)
            entry = self._desired().get(ext_id)
            if entry is None:
                return None
            return await self._start(entry)

    async def stop_all(self) -> None:
        """Stop every running service (process shutdown). Failures are logged; every service is attempted."""
        async with self._lock:
            for ext_id in list(self._running):
                await self._stop(ext_id)

    def _desired(self) -> dict[str, ExtensionEntry]:
        enabled = self._enabled()
        return {
            entry.id: entry
            for entry in self._discovery().valid_entries()
            if entry.manifest.service and entry.id in enabled
        }

    async def _reconcile_locked(self) -> dict[str, str]:
        desired = self._desired()
        for ext_id in [ext_id for ext_id in self._running if ext_id not in desired]:
            await self._stop(ext_id)
        # A turned-off extension has no failure to show.
        for ext_id in [ext_id for ext_id in self._errors if ext_id not in desired]:
            del self._errors[ext_id]
        failed: dict[str, str] = {}
        for ext_id, entry in desired.items():
            if ext_id in self._running:
                continue
            message = await self._start(entry)
            if message is not None:
                failed[ext_id] = message
        return failed

    async def _start(self, entry: ExtensionEntry) -> str | None:
        """Start one service; record and return its failure message, else clear it and return ``None``."""
        if self._app is None:
            raise RuntimeError("ExtensionServiceHost.attach(app) must be called before starting a service")
        instance: SupportsService | None = None
        try:
            # Loading imports the package, which can block.
            instance = await asyncio.to_thread(load_extension, entry)
            await instance.start_service(self._app)
        except ServiceStartError as exc:
            message = str(exc)
        except ExtensionLoadError as exc:
            logger.error("Extension %s could not load to start its service: %s", entry.id, exc)
            message = str(exc)
        except Exception:
            logger.exception("Extension %s failed to start its service", entry.id)
            message = f"Couldn’t start {entry.manifest.name}. The details are in the log."
        else:
            self._running[entry.id] = instance
            self._errors.pop(entry.id, None)
            logger.info("Started the service of extension %s", entry.id)
            return None
        if instance is not None:
            # start_service may have taken something before it failed.
            await self._release(entry.id, instance)
        self._errors[entry.id] = message
        logger.error("Service of extension %s is not running: %s", entry.id, message)
        return message

    async def _stop(self, ext_id: str) -> None:
        instance = self._running.pop(ext_id)
        await self._release(ext_id, instance)
        logger.info("Stopped the service of extension %s", ext_id)

    async def _release(self, ext_id: str, instance: SupportsService) -> None:
        """Stop an instance's service, shut it down and unload it; log, never raise, each step's failure.

        An instance whose stop or shutdown failed is in an unknown state, so
        it is unloaded either way and the next start creates a fresh one.
        """
        try:
            await instance.stop_service()
        except Exception:
            logger.exception("Extension %s failed to stop its service", ext_id)
        try:
            await asyncio.to_thread(instance.shutdown)
        except Exception:
            logger.exception("Extension %s failed to shut down", ext_id)
        unload_extension(ext_id)


extension_services = ExtensionServiceHost()
