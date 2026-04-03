"""BossMod AI — Backend-owned prompt-history view assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import db
from core.agent_loop.task_thread_history import load_task_thread_history
from core.llm.client import count_tokens
from core.models import Agent, Notification


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
    fetch_limit = _history_fetch_limit(policy.last_n_histories)

    if trigger_type == "activity_resumed" and trigger.get("task_id"):
        thread = load_task_thread_history(
            task_id=str(trigger["task_id"]),
            limit=fetch_limit,
            earliest_ts=policy.earliest_ts_allowed,
        )
        return _apply_policy_window(thread, agent.id, policy, token_model=token_model)

    if trigger_type in {"task_assigned", "task_follow_up", "task_update"} and trigger.get("task_id"):
        thread = load_task_thread_history(
            task_id=str(trigger["task_id"]),
            limit=fetch_limit,
            earliest_ts=policy.earliest_ts_allowed,
            exclude_source_message_id=trigger.get("source_message_id"),
            exclude_source_task_event_id=trigger.get("source_task_event_id"),
        )
        return _apply_policy_window(thread, agent.id, policy, token_model=token_model)

    if trigger_type in ("human_chat", "watchdog_status_ping"):
        thread = db.get_human_chat_thread(
            agent.id,
            limit=fetch_limit,
            earliest_ts=policy.earliest_ts_allowed,
        )
        formatted = db.get_formatted_messages(thread, human_label="Human Operator")
        if trigger_type == "human_chat":
            formatted = _exclude_source_message(formatted, trigger.get("source_message_id"))
        return _apply_policy_window(formatted, agent.id, policy, token_model=token_model)

    if trigger_type == "peer_message" and trigger.get("from_agent"):
        thread = db.get_agent_direct_thread(
            agent.id,
            trigger["from_agent"],
            limit=fetch_limit,
            earliest_ts=policy.earliest_ts_allowed,
        )
        formatted = db.get_formatted_messages(thread, human_label="Human Operator")
        formatted = _exclude_source_message(formatted, trigger.get("source_message_id"))
        return _apply_policy_window(formatted, agent.id, policy, token_model=token_model)

    if trigger_type in {"channel_message", "channel_response"}:
        channel_id = trigger.get("channel_id")
        if channel_id:
            thread = db.get_formatted_channel_messages(
                channel_id,
                limit=fetch_limit,
            )
            thread = _exclude_source_message(thread, trigger.get("source_message_id"))
            return _apply_policy_window(thread, agent.id, policy, token_model=token_model)

    if trigger_type in {"session_message", "session_response"}:
        session_id = trigger.get("session_id")
        if session_id:
            thread = db.get_formatted_meeting_session_messages(
                session_id,
                limit=fetch_limit,
            )
            thread = _exclude_source_message(thread, trigger.get("source_message_id"))
            return _apply_policy_window(thread, agent.id, policy, token_model=token_model)

    if trigger_type == "activity_resumed":
        active = db.get_active_activity(agent.id)
        if active and active.kind == "meeting":
            session = db.get_active_meeting_session_for_agent(agent.id)
            if session is not None:
                thread = db.get_formatted_meeting_session_messages(
                    session.id,
                    limit=fetch_limit,
                )
                return _apply_policy_window(thread, agent.id, policy, token_model=token_model)
        if active and active.kind == "conversation":
            thread = db.get_human_chat_thread(
                agent.id,
                limit=fetch_limit,
                earliest_ts=policy.earliest_ts_allowed,
            )
            formatted = db.get_formatted_messages(thread, human_label="Human Operator")
            return _apply_policy_window(formatted, agent.id, policy, token_model=token_model)

    return []


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


def _apply_policy_window(
    messages: list[dict[str, Any]],
    agent_id: str,
    policy: Any,
    *,
    token_model: str | None = None,
) -> list[dict[str, Any]]:
    if policy.last_n_histories == 0:
        return []

    selected = list(messages[-policy.last_n_histories :]) if policy.last_n_histories else list(messages)
    max_tokens = policy.max_allowed_history_tokens
    if max_tokens <= 0:
        return selected

    kept_reversed: list[dict[str, Any]] = []
    tokens_used = 0
    for message in reversed(selected):
        entry_tokens = count_tokens(_render_history_entry(message, agent_id), model=token_model)
        if entry_tokens > 0 and kept_reversed and tokens_used + entry_tokens > max_tokens:
            break
        if entry_tokens > 0:
            tokens_used += entry_tokens
        kept_reversed.append(message)
    kept_reversed.reverse()
    return kept_reversed


def _render_history_entry(message: dict[str, Any], agent_id: str) -> str:
    if message.get("from_agent") == agent_id:
        return str(message.get("content") or "")
    sender = str(message.get("from_name") or "Unknown")
    content = str(message.get("content") or "")
    return f"[{sender}]: {content}"


def _history_fetch_limit(last_n_histories: int) -> int:
    base = max(last_n_histories, 30)
    return min(max(base * 4, 60), 300)
