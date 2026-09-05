"""BossMod AI — Durable trigger dispatcher."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import (
    build_task_assigned_trigger,
    can_dispatch_trigger,
    plan_arrival_follow_up,
    prepare_trigger_context,
)
from core.agent_loop.loop import run_turn
from core.agent_loop.policies import get_trigger_policy
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import runtime_events as manager
from core.tasking.transitions import transition_task
import db

logger = logging.getLogger(__name__)

_HUMAN_PREEMPTED_TRIGGER_TYPES = ["activity_resumed", "watchdog_status_ping", "social"]
_REBUILDABLE_BACKLOG_TRIGGER_TYPES = ["task_assigned", "activity_resumed", "watchdog_status_ping", "social"]
_WORK_REPLAN_ACTIONS = {"complete", "blocked", "delegated", "abandoned"}


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
        claim_timeout = config.get_int("trigger_claim_timeout_seconds") or 300
        recovered = db.requeue_stale_triggers(claim_timeout)
        if recovered:
            logger.warning("Requeued %d stale claimed triggers", recovered)
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Turn dispatcher started")

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        loop_task = self._task
        self._task = None
        if loop_task:
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task

        active_turns = list(self._active_turns.values())
        for task in active_turns:
            task.cancel()
        self._active_turns.clear()
        for task in active_turns:
            with suppress(asyncio.CancelledError):
                await task

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
        if trigger_type == "human_chat":
            db.delete_queued_triggers(agent_id, trigger_types=_HUMAN_PREEMPTED_TRIGGER_TYPES)
        db.create_agent_trigger(
            agent_id=agent_id,
            trigger_type=trigger_type,
            source_channel=source_channel,
            payload=payload,
            task_id=task_id,
        )
        self.notify()

    async def reset_runtime(self) -> None:
        """Cancel all active turns and deferred timers without mutating the database."""
        for handle in self._social_timers.values():
            handle.cancel()
        self._social_timers.clear()

        active_tasks = list(self._active_turns.values())
        self._active_turns.clear()
        for task in active_tasks:
            task.cancel()
        for task in active_tasks:
            with suppress(asyncio.CancelledError):
                await task
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

    def _retry_limit(self) -> int:
        """Return the configured number of retries after the initial failed attempt."""
        configured = config.get_int("turn_failure_retry_limit")
        if configured is None or configured < 0:
            return 2
        return configured

    @staticmethod
    def _short_error_detail(detail: str, *, limit: int = 240) -> str:
        """Keep persisted failure detail readable in task notes and chat notices."""
        text = " ".join((detail or "Unknown turn failure").split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    @staticmethod
    def _is_retryable_outcome(trigger_status: str) -> bool:
        """Return whether a trigger outcome should be retried automatically."""
        return trigger_status == "failed"

    def _resolve_stuck_task(self, agent_id: str, trigger: dict[str, Any]):
        """Return the task that should be marked stalled after retry exhaustion, if any."""
        task_id = trigger.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            task_id = activity_runtime.get_active_task_id(agent_id)
        if not isinstance(task_id, str) or not task_id.strip():
            return None
        task = db.get_task(task_id)
        if task is None:
            return None
        if task.status in {"complete", "abandoned", "delegated", "declined"}:
            return None
        return task

    async def _notify_human_of_stuck_turn(
        self,
        *,
        agent: Any,
        failure_detail: str,
        task: Any | None,
    ) -> None:
        """Persist and broadcast a requester-visible stuck notice."""
        if task is not None:
            content = (
                f'I hit repeated runtime failures while handling "{task.title}". '
                f'The task is now stalled. Last error: {failure_detail}'
            )
        else:
            content = (
                "I hit repeated runtime failures while handling the request and could not recover. "
                f"Last error: {failure_detail}"
            )
        state = db.get_agent_state(agent.id)
        message = db.create_message(
            from_agent=agent.id,
            to_agent=HUMAN_SENDER_ID,
            content=content,
            message_type="social",
            location_x=state.x if state else 0,
            location_y=state.y if state else 0,
        )
        await manager.broadcast_chat_message(
            agent_id=agent.id,
            content=message.content,
            from_type="agent",
            from_name=agent.name,
            message_type=message.message_type,
            message_id=message.id,
            created_at=message.created_at,
        )

    async def _exhaust_failed_trigger(
        self,
        *,
        agent: Any,
        trigger: dict[str, Any],
        failure_detail: str,
    ) -> None:
        """Fail the trigger permanently, reconcile state, and surface the stall."""
        trigger_id = trigger["trigger_id"]
        db.fail_agent_trigger(trigger_id, failure_detail)

        task = self._resolve_stuck_task(agent.id, trigger)
        if task is not None:
            transition_task(
                task.id,
                "stalled",
                reason=f"Runtime exhausted automatic retries: {failure_detail}",
                actor="BossMod",
                status_note=f"Runtime exhausted automatic retries: {failure_detail}",
                watchdog_pinged_at=None,
            )
            db.cancel_open_activities(
                agent.id,
                detail=f"Cancelled after retry exhaustion: {failure_detail}",
            )
            activity_runtime.refresh_agent_status(agent.id)
            await self._notify_human_of_stuck_turn(agent=agent, failure_detail=failure_detail, task=task)
            await manager.broadcast_activity(
                event="task_stalled",
                detail=f'Task "{task.title}" stalled after retry exhaustion',
                agent_name=agent.name,
                extra={
                    "task_id": task.id,
                    "trigger_type": trigger.get("type"),
                    "failure_reason": failure_detail,
                },
            )
            return

        activity_runtime.reconcile_after_turn_failure(
            agent.id,
            detail=f"Turn failed while processing {trigger.get('type', 'trigger')}: {failure_detail}",
        )
        if trigger.get("type") == "human_chat":
            await self._notify_human_of_stuck_turn(agent=agent, failure_detail=failure_detail, task=None)
        await manager.broadcast_activity(
            event="agent_error",
            detail=f"{agent.name} failed while processing a trigger",
            agent_name=agent.name,
            extra={
                "trigger_type": trigger.get("type"),
                "failure_reason": failure_detail,
            },
        )

    async def _supervise_failed_turn(
        self,
        *,
        agent: Any,
        trigger: dict[str, Any],
        failure_detail: str,
        retryable: bool,
    ) -> None:
        """Route every failed turn through one retry-or-exhaust decision path."""
        normalized_detail = self._short_error_detail(failure_detail)
        trigger_record = db.get_agent_trigger(trigger["trigger_id"])
        retry_limit = self._retry_limit()
        if retryable and trigger_record is not None and trigger_record.retry_count < retry_limit:
            retried = db.retry_agent_trigger(trigger["trigger_id"], normalized_detail)
            retry_count = retried.retry_count if retried is not None else trigger_record.retry_count + 1
            await manager.broadcast_activity(
                event="trigger_retry_scheduled",
                detail=(
                    f"{agent.name} hit a runtime failure and will retry "
                    f"({retry_count}/{retry_limit})"
                ),
                agent_name=agent.name,
                extra={
                    "trigger_id": trigger["trigger_id"],
                    "trigger_type": trigger.get("type"),
                    "retry_count": retry_count,
                    "retry_limit": retry_limit,
                    "failure_reason": normalized_detail,
                },
            )
            return

        await self._exhaust_failed_trigger(
            agent=agent,
            trigger=trigger,
            failure_detail=normalized_detail,
        )

    def _enqueue_result_triggers(self, result: dict[str, Any]) -> None:
        """Persist any follow-up triggers emitted by a successful turn."""
        for queued in result.get("trigger_requests", []):
            self.enqueue_trigger(
                agent_id=queued["agent_id"],
                trigger_type=queued["trigger_type"],
                source_channel=queued["source_channel"],
                payload=queued["payload"],
                task_id=queued.get("task_id"),
            )

    async def _record_dispatcher_exception(self, *, agent: Any, trigger: dict[str, Any], exc: Exception) -> None:
        """Persist a diagnostic row for exceptions raised outside normal turn finalization."""
        diag = db.create_diagnostic(
            agent_id=agent.id,
            agent_name=agent.name,
            trigger_type=trigger.get("type", "unknown"),
            trigger_data=json.dumps(trigger),
            status="error",
                mode="decision"
                if trigger.get("type")
                in {
                    "human_chat",
                    "peer_message",
                    "meeting_invite",
                    "task_follow_up",
                    "task_update",
                    "session_message",
                    "session_response",
                    "channel_message",
                    "channel_response",
                    "task_assigned",
                }
                else "execution",
            model=None,
            model_source="runtime",
            context=None,
            raw_response=None,
            action_name="",
            parsed_action=None,
            result=json.dumps({"event": "agent_error", "detail": str(exc)}, default=str),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            error=str(exc),
            duration_ms=0,
            steps=None,
        )
        await manager.broadcast_diagnostic(diag)

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
            candidate = self._claim_available_trigger()
            if not candidate:
                return

            payload = json.loads(candidate.payload) if candidate.payload else {}
            payload.update({
                "type": candidate.trigger_type,
                "trigger_id": candidate.id,
                "task_id": candidate.task_id,
                "source_channel": candidate.source_channel,
            })

            agent = db.get_agent(candidate.agent_id)
            if not agent:
                db.fail_agent_trigger(candidate.id, "Agent not found")
                continue

            prepare_trigger_context(agent.id, payload)
            policy = get_trigger_policy(candidate.trigger_type)
            state = activity_runtime.refresh_agent_status(agent.id)
            if state is None:
                db.fail_agent_trigger(candidate.id, "Agent state not found")
                continue

            if policy.require_work_activity and not activity_runtime.get_active_task_id(agent.id):
                db.fail_agent_trigger(candidate.id, "Trigger requires active work activity")
                activity_runtime.refresh_agent_status(agent.id)
                continue

            task = asyncio.create_task(self._run_trigger(agent, state, payload))
            self._active_turns[agent.id] = task

    def _claim_available_trigger(self):
        """Claim the next queued trigger that can legally run now."""
        for trigger in db.list_queued_triggers(limit=100):
            if trigger.agent_id in self._active_turns:
                continue

            state = db.get_agent_state(trigger.agent_id)
            active_activity = activity_runtime.get_active_activity(trigger.agent_id)
            if not can_dispatch_trigger(
                trigger_type=trigger.trigger_type,
                state=state,
                active_activity=active_activity,
            ):
                continue

            claimed = db.claim_trigger(trigger.id)
            if claimed is not None:
                return claimed
        return None

    async def _run_trigger(self, agent: Any, state: Any, trigger: dict[str, Any]) -> None:
        trigger_id = trigger["trigger_id"]

        try:
            outcome = await run_turn(agent, state, trigger)
            result = outcome.result

            if outcome.trigger_status == "completed":
                if self._should_replan_backlog(trigger, outcome.action):
                    self._rebuild_backlog_queue(agent.id)
                db.complete_agent_trigger(trigger_id)
                self._enqueue_result_triggers(result)

                if result.get("path") and result.get("agent_id"):
                    from core.world.simulation import simulation

                    simulation.set_agent_path(result["agent_id"], result["path"])
            elif outcome.trigger_status == "skipped":
                # No-model (and other) skips are not failures. Completing the
                # trigger avoids _exhaust_failed_trigger, which would mark the
                # row failed and can stall the bound task (HA-CORR-P0-03).
                db.complete_agent_trigger(trigger_id)
            else:
                await self._supervise_failed_turn(
                    agent=agent,
                    trigger=trigger,
                    failure_detail=outcome.diagnostic_error or "Turn failed",
                    retryable=self._is_retryable_outcome(outcome.trigger_status),
                )

        except Exception as exc:
            logger.exception("Trigger execution failed for %s", agent.name)
            try:
                await self._record_dispatcher_exception(agent=agent, trigger=trigger, exc=exc)
                await self._supervise_failed_turn(
                    agent=agent,
                    trigger=trigger,
                    failure_detail=str(exc),
                    retryable=True,
                )
            except Exception:
                logger.exception("Failed to clean up agent after trigger failure")
        finally:
            self._active_turns.pop(agent.id, None)
            final_state = db.get_agent_state(agent.id)
            if final_state and final_state.status == "idle":
                self.notify_agent_idle(agent.id)
            self.notify()

    async def handle_arrival(self, agent_id: str, room_name: str) -> None:
        """Resolve movement arrival and schedule the resumed activity, if any."""
        resumed_activity = activity_runtime.resolve_arrival(agent_id)
        for queued in plan_arrival_follow_up(agent_id, resumed_activity, room_name):
            self.enqueue_trigger(
                agent_id=queued["agent_id"],
                trigger_type=queued["trigger_type"],
                source_channel=queued["source_channel"],
                payload=queued["payload"],
                task_id=queued.get("task_id"),
            )

        state = db.get_agent_state(agent_id)
        if state and state.status == "idle":
            self.notify_agent_idle(agent_id)
        self.notify()

    def _should_replan_backlog(self, trigger: dict[str, Any], action: dict[str, Any] | None) -> bool:
        """Return whether a direct interrupt changed durable work selection."""
        if trigger.get("type") != "human_chat" or not action:
            return False
        action_name = action.get("action")
        if action_name in _WORK_REPLAN_ACTIONS:
            return True
        return action.get("decision") in {"accept", "defer"} and action.get("commitmentKind") == "work"

    def _rebuild_backlog_queue(self, agent_id: str) -> None:
        """Drop stale resumptive backlog triggers and rebuild pending assignments."""
        db.delete_queued_triggers(agent_id, trigger_types=_REBUILDABLE_BACKLOG_TRIGGER_TYPES)
        for task in db.list_tasks(assigned_to=agent_id, status="pending"):
            self.enqueue_trigger(**build_task_assigned_trigger(task))

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

        if (
            db.list_tasks(assigned_to=agent_id, status="pending")
            or db.list_tasks(assigned_to=agent_id, status="accepted")
            or db.list_tasks(assigned_to=agent_id, status="active")
        ):
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
            if (
                db.list_tasks(assigned_to=peer_id, status="pending")
                or db.list_tasks(assigned_to=peer_id, status="accepted")
                or db.list_tasks(assigned_to=peer_id, status="active")
            ):
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
