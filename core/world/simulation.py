"""BossMod AI — World simulation tick loop.

Runs as a background asyncio task during the application lifespan.
Every tick (interval from ``tick_interval`` setting):
  1. Advance agents that are in transit (``steps_per_tick`` setting)
  2. Check activation triggers for idle agents
  3. Launch agent turns as concurrent tasks (non-blocking)

All timing and movement values come from the settings table.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from api.websocket import manager
from core import config
from core.agent_loop.activation import check_activation
from core.agent_loop.loop import run_turn
from core.world.tilemap import get_room_at
import db

logger = logging.getLogger(__name__)


class WorldSimulation:
    """Background world simulation loop."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._active_turns: dict[str, asyncio.Task[Any]] = {}
        self._agent_paths: dict[str, list[tuple[int, int]]] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the simulation loop as a background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("World simulation started")

    def stop(self) -> None:
        """Stop the simulation loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

        for task in self._active_turns.values():
            task.cancel()
        self._active_turns.clear()
        self._agent_paths.clear()

        logger.info("World simulation stopped")

    def set_agent_path(
        self,
        agent_id: str,
        path: list[tuple[int, int]],
    ) -> None:
        """Set a pathfinding route for an agent to follow.

        Skips the first element (current position).
        """
        self._agent_paths[agent_id] = path[1:] if path else []

    # ─── Main loop ───

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in simulation tick")

            # Read tick interval from settings each iteration
            interval = config.get_float("tick_interval") or 3.0
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        """Execute one simulation tick."""
        # Clean up finished agent turns
        finished = [
            aid for aid, task in self._active_turns.items()
            if task.done()
        ]
        for aid in finished:
            task = self._active_turns.pop(aid)
            if not task.cancelled() and task.exception():
                logger.error("Agent turn failed for %s: %s", aid, task.exception())

        await self._advance_movement()
        await self._check_activations()

    # ─── Movement ───

    async def _advance_movement(self) -> None:
        """Move in-transit agents along their paths."""
        steps = config.get_int("steps_per_tick") or 1
        completed: list[str] = []

        for agent_id, path in self._agent_paths.items():
            if not path:
                completed.append(agent_id)
                continue

            for _ in range(steps):
                if not path:
                    break
                next_x, next_y = path.pop(0)
                db.update_agent_state(agent_id, x=next_x, y=next_y)

            if not path:
                completed.append(agent_id)

        for agent_id in completed:
            self._agent_paths.pop(agent_id, None)
            state = db.get_agent_state(agent_id)
            if state and state.status == "in_transit":
                db.update_agent_state(agent_id, status="idle")

                agent = db.get_agent(agent_id)
                if agent:
                    room = get_room_at(state.x, state.y)
                    room_name = room["name"] if room else "destination"
                    await manager.broadcast_activity(
                        event="agent_moved",
                        detail=f"{agent.name} arrived at {room_name}",
                        agent_name=agent.name,
                    )

        if completed:
            await manager.broadcast_world_state()

    # ─── Activation checks ───

    async def _check_activations(self) -> None:
        """Check idle agents for triggers and launch turns."""
        agents = db.list_agents()

        for agent in agents:
            if agent.id in self._active_turns:
                continue

            state = db.get_agent_state(agent.id)
            if not state or state.status != "idle":
                continue

            trigger = await check_activation(agent, state)
            if trigger:
                task = asyncio.create_task(
                    self._safe_turn(agent, state, trigger)
                )
                self._active_turns[agent.id] = task

    async def _safe_turn(
        self,
        agent: Any,
        state: Any,
        trigger: dict[str, Any],
    ) -> None:
        """Run an agent turn with error handling.

        If the turn produces a ``walk_to`` result with path data,
        register it for the movement system.
        """
        try:
            result = await run_turn(agent, state, trigger)

            # If the action was walk_to, consume the path data
            if result.get("path") and result.get("agent_id"):
                self.set_agent_path(result["agent_id"], result["path"])

        except Exception:
            logger.exception("Agent turn failed for %s", agent.name)
            try:
                db.update_agent_state(agent.id, status="idle")
            except Exception:
                logger.warning("Failed to reset agent %s to idle after turn failure", agent.name)


# Module-level singleton
simulation = WorldSimulation()
