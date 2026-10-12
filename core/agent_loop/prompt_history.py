"""BossMod AI — Backend-owned prompt-history view assembly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import db
from core.boss import boss_label
from core.agent_loop.conversation_notes import (
    ThreadScope,
    entry_tokens,
    load_thread_window,
    maybe_schedule_compaction,
    read_settings,
    render_history_entry,
    window_entries,
)
from core.agent_loop.sticky_slots import compose_channel_sticky_slots, compose_task_sticky_slots
from core.agent_loop.task_thread_history import load_task_thread_history
from core.models import Agent, Notification

# Rows a budgeted reader (task thread, meeting) asks for per step, and the
# channel rows before the visible window handed to sticky slots so a task
# turn folded into the notes still reaches its slots.
_FETCH_STEP_ROWS = 100


@dataclass(slots=True)
class PromptHistoryView:
    """The model-visible history view derived from source tables."""

    conversation_history: list[dict[str, Any]]
    prompt_notifications: list[Notification]


def build_prompt_history_view(
    agent: Agent,
    trigger: dict[str, Any],
    *,
    token_model: str | None = None,
) -> PromptHistoryView:
    """Build the prompt-visible history view for one agent turn."""
    policy = db.ensure_agent_prompt_history_policy(agent.id)
    conversation_history = _load_conversation_history(agent, trigger, policy, token_model=token_model)
    prompt_notifications: list[Notification] = []
    if policy.include_notifications:
        prompt_notifications = _load_prompt_notifications(agent.id, policy)
    return PromptHistoryView(
        conversation_history=conversation_history,
        prompt_notifications=prompt_notifications,
    )


def _load_conversation_history(
    agent: Agent,
    trigger: dict[str, Any],
    policy: Any,
    *,
    token_model: str | None = None,
) -> list[dict[str, Any]]:
    trigger_type = trigger.get("type")
    budget = read_settings().budget_tokens

    if trigger_type == "activity_resumed" and trigger.get("task_id"):
        thread = _fetch_until_budget(
            lambda limit: load_task_thread_history(
                task_id=str(trigger["task_id"]),
                limit=limit,
                earliest_ts=policy.earliest_ts_allowed,
            ),
            budget,
            agent.id,
        )
        return _finish_history(
            thread,
            agent.id,
            policy,
            token_model=token_model,
            scope_kind="task",
            scope_id=str(trigger["task_id"]),
        )

    if trigger_type in {"task_assigned", "task_follow_up", "task_update"} and trigger.get("task_id"):
        thread = _fetch_until_budget(
            lambda limit: load_task_thread_history(
                task_id=str(trigger["task_id"]),
                limit=limit,
                earliest_ts=policy.earliest_ts_allowed,
                exclude_source_message_id=trigger.get("source_message_id"),
                exclude_source_task_event_id=trigger.get("source_task_event_id"),
            ),
            budget,
            agent.id,
        )
        return _finish_history(
            thread,
            agent.id,
            policy,
            token_model=token_model,
            scope_kind="task",
            scope_id=str(trigger["task_id"]),
        )

    if trigger_type in ("human_chat", "watchdog_status_ping"):
        return _thread_history(
            ThreadScope.human_dm(agent.id),
            agent,
            earliest_ts=policy.earliest_ts_allowed,
            exclude_message_id=trigger.get("source_message_id") if trigger_type == "human_chat" else None,
        )

    if trigger_type == "peer_message" and trigger.get("from_agent"):
        return _thread_history(
            ThreadScope.agent_dm(agent.id, str(trigger["from_agent"])),
            agent,
            earliest_ts=policy.earliest_ts_allowed,
            exclude_message_id=trigger.get("source_message_id"),
        )

    if trigger_type in {"channel_message", "channel_response"}:
        channel_id = trigger.get("channel_id")
        if channel_id:
            return _channel_history(
                str(channel_id),
                agent,
                policy,
                token_model=token_model,
                exclude_message_id=trigger.get("source_message_id"),
            )

    if trigger_type in {"session_message", "session_response"}:
        session_id = trigger.get("session_id")
        if session_id:
            thread = _fetch_until_budget(
                lambda limit: db.get_formatted_meeting_session_messages(
                    session_id,
                    human_label=boss_label(),
                    limit=limit,
                ),
                budget,
                agent.id,
            )
            thread = _exclude_source_message(thread, trigger.get("source_message_id"))
            return _finish_history(
                thread,
                agent.id,
                policy,
                token_model=token_model,
                scope_kind="meeting",
                scope_id=str(session_id),
            )

    if trigger_type == "activity_resumed":
        active = db.get_active_activity(agent.id)
        if active and active.kind == "meeting":
            session = db.get_active_meeting_session_for_agent(agent.id)
            if session is not None:
                thread = _fetch_until_budget(
                    lambda limit: db.get_formatted_meeting_session_messages(
                        session.id,
                        human_label=boss_label(),
                        limit=limit,
                    ),
                    budget,
                    agent.id,
                )
                return _finish_history(
                    thread,
                    agent.id,
                    policy,
                    token_model=token_model,
                    scope_kind="meeting",
                    scope_id=session.id,
                )
        if active and active.kind == "conversation":
            return _thread_history(
                ThreadScope.human_dm(agent.id),
                agent,
                earliest_ts=policy.earliest_ts_allowed,
                exclude_message_id=None,
            )

    if trigger_type in {"host_path_consent_resolved", "cli_approval_resolved"}:
        return _load_consent_or_approval_history(
            agent,
            trigger,
            policy,
            token_model=token_model,
        )

    return []


def _load_consent_or_approval_history(
    agent: Agent,
    trigger: dict[str, Any],
    policy: Any,
    *,
    token_model: str | None = None,
) -> list[dict[str, Any]]:
    """Load the conversation the consent/approval wake is resuming.

    Always-allow / approval enqueue is fine; the wake used to fall through
    to empty history, so the execution turn had no operator ask and idled.
    Prefer the bound task thread when one exists, then the originating
    channel thread, otherwise human chat.
    """
    task_id = trigger.get("task_id")
    if task_id:
        thread = _fetch_until_budget(
            lambda limit: load_task_thread_history(
                task_id=str(task_id),
                limit=limit,
                earliest_ts=policy.earliest_ts_allowed,
            ),
            read_settings().budget_tokens,
            agent.id,
        )
        if thread:
            return _finish_history(
                thread,
                agent.id,
                policy,
                token_model=token_model,
                scope_kind="task",
                scope_id=str(task_id),
            )

    channel_id = trigger.get("channel_id")
    if isinstance(channel_id, str) and channel_id.strip():
        history = _channel_history(
            channel_id.strip(),
            agent,
            policy,
            token_model=token_model,
            exclude_message_id=None,
        )
        if history:
            return history

    return _thread_history(
        ThreadScope.human_dm(agent.id),
        agent,
        earliest_ts=policy.earliest_ts_allowed,
        exclude_message_id=None,
    )


def _thread_history(
    scope: ThreadScope,
    agent: Agent,
    *,
    earliest_ts: Any,
    exclude_message_id: Any,
) -> list[dict[str, Any]]:
    """One DM's history: its conversation notes, then the verbatim tail.

    Also starts a background compaction when the window is over the start
    line; the turn never waits for it.
    """
    window = load_thread_window(
        scope,
        earliest_ts=earliest_ts,
        exclude_message_id=str(exclude_message_id or "").strip() or None,
        perspective_agent_id=agent.id,
    )
    maybe_schedule_compaction(scope, window)
    return window_entries(window)


def _channel_history(
    channel_id: str,
    agent: Agent,
    policy: Any,
    *,
    token_model: str | None,
    exclude_message_id: Any,
) -> list[dict[str, Any]]:
    """One channel's history, then task-side sticky slots.

    The window is the channel's notes plus its verbatim tail. Sticky slots get
    that window as ``visible`` and, as ``verbatim``, the window's rows plus up
    to ``_FETCH_STEP_ROWS`` rows before it, so a task-linked turn that was
    folded into the notes still counts as dropped and keeps its slots.
    """
    scope = ThreadScope.channel(channel_id)
    exclude = str(exclude_message_id or "").strip() or None
    window = load_thread_window(scope, exclude_message_id=exclude, perspective_agent_id=agent.id)
    maybe_schedule_compaction(scope, window)
    visible = window_entries(window)
    recent = db.get_formatted_channel_messages(
        channel_id,
        human_label=boss_label(),
        limit=len(window.rows) + _FETCH_STEP_ROWS,
    )
    verbatim = _exclude_source_message(recent, exclude)
    return compose_channel_sticky_slots(
        verbatim=verbatim,
        visible=visible,
        policy=policy,
        agent_id=agent.id,
        token_model=token_model,
    )


def _exclude_source_message(messages: list[dict[str, Any]], source_message_id: Any) -> list[dict[str, Any]]:
    source_id = str(source_message_id or "").strip()
    if not source_id:
        return messages
    return [message for message in messages if str(message.get("id") or "") != source_id]


def _load_prompt_notifications(agent_id: str, policy: Any) -> list[Notification]:
    rows = db.list_notifications(
        agent_id=agent_id,
        limit=3,
        prompt_visible=True,
        earliest_ts=policy.earliest_ts_allowed,
    )
    rows.reverse()
    return rows


def _finish_history(
    messages: list[dict[str, Any]],
    agent_id: str,
    policy: Any,
    *,
    token_model: str | None,
    scope_kind: str,
    scope_id: str,
    verbatim: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Apply the hard token window. Task threads then receive task-side slots.

    Only task and meeting history comes here: they are not compacted, so
    they keep a hard window at the per-thread history budget.
    """
    windowed = _apply_budget_window(messages, agent_id, read_settings().budget_tokens)
    if scope_kind != "task" or not str(scope_id).strip():
        return windowed
    return compose_task_sticky_slots(
        task_id=str(scope_id),
        verbatim=verbatim if verbatim is not None else messages,
        visible=windowed,
        policy=policy,
        agent_id=agent_id,
        token_model=token_model,
    )


