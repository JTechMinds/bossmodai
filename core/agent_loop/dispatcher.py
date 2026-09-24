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
    can_dispatch_trigger,
    persist_result_triggers,
    plan_arrival_follow_up,
    prepare_trigger_context,
)
from core.agent_loop.loop import run_turn
from core.llm.call_budget import (
    Lane,
    bind_turn_lane,
    budget,
    max_concurrent_model_calls,
    reset_turn_lane,
)
from core.llm.client import close_provider_sessions
from core.agent_loop.policies import get_trigger_policy
from core.agent_loop.queue_visibility import emit_queue_visibility, schedule_queue_visibility
from core.floors import VACATION_DENY, agent_id_on_vacation, is_on_vacation
from core.agent_loop.task_origin_mirrors import (
    format_origin_status_line,
    origin_thread_target,
    persist_origin_status_line,
)
from core.models.message import HUMAN_SENDER_ID
from core.runtime.events import runtime_events as manager
from core.tasking.transitions import IllegalTaskTransition, is_terminal_task_status, transition_task
import db

logger = logging.getLogger(__name__)

_HUMAN_PREEMPTED_TRIGGER_TYPES = ["activity_resumed", "watchdog_status_ping", "social"]
_REBUILDABLE_BACKLOG_TRIGGER_TYPES = ["task_assigned", "activity_resumed", "watchdog_status_ping", "social"]
_WORK_REPLAN_ACTIONS = {"complete", "blocked", "delegated", "abandoned"}
LEASE_HEARTBEAT_SECONDS = 10.0


def _runtime_is_paused() -> bool:
    """Return True as soon as Pause is stored, before the worker command runs.

    The app writes the setting in its process. This process must read it
    live so a new wake does not start on the next claim.
    """
    return config.get_live("runtime_control_state") == "paused"


def _is_decision_llm_timeout(exc: Exception, trigger: dict[str, Any]) -> bool:
    """Return whether an escaped error is a decision-turn model timeout."""
    from core.agent_loop.turn_context import _DECISION_TRIGGER_TYPES
    from core.llm.client import LLMTimeoutError

    return isinstance(exc, LLMTimeoutError) and trigger.get("type") in _DECISION_TRIGGER_TYPES


