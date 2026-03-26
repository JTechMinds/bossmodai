"""BossMod AI — Coordinated runtime service lifecycle."""

from __future__ import annotations

import asyncio

import db
from core import config
from core.agent_loop.dispatcher import dispatcher
from core.agent_loop.watchdog import watchdog
from core.world.simulation import simulation


class RuntimeServices:
    """Own start/stop/reset for the long-running runtime services."""

    _STATE_KEY = "runtime_control_state"
    _RUNNING = "running"
    _PAUSED = "paused"

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    def start(self) -> None:
        """Start all runtime services."""
        if self.is_paused():
            return
        dispatcher.start()
        simulation.start()
        watchdog.start()

    async def stop(self) -> None:
        """Stop all runtime services and await cancellation."""
        async with self._lock:
            await self._stop_unlocked()

    async def reseed_application_data(self) -> None:
        """Recreate the application database from the current schema and restart services."""
        async with self._lock:
            await self._stop_unlocked()
            db.reset_database()
            config.reload()
            self.start()

    def state(self) -> str:
        """Return the persisted global runtime state."""
        state = config.get(self._STATE_KEY)
        if state == self._PAUSED:
            return self._PAUSED
        return self._RUNNING

    def is_paused(self) -> bool:
        """Return whether the global runtime kill switch is engaged."""
        return self.state() == self._PAUSED

    def status_payload(self) -> dict[str, object]:
        """Return a small UI-friendly runtime state payload."""
        state = self.state()
        return {
            "state": state,
            "paused": state == self._PAUSED,
        }

    async def pause(self) -> dict[str, object]:
        """Persist the global paused state and stop runtime services."""
        async with self._lock:
            db.set_setting(self._STATE_KEY, self._PAUSED, "advanced")
            config.reload()
            await self._stop_unlocked()
            return self.status_payload()

    async def resume(self) -> dict[str, object]:
        """Persist the global running state and restart runtime services."""
        async with self._lock:
            db.set_setting(self._STATE_KEY, self._RUNNING, "advanced")
            config.reload()
            self.start()
            return self.status_payload()

    async def _stop_unlocked(self) -> None:
        """Stop services in a fixed order while holding the lifecycle lock."""
        await watchdog.stop()
        await dispatcher.stop()
        await simulation.stop()


runtime_services = RuntimeServices()
