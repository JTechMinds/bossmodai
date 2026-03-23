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

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    def start(self) -> None:
        """Start all runtime services."""
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

    async def _stop_unlocked(self) -> None:
        """Stop services in a fixed order while holding the lifecycle lock."""
        await watchdog.stop()
        await dispatcher.stop()
        await simulation.stop()


runtime_services = RuntimeServices()