class TurnDispatcher:
    """Claims queued triggers and launches agent turns."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._active_turns: dict[str, asyncio.Task[Any]] = {}
        self._turn_lanes: dict[str, Lane] = {}
        self._queue_notices: list[dict[str, Any]] = []
        self._social_timers: dict[str, asyncio.TimerHandle] = {}

    def start(self) -> None:
        if self._running:
            return
        claim_timeout = config.get_int("trigger_claim_timeout_seconds") or 300
        # New process: every claimed row is an orphan. force=True does not
        # touch completed triggers (HA-SEC-P1-02).
        recovered = db.requeue_stale_triggers(claim_timeout, force=True)
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
        """Persist a trigger and wake the dispatcher.

        Writes nothing for a payload aimed at an archived thread or for an
        agent on vacation; both are expected, documented skips.
        """
        if agent_id_on_vacation(agent_id):
            logger.info("Skipped %s trigger for %s: agent is on vacation", trigger_type, agent_id)
            return
        if trigger_type == "human_chat":
            db.delete_queued_triggers(agent_id, trigger_types=_HUMAN_PREEMPTED_TRIGGER_TYPES)
        if db.payload_targets_archived_channel(payload):
            return
        db.create_agent_trigger(
            agent_id=agent_id,
            trigger_type=trigger_type,
            source_channel=source_channel,
            payload=payload,
            task_id=task_id,
        )
        schedule_queue_visibility(agent_id)
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
        await emit_queue_visibility(agent_id)
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
        if task.status in {"complete", "abandoned", "delegated", "declined", "cancelled"}:
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
            content = format_origin_status_line(
                kind="stalled",
                agent=agent,
                task=task,
                reason=f"Runtime exhausted automatic retries: {failure_detail}",
            )
        else:
            content = (
                "I hit repeated runtime failures while handling the request and could not recover. "
                f"Last error: {failure_detail}"
            )
        if origin_thread_target(task) == "channel":
            posted = persist_origin_status_line(
                task=task,
                agent=agent,
                content=content,
                kind="stalled",
            )
            channel_message = posted.get("channel_message")
            if channel_message:
                await manager.broadcast_channel_message(
                    channel_id=channel_message["channel_id"],
                    content=channel_message["content"],
                    author_type=channel_message["author_type"],
                    author_name=channel_message["author_name"],
                    message_id=channel_message.get("message_id"),
                    created_at=channel_message.get("created_at"),
                    notification_kind=channel_message.get("notification_kind"),
                )
            return
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
        claim_generation = self._claim_generation_for(trigger)
        failed = db.fail_agent_trigger(
            trigger_id,
            failure_detail,
            claim_generation=claim_generation,
        )
        if failed is None:
            logger.warning(
                "Skipped stale fail for trigger %s generation %s",
                trigger_id,
                claim_generation,
            )
            return

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
            retried = db.retry_agent_trigger(
                trigger["trigger_id"],
                normalized_detail,
                claim_generation=self._claim_generation_for(trigger),
            )
            if retried is None:
                logger.warning(
                    "Skipped stale retry for trigger %s generation %s",
                    trigger.get("trigger_id"),
                    trigger.get("claim_generation"),
                )
                return
            retry_count = retried.retry_count
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
        persisted = persist_result_triggers(result)
        if persisted:
            self.notify()

    async def _recover_escaped_decision_timeout(
        self,
        *,
        agent: Any,
        trigger: dict[str, Any],
        exc: Exception,
    ) -> None:
        """Note and re-queue when a decision-turn timeout escapes the repair loop.

        This is the same commitment wake as an in-turn timeout fail-close.
        It does not retry through ``_supervise_failed_turn``, which would
        stall the task after the trigger retry budget.
        """
        from core.agent_loop.decision_parse_fail import surface_llm_timeout_failure
        from core.agent_loop.decision_turn import broadcast_recovery_note

        surfaced = surface_llm_timeout_failure(agent=agent, trigger=trigger)
        result: dict[str, Any] = {
            "event": "agent_error",
            "detail": str(exc),
            "agent_name": agent.name,
            "trigger_requests": surfaced.get("trigger_requests") or [],
            "llm_timeout": True,
        }
        if surfaced.get("chat_message"):
            result["chat_message"] = surfaced["chat_message"]
        if surfaced.get("channel_message"):
            result["channel_message"] = surfaced["channel_message"]
        await manager.broadcast_activity(
            event=result["event"],
            detail=result["detail"],
            agent_name=result["agent_name"],
        )
        await broadcast_recovery_note(agent, result)
        await self._record_dispatcher_exception(agent=agent, trigger=trigger, exc=exc)
        trigger_id = trigger["trigger_id"]
        db.complete_agent_trigger(
            trigger_id,
            claim_generation=self._claim_generation_for(trigger),
        )
        self._enqueue_result_triggers(result)
        activity_runtime.refresh_agent_status(agent.id)

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
        if _runtime_is_paused():
            return
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
            await self._emit_queue_notices()
            if not candidate:
                return

            try:
                started = await self._launch_claimed_trigger(candidate)
            except Exception:
                self._release_turn_lane(candidate.agent_id)
                raise
            if not started:
                self._release_turn_lane(candidate.agent_id)

    async def _launch_claimed_trigger(self, candidate: Any) -> bool:
        """Start one claimed trigger. False means the lane should be released."""
        payload = json.loads(candidate.payload) if candidate.payload else {}
        payload.update({
            "type": candidate.trigger_type,
            "trigger_id": candidate.id,
            "task_id": candidate.task_id,
            "source_channel": candidate.source_channel,
            "claim_generation": candidate.claim_generation,
        })
        if db.payload_targets_archived_channel(payload):
            db.complete_agent_trigger(
                candidate.id,
                claim_generation=candidate.claim_generation,
            )
            return False

        agent = db.get_agent(candidate.agent_id)
        if not agent:
            db.fail_agent_trigger(
                candidate.id,
                "Agent not found",
                claim_generation=candidate.claim_generation,
            )
            return False
        # Backstop: enqueue already refuses a vacationer, but a row written
        # before they went home, or by a path that bypasses enqueue, must
        # still never start a turn.
        if is_on_vacation(agent):
            db.fail_agent_trigger(
                candidate.id,
                VACATION_DENY,
                claim_generation=candidate.claim_generation,
            )
            return False

        try:
            prepare_trigger_context(agent.id, payload)
        except IllegalTaskTransition as exc:
            logger.warning(
                "Soft-skipped illegal task wake for %s: %s",
                agent.name,
                exc,
            )
            activity_runtime.close_terminal_work_activity(
                agent.id,
                reason=activity_runtime.TERMINAL_WAKE_WHY,
                task_id=payload.get("task_id") if isinstance(payload.get("task_id"), str) else None,
            )
            task = db.get_task(payload["task_id"]) if isinstance(payload.get("task_id"), str) else None
            activity_runtime.note_terminal_wake_block(agent.id, task)
            db.complete_agent_trigger(
                candidate.id,
                claim_generation=candidate.claim_generation,
            )
            activity_runtime.refresh_agent_status(agent.id)
            return False

        if (
            candidate.trigger_type == "activity_resumed"
            and _complete_wake_has_no_live_work(agent.id, payload)
        ):
            db.complete_agent_trigger(
                candidate.id,
                claim_generation=candidate.claim_generation,
            )
            activity_runtime.refresh_agent_status(agent.id)
            return False

        policy = get_trigger_policy(candidate.trigger_type)
        state = activity_runtime.refresh_agent_status(agent.id)
        if state is None:
            db.fail_agent_trigger(
                candidate.id,
                "Agent state not found",
                claim_generation=candidate.claim_generation,
            )
            return False

        if policy.require_work_activity and not activity_runtime.get_active_task_id(agent.id):
            db.fail_agent_trigger(
                candidate.id,
                "Trigger requires active work activity",
                claim_generation=candidate.claim_generation,
            )
            activity_runtime.refresh_agent_status(agent.id)
            return False

        task = asyncio.create_task(self._run_trigger(agent, state, payload))
        self._active_turns[agent.id] = task
        return True

    def _claim_available_trigger(self):
        """Claim the next queued trigger that can legally run now.

        A lane is acquired before the trigger is claimed, and thinking is
        broadcast only after that. No lane leaves the trigger queued and
        records ``Queued (n ahead)``. One agent still holds at most one turn.
        A channel holds at most one live speak, even when the global knob
        has a free lane. The next wake in that channel stays queued and is
        painted Queued (n ahead) until the live claim ends. That claim ends
        after the turn has posted, so the next speaker's last-N history
        includes the post. Ahead is channel-local: one for the live speak,
        plus each earlier waiter in that channel. Another room can still
        take a free lane. This wait does not delete the trigger or clear
        stay, and it does not replace an empty speak on a settled no-op.
        Repair wakes are ordered behind a live channel lead and use a lane
        from the same budget once claimed.
        """
        self._queue_notices = []
        if _runtime_is_paused():
            return None
        eligible = []
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
            eligible.append(trigger)

        limit = max_concurrent_model_calls()
        if len(self._active_turns) >= limit:
            self._note_waiting(eligible, 0)
            return None

        held: list[Any] = []
        runnable: list[Any] = []
        for trigger in eligible:
            if _peer_snapshot_is_drafting(trigger):
                held.append(trigger)
            else:
                runnable.append(trigger)
        self._note_channel_waiters(held)

        for index, trigger in enumerate(runnable):
            lane = budget.try_acquire(kind="turn", owner=trigger.agent_id)
            if lane is None:
                self._note_waiting(runnable, index)
                return None
            claimed = db.claim_trigger(trigger.id)
            if claimed is None:
                budget.release(lane)
                continue
            self._turn_lanes[claimed.agent_id] = lane
            schedule_queue_visibility(claimed.agent_id)
            return claimed
        return None

    def _note_channel_waiters(self, triggers: list[Any]) -> None:
        """Paint same-channel peers Queued behind the one live speak.

        Ahead is not the global inflight count. The live speak is one ahead.
        Each earlier waiter in that same channel adds one more. A full knob
        uses ``_note_waiting`` instead, so these peers are not recorded twice.
        """
        seen: dict[str, int] = {}
        for trigger in triggers:
            channel_id = _trigger_channel_id(trigger)
            if not channel_id:
                continue
            slot = seen.get(channel_id, 0)
            seen[channel_id] = slot + 1
            self._queue_notices.append(self._queue_notice(trigger, 1 + slot))

    def _note_waiting(self, triggers: list[Any], start: int) -> None:
        """Record Queued (n ahead) for triggers that did not get a lane."""
        ahead_base = max(budget.inflight(), len(self._active_turns))
        for offset, trigger in enumerate(triggers[start:]):
            self._queue_notices.append(self._queue_notice(trigger, ahead_base + offset))

    def _queue_notice(self, trigger: Any, ahead: int) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if trigger.payload:
            try:
                loaded = json.loads(trigger.payload)
            except json.JSONDecodeError:
                loaded = {}
            if isinstance(loaded, dict):
                payload = loaded
        agent = db.get_agent(trigger.agent_id)
        channel_id = _channel_id_for_presence({
            "type": trigger.trigger_type,
            "channel_id": payload.get("channel_id"),
        })
        return {
            "agent_id": trigger.agent_id,
            "agent_name": agent.name if agent is not None else trigger.agent_id,
            "channel_id": channel_id,
            "phase": "queued",
            "ahead": ahead,
        }

    def _release_turn_lane(self, agent_id: str) -> None:
        lane = self._turn_lanes.pop(agent_id, None)
        if lane is not None:
            budget.release(lane)

    async def _emit_queue_notices(self) -> None:
        notices = self._queue_notices
        self._queue_notices = []
        for notice in notices:
            await self._broadcast_presence(
                agent_id=str(notice["agent_id"]),
                agent_name=str(notice["agent_name"]),
                phase="queued",
                channel_id=notice.get("channel_id"),
                ahead=int(notice["ahead"]),
            )

    async def _broadcast_presence(
        self,
        *,
        agent_id: str,
        agent_name: str,
        phase: str,
        channel_id: str | None,
        ahead: int | None = None,
    ) -> None:
        """Tell the desk and, when this turn has a channel, the thread."""
        await manager.broadcast_agent_presence(
            agent_id=agent_id,
            agent_name=agent_name,
            phase=phase,
            ahead=ahead,
            channel_id=channel_id,
        )
        if channel_id and not db.is_channel_archived(channel_id):
            await manager.broadcast_channel_presence(
                channel_id=channel_id,
                agent_id=agent_id,
                agent_name=agent_name,
                phase=phase,
                ahead=ahead,
            )

    async def _heartbeat_claim_lease(self, trigger_id: str, claim_generation: int) -> None:
        """Keep ``claimed_at`` fresh for the duration of a long LLM/shell turn."""
        while True:
            await asyncio.sleep(LEASE_HEARTBEAT_SECONDS)
            refreshed = db.heartbeat_trigger_lease(trigger_id, claim_generation)
            if refreshed is None:
                return

    def _claim_generation_for(self, trigger: dict[str, Any]) -> int | None:
        raw = trigger.get("claim_generation")
        if isinstance(raw, int):
            return raw
        trigger_id = trigger.get("trigger_id")
        if not isinstance(trigger_id, str) or not trigger_id:
            return None
        row = db.get_agent_trigger(trigger_id)
        return row.claim_generation if row is not None else None

    async def _run_trigger(self, agent: Any, state: Any, trigger: dict[str, Any]) -> None:
        trigger_id = trigger["trigger_id"]
        claim_generation = self._claim_generation_for(trigger)
        heartbeat_task: asyncio.Task[None] | None = None
        if claim_generation is not None:
            heartbeat_task = asyncio.create_task(
                self._heartbeat_claim_lease(trigger_id, claim_generation)
            )

        channel_id = _channel_id_for_presence(trigger)
        lane = self._turn_lanes.get(agent.id)
        lane_token = bind_turn_lane(lane) if lane is not None else None
        # Thinking is broadcast only after a lane is held. A direct test call
        # with no lane does not pretend the model is running.
        if lane is not None:
            await self._broadcast_presence(
                agent_id=agent.id,
                agent_name=agent.name,
                phase="thinking",
                channel_id=channel_id,
            )

        try:
            if db.payload_targets_archived_channel(trigger) or (
                channel_id is not None and db.is_channel_archived(channel_id)
            ):
                db.complete_agent_trigger(trigger_id, claim_generation=claim_generation)
                return
            outcome = await run_turn(agent, state, trigger)
            result = outcome.result

            if outcome.trigger_status == "completed":
                if self._should_replan_backlog(trigger, outcome.action):
                    self._rebuild_backlog_queue(agent.id)
                db.complete_agent_trigger(trigger_id, claim_generation=claim_generation)
                self._enqueue_result_triggers(result)

                if result.get("path") and result.get("agent_id"):
                    from core.world.simulation import simulation

                    simulation.set_agent_path(result["agent_id"], result["path"])
            elif outcome.trigger_status == "skipped":
                # No-model (and other) skips are not failures. Completing the
                # trigger avoids _exhaust_failed_trigger, which would mark the
                # row failed and can stall the bound task (HA-CORR-P0-03).
                db.complete_agent_trigger(trigger_id, claim_generation=claim_generation)
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
                if _is_decision_llm_timeout(exc, trigger):
                    await self._recover_escaped_decision_timeout(
                        agent=agent,
                        trigger=trigger,
                        exc=exc,
                    )
                else:
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
            if lane_token is not None:
                reset_turn_lane(lane_token)
            if lane is not None:
                await self._broadcast_presence(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    phase="idle",
                    channel_id=channel_id,
                )
            self._release_turn_lane(agent.id)
            try:
                await close_provider_sessions(allow_inflight=0)
            except Exception:
                logger.debug("provider session close after turn failed", exc_info=True)
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            self._active_turns.pop(agent.id, None)
            final_state = db.get_agent_state(agent.id)
            if final_state and final_state.status == "idle":
                self.notify_agent_idle(agent.id)
            await emit_queue_visibility(agent.id)
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
            from core.agent_loop.activity_scheduler import assignment_wake_trigger

            wake = assignment_wake_trigger(task)
            if wake is None:
                continue
            self.enqueue_trigger(**wake)

    async def _maybe_enqueue_social_trigger(self, agent_id: str) -> None:
        if _runtime_is_paused():
            return
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


def _trigger_channel_id(trigger: Any) -> str | None:
    """Return the channel id on a queued channel peer trigger, if any."""
    if getattr(trigger, "trigger_type", None) not in {"channel_message", "channel_response"}:
        return None
    try:
        payload = json.loads(trigger.payload) if trigger.payload else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        return None
    return _channel_id_for_presence({
        "type": trigger.trigger_type,
        "channel_id": payload.get("channel_id"),
    })


def _peer_snapshot_is_drafting(trigger: Any) -> bool:
    """Return whether this queued channel peer must wait for the room's live speak."""
    channel_id = _trigger_channel_id(trigger)
    if not channel_id:
        return False
    return db.channel_peer_snapshot_is_drafting(channel_id)


def _channel_id_for_presence(trigger: dict[str, Any]) -> str | None:
    """Return the shared channel for an in-flight channel turn, if any."""
    if trigger.get("type") not in {"channel_message", "channel_response"}:
        return None
    raw = trigger.get("channel_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _complete_wake_has_no_live_work(agent_id: str, payload: dict[str, Any]) -> bool:
    """Return whether an activity resume targeted a complete task with nowhere live to continue."""
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return False
    task = db.get_task(task_id)
    if task is None or not is_terminal_task_status(task.status):
        return False
    live = activity_runtime.get_active_work_activity(agent_id)
    if live and live.task_id:
        bound = db.get_task(live.task_id)
        if bound is not None and not is_terminal_task_status(bound.status):
            return False
    return True


dispatcher = TurnDispatcher()
