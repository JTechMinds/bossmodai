"""BossMod AI — World simulation tick loop.

Runs as a background asyncio task during the application lifespan.
The simulation now owns movement only; agent wake-ups are handled by the
dispatcher-backed trigger queue.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from api.websocket import manager
from core import config
from core.world.tilemap import get_room_at
import db

logger = logging.getLogger(__name__)


class WorldSimulation:
    """Background world simulation loop."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._agent_paths: dict[str, list[tuple[int, int]]] = {}
        self._agent_progress: dict[str, float] = {}

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

        self._agent_paths.clear()
        self._agent_progress.clear()

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
        self._agent_progress[agent_id] = 0.0

    def clear_agent_path(self, agent_id: str) -> None:
        """Stop any in-progress movement for an agent."""
        self._agent_paths.pop(agent_id, None)
        self._agent_progress.pop(agent_id, None)

    # ─── Main loop ───

    async def _loop(self) -> None:
        consecutive_errors = 0
        last_tick_at = time.monotonic()
        while self._running:
            tick_started_at = time.monotonic()
            elapsed = max(tick_started_at - last_tick_at, 0.0)
            last_tick_at = tick_started_at
            try:
                await self._tick(elapsed)
                consecutive_errors = 0
            except asyncio.CancelledError:
                break
            except Exception:
                consecutive_errors += 1
                threshold = config.get_int("sim_error_threshold") or 10
                logger.exception("Simulation tick error (%d consecutive)", consecutive_errors)
                if consecutive_errors >= threshold:
                    backoff = config.get_int("sim_error_backoff_seconds") or 30
                    logger.critical(
                        "%d consecutive tick failures — pausing for %ds",
                        threshold, backoff,
                    )
                    await asyncio.sleep(backoff)
                    consecutive_errors = 0

            interval = config.get_float("tick_interval") or 3.0
            await asyncio.sleep(interval)

    async def _tick(self, elapsed: float) -> None:
        """Execute one simulation tick."""
        # Clean up finished agent turns
        await self._advance_movement(elapsed)

    # ─── Movement ───

    async def _advance_movement(self, elapsed: float = 0.0) -> None:
        """Move in-transit agents along their paths."""
        movement_speed = config.get_float("movement_tiles_per_second") or 4.0
        completed: list[str] = []
        moved_any = False

        for agent_id, path in list(self._agent_paths.items()):
            if not path:
                completed.append(agent_id)
                continue

            progress = self._agent_progress.get(agent_id, 0.0) + (elapsed * movement_speed)
            steps = int(progress)
            self._agent_progress[agent_id] = progress - steps

            for _ in range(steps):
                if not path:
                    break
                next_x, next_y = path.pop(0)
                db.update_agent_state(agent_id, x=next_x, y=next_y)
                moved_any = True

            if not path:
                completed.append(agent_id)

        for agent_id in completed:
            self._agent_paths.pop(agent_id, None)
            self._agent_progress.pop(agent_id, None)
            state = db.get_agent_state(agent_id)
            if state and state.status == "in_transit":
                db.update_agent_state(agent_id, status="idle")

                agent = db.get_agent(agent_id)
                room = get_room_at(state.x, state.y)
                room_name = room["name"] if room else "destination"
                if agent:
                    await manager.broadcast_activity(
                        event="agent_moved",
                        detail=f"{agent.name} arrived at {room_name}",
                        agent_name=agent.name,
                    )
                task = db.get_task(state.current_task_id) if state.current_task_id else None
                from core.agent_loop.dispatcher import dispatcher

                if task and task.status == "active":
                    trigger_type = "task_resumed"
                    payload = {
                        "task_title": task.title,
                        "task_description": task.description or "",
                        "content": f'You arrived at your destination. Resume work on "{task.title}".',
                    }
                    if not room or room["room_type"] != "workspace":
                        trigger_type = "task_attention_required"
                        payload = {
                            "task_title": task.title,
                            "task_description": task.description or "",
                            "room_name": room_name,
                            "content": (
                                f'You arrived in the {room_name} while "{task.title}" is still active. '
                                "You cannot produce work here. Either walk to your desk, send a status update, "
                                "or sign off with complete/blocked/delegated/abandoned."
                            ),
                        }
                    dispatcher.enqueue_trigger(
                        agent_id=agent_id,
                        trigger_type=trigger_type,
                        source_channel="work",
                        payload=payload,
                        task_id=task.id,
                    )
                else:
                    dispatcher.notify_agent_idle(agent_id)

        if moved_any:
            await manager.broadcast_world_state()


# Module-level singleton
simulation = WorldSimulation()