def _apply_budget_window(
    messages: list[dict[str, Any]],
    agent_id: str,
    budget: int,
) -> list[dict[str, Any]]:
    """Keep the newest whole rows within ``budget`` tokens; the newest always stays."""
    kept_reversed: list[dict[str, Any]] = []
    tokens_used = 0
    for message in reversed(messages):
        size = entry_tokens(render_history_entry(message, agent_id))
        if kept_reversed and tokens_used + size > budget:
            break
        tokens_used += size
        kept_reversed.append(message)
    kept_reversed.reverse()
    return kept_reversed


def _fetch_until_budget(
    fetch: Callable[[int], list[dict[str, Any]]],
    budget: int,
    agent_id: str,
) -> list[dict[str, Any]]:
    """Read the newest rows in steps of ``_FETCH_STEP_ROWS`` until ``budget`` is met.

    ``fetch(limit)`` returns the newest ``limit`` rows, oldest first. Growth
    stops once the rows reach the token budget or a larger limit returns no
    more rows (the thread is exhausted).
    """
    limit = _FETCH_STEP_ROWS
    previous = -1
    while True:
        rows = fetch(limit)
        tokens = sum(entry_tokens(render_history_entry(row, agent_id)) for row in rows)
        if tokens >= budget or len(rows) <= previous:
            return rows
        previous = len(rows)
        limit += _FETCH_STEP_ROWS
