"""BossMod AI — Active-task watchdog."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.dispatcher import dispatcher
from core.runtime.events import runtime_events as manager
from core.time import ensure_utc
import db

logger = logging.getLogger(__name__)


class TaskWatchdog:
    """Monitors active tasks and nudges stalled agents."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Task watchdog started")

    async def stop(self) -> None:
        self._running = False
        loop_task = self._task
        self._task = None
        if loop_task:
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task
        logger.info("Task watchdog stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check_tasks()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Task watchdog loop error")
            interval = config.get_float("watchdog_check_interval_seconds") or 5.0
            await asyncio.sleep(interval)

    async def _check_tasks(self) -> None:
        soft_minutes = config.get_int("watchdog_soft_ping_minutes") or 15
        escalation_minutes = config.get_int("watchdog_escalation_minutes") or 15

        now = datetime.now(timezone.utc)
        active_tasks = db.list_tasks(status="active")
        for task in active_tasks:
            if not task.assigned_to:
                continue

            if dispatcher.is_active(task.assigned_to) or db.has_open_trigger(task.assigned_to):
                continue

            active_activity = activity_runtime.get_active_activity(task.assigned_to)
            if active_activity and active_activity.kind == "movement":
                continue

            last_progress = ensure_utc(task.last_progress_at or task.last_activity or task.created_at)
            last_heartbeat = ensure_utc(task.last_heartbeat_at or task.last_activity or task.created_at)
            quiet_since = max(last_progress, last_heartbeat)
            quiet_for = now - quiet_since
            soft_threshold = timedelta(minutes=soft_minutes)
            escalation_threshold = timedelta(minutes=escalation_minutes)

            if task.watchdog_pinged_at:
                pinged_at = ensure_utc(task.watchdog_pinged_at)
                if last_heartbeat > pinged_at:
                    db.update_task(task.id, watchdog_pinged_at=None)
                    continue

                if now - pinged_at >= escalation_threshold and task.status != "stalled":
                    db.update_task(
                        task.id,
                        status="stalled",
                        status_note="Watchdog escalated after no heartbeat from the agent.",
                    )
                    db.cancel_open_activities(
                        task.assigned_to,
                        detail="Cancelled after watchdog escalation.",
                    )
                    activity_runtime.refresh_agent_status(task.assigned_to)
                    await manager.broadcast_activity(
                        event="task_stalled",
                        detail=f'Task "{task.title}" stalled after watchdog escalation',
                        agent_name=_agent_name(task.assigned_to),
                    )
                continue

            if quiet_for < soft_threshold:
                continue

            agent = db.get_agent(task.assigned_to)
            if agent is None:
                continue

            if db.has_open_trigger_matching(
                agent.id,
                trigger_types=["watchdog_status_ping"],
                task_id=task.id,
            ):
                continue

            content = f'Watchdog check: are you still working on "{task.title}"? Provide a status update.'
            db.update_task(task.id, watchdog_pinged_at=now)
            await manager.broadcast_activity(
                event="watchdog_ping",
                detail=f'Watchdog pinged {agent.name} for task "{task.title}"',
                agent_name=agent.name,
            )
            dispatcher.enqueue_trigger(
                agent_id=agent.id,
                trigger_type="watchdog_status_ping",
                source_channel="system",
                payload={
                    "content": content,
                    "from_name": "System",
                    "task_title": task.title,
                },
                task_id=task.id,
            )
def _agent_name(agent_id: str) -> str | None:
    agent = db.get_agent(agent_id)
    return agent.name if agent else None


watchdog = TaskWatchdog()
