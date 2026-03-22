"""BossMod AI — Durable trigger dispatcher."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from api.websocket import manager
from core import config
from core.agent_loop.loop import run_turn
from core.agent_loop.policies import get_trigger_policy
from core.models.message import HUMAN_SENDER_ID
import db

logger = logging.getLogger(__name__)


class TurnDispatcher:
    """Claims queued triggers and launches agent turns."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._active_turns: dict[str, asyncio.Task[Any]] = {}
        self._social_timers: dict[str, asyncio.TimerHandle] = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Turn dispatcher started")

    def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        if self._task:
            self._task.cancel()
            self._task = None
        for task in self._active_turns.values():
            task.cancel()
        self._active_turns.clear()
        for handle in self._social_timers.values():
            handle.cancel()
        self._social_timers.clear()
        logger.info("Turn dispatcher stopped")

    def notify(self) -> None:
        """Wake the dispatcher loop."""
        self._wake_event.set()

    def is_active(self, agent_id: str) -> bool:
        """Return whether the agent currently has an active turn."""
        return agent_id in self._active_turns

    def enqueue_trigger(
        self,
        agent_id: str,
        trigger_type: str,
        source_channel: str,
        payload: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        """Persist a trigger and wake the dispatcher."""
        db.create_agent_trigger(
            agent_id=agent_id,
            trigger_type=trigger_type,
            source_channel=source_channel,
            payload=payload,
            task_id=task_id,
        )
        self.notify()

    def notify_agent_idle(self, agent_id: str) -> None:
        """Schedule an event-driven social probe after the idle threshold."""
        handle = self._social_timers.pop(agent_id, None)
        if handle:
            handle.cancel()

        minutes = config.get_int("social_idle_threshold_minutes")
        if not minutes:
            return

        loop = asyncio.get_running_loop()
        self._social_timers[agent_id] = loop.call_later(
            minutes * 60,
            lambda: asyncio.create_task(self._run_social_probe(agent_id)),
        )

    async def reset_agent(self, agent_id: str) -> None:
        """Cancel any active turn or deferred social timer for an agent."""
        handle = self._social_timers.pop(agent_id, None)
        if handle:
            handle.cancel()

        task = self._active_turns.pop(agent_id, None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.notify()

    async def _run_social_probe(self, agent_id: str) -> None:
        self._social_timers.pop(agent_id, None)
        try:
            await self._maybe_enqueue_social_trigger(agent_id)
        finally:
            self.notify()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._drain_queue()
                self._wake_event.clear()
                await asyncio.wait_for(self._wake_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Dispatcher loop error")

    async def _drain_queue(self) -> None:
        while self._running:
            trigger = db.claim_next_trigger(excluded_agent_ids=list(self._active_turns))
            if not trigger:
                return

            payload = json.loads(trigger.payload) if trigger.payload else {}
            payload.update({
                "type": trigger.trigger_type,
                "trigger_id": trigger.id,
                "task_id": trigger.task_id,
                "source_channel": trigger.source_channel,
            })

            agent = db.get_agent(trigger.agent_id)
            if not agent:
                db.fail_agent_trigger(trigger.id, "Agent not found")
                continue

            policy = get_trigger_policy(trigger.trigger_type)
            state_updates: dict[str, Any] = {
                "status": policy.activation_status,
            }
            if policy.bind_trigger_task and trigger.task_id:
                state_updates["current_task_id"] = trigger.task_id

            state = db.update_agent_state(agent.id, **state_updates)
            if state is None:
                db.fail_agent_trigger(trigger.id, "Agent state not found")
                continue

            if policy.require_task_context and not state.current_task_id:
                db.fail_agent_trigger(trigger.id, "Trigger requires active task context")
                db.update_agent_state(agent.id, status="idle")
                continue

            if trigger.task_id:
                task = db.get_task(trigger.task_id)
                if task and task.status == "pending":
                    db.update_task(trigger.task_id, status="active")

            task = asyncio.create_task(self._run_trigger(agent, state, payload))
            self._active_turns[agent.id] = task

    async def _run_trigger(self, agent: Any, state: Any, trigger: dict[str, Any]) -> None:
        trigger_id = trigger["trigger_id"]
        task_id = trigger.get("task_id")

        try:
            outcome = await run_turn(agent, state, trigger)
            result = outcome.result

            if outcome.trigger_status == "completed":
                db.complete_agent_trigger(trigger_id)
            else:
                db.fail_agent_trigger(trigger_id, outcome.diagnostic_error or "Turn failed")

            if result.get("path") and result.get("agent_id"):
                from core.world.simulation import simulation

                simulation.set_agent_path(result["agent_id"], result["path"])

        except Exception as exc:
            logger.exception("Trigger execution failed for %s", agent.name)
            db.fail_agent_trigger(trigger_id, str(exc))
            try:
                db.update_agent_state(agent.id, status="idle")
                await manager.broadcast_activity(
                    event="agent_error",
                    detail=f"{agent.name} failed while processing a trigger",
                    agent_name=agent.name,
                )
            except Exception:
                logger.exception("Failed to clean up agent after trigger failure")
        finally:
            self._active_turns.pop(agent.id, None)
            final_state = db.get_agent_state(agent.id)
            if final_state and final_state.status == "idle":
                self.notify_agent_idle(agent.id)
            self.notify()

    async def _maybe_enqueue_social_trigger(self, agent_id: str) -> None:
        agent = db.get_agent(agent_id)
        state = db.get_agent_state(agent_id)
        if not agent or not state or state.status != "idle":
            return
        if self.is_active(agent_id) or db.has_open_trigger(agent_id):
            return

        idle_since = state.idle_since
        if idle_since is None:
            return

        if db.list_tasks(assigned_to=agent_id, status="pending") or db.list_tasks(assigned_to=agent_id, status="active"):
            return

        proximity = config.get_int("social_proximity_tiles") or 0
        cooldown_min = config.get_int("social_cooldown_minutes") or 0
        if proximity <= 0 or cooldown_min <= 0:
            return

        nearby = db.get_nearby_agents(agent.id, state.x, state.y, proximity)
        if not nearby:
            return

        now = datetime.now(timezone.utc)
        cooldown_cutoff = now - timedelta(minutes=cooldown_min)
        eligible_peer = None
        for peer in nearby:
            peer_id = peer["id"]
            peer_state = db.get_agent_state(peer_id)
            if not peer_state or peer_state.status != "idle":
                continue
            if self.is_active(peer_id) or db.has_open_trigger(peer_id):
                continue
            if db.list_tasks(assigned_to=peer_id, status="pending") or db.list_tasks(assigned_to=peer_id, status="active"):
                continue

            thread = db.get_agent_direct_thread(agent.id, peer_id, limit=10)
            recent_social = [
                msg for msg in thread
                if msg.message_type == "social" and msg.created_at >= cooldown_cutoff
            ]
            if recent_social:
                continue

            eligible_peer = peer
            break

        if eligible_peer is None:
            return

        if agent.id > eligible_peer["id"]:
            return

        self.enqueue_trigger(
            agent_id=agent.id,
            trigger_type="social",
            source_channel="chat",
            payload={
                "peer_id": eligible_peer["id"],
                "peer_name": eligible_peer["name"],
                "nearby_names": [eligible_peer["name"]],
            },
        )


dispatcher = TurnDispatcher()
