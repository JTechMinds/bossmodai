"""BossMod AI — First-class human-facing notification projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import db
from core.models import Activity, Agent

NotificationKind = Literal["receipt", "completion", "blocked", "handoff", "abandoned", "host_path_consent"]

_DESTINATION_LABELS = {
    "desk": "desk",
    "meetingRoom": "Meeting Room",
    "breakRoom": "Break Room",
    "mainWorkspace": "Main Workspace",
    "southWorkspace": "South Workspace",
    "hallway": "hallway",
}

_HUMAN_VISIBLE_ACTIVITY_KINDS = {"conversation", "meeting"}
_RECEIPT_ACTIONS = {"walkTo", "attendMeeting", "remoteMeeting"}


@dataclass(frozen=True, slots=True)
class ChatNotification:
    """A deterministic human-facing notification projected by the runtime."""

    kind: NotificationKind
    content: str
    source_channel: str
    policy: str
    chat_visible: bool = True
    prompt_visibility: bool = False
    task_id: str | None = None
    activity_id: str | None = None
    desk_path: str | None = None
    consent_id: str | None = None
    channel_id: str | None = None


def project_chat_notifications(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    active_activity: Activity | None,
    action: dict[str, Any],
    result: dict[str, Any],
) -> list[ChatNotification]:
    """Project runtime outcomes into human-facing chat notifications."""
    notifications: list[ChatNotification] = []

    receipt = _build_receipt_notification(
        agent=agent,
        trigger=trigger,
        active_activity=active_activity,
        action=action,
        result=result,
    )
    if receipt is not None:
        notifications.append(receipt)

    task_notification = _build_task_notification(agent=agent, result=result)
    if task_notification is not None:
        notifications.append(task_notification)

    consent = _build_consent_notification(agent=agent, trigger=trigger, result=result)
    if consent is not None:
        notifications.append(consent)

    return notifications


async def emit_chat_notifications(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    active_activity: Activity | None,
    action: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Persist projected chat/channel notifications and broadcast them."""
    from core.runtime.events import runtime_events as manager

    for notification in project_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=active_activity,
        action=action,
        result=result,
    ):
        if notification.channel_id:
            channel_notification = persist_channel_notification(agent, notification)
            await manager.broadcast_channel_message(
                channel_id=channel_notification["channel_id"],
                content=channel_notification["content"],
                author_type=channel_notification["author_type"],
                author_name=channel_notification["author_name"],
                message_id=channel_notification.get("message_id"),
                created_at=channel_notification.get("created_at"),
            )
            continue
        chat_notification = persist_chat_notification(agent, notification)
        await manager.broadcast_chat_message(
            agent_id=chat_notification["agent_id"],
            content=chat_notification["content"],
            from_type=chat_notification["from_type"],
            from_name=chat_notification["from_name"],
            message_type=chat_notification.get("message_type"),
            message_id=chat_notification.get("message_id"),
            created_at=chat_notification.get("created_at"),
            notification_kind=chat_notification.get("notification_kind"),
            desk_path=chat_notification.get("desk_path"),
            host_path_consent=chat_notification.get("host_path_consent"),
        )
        if chat_notification.get("feed_entry"):
            await manager.broadcast_feed_update(chat_notification["feed_entry"])


def persist_chat_notification(agent: Agent, notification: ChatNotification) -> dict[str, Any]:
    """Persist one chat notification and return broadcast data.

    The returned dict includes a ``feed_entry`` key with the unified feed
    shape so the caller can broadcast it to the activity panel.
    """
    stored = db.create_notification(
        agent_id=agent.id,
        task_id=notification.task_id,
        activity_id=notification.activity_id,
        kind=notification.kind,
        content=notification.content,
        source_channel=notification.source_channel,
        policy=notification.policy,
        chat_visible=notification.chat_visible,
        prompt_visibility=notification.prompt_visibility,
    )
    if notification.desk_path:
        db.create_notification_link(
            notification_id=stored.id,
            target_kind="desk",
            target_path=notification.desk_path,
        )
    if notification.consent_id:
        db.create_notification_link(
            notification_id=stored.id,
            target_kind="host_path_consent",
            target_path=notification.consent_id,
            label="Host path consent",
        )

    feed_entry = db.normalize_notification_entry(
        {
            "id": stored.id,
            "kind": stored.kind,
            "content": stored.content,
            "agent_name": agent.name,
            "task_id": stored.task_id,
            "source_channel": stored.source_channel,
            "policy": stored.policy,
            "chat_visible": stored.chat_visible,
            "prompt_visibility": stored.prompt_visibility,
            "created_at": stored.created_at,
        },
        target_path=notification.desk_path,
    )

    return {
        "agent_id": agent.id,
        "content": stored.content,
        "from_type": "system",
        "from_name": agent.name,
        "message_type": "system",
        "message_id": stored.id,
        "created_at": stored.created_at,
        "notification_kind": notification.kind,
        "desk_path": notification.desk_path,
        "host_path_consent": (
            db.get_consent_request(notification.consent_id).as_card()
            if notification.consent_id and db.get_consent_request(notification.consent_id)
            else None
        ),
        "feed_entry": feed_entry,
    }


def persist_channel_notification(agent: Agent, notification: ChatNotification) -> dict[str, Any]:
    """Persist one shared-channel notification as a system transcript message.

    Completion / blocked / handoff cards stay transcript-only. They must not
    open a response round or enqueue peer ``channel_message`` wakes.
    """
    if not notification.channel_id:
        raise ValueError("channel notifications require a channel_id")
    message = db.create_channel_message(
        channel_id=notification.channel_id,
        author_type="system",
        author_name=agent.name,
        content=notification.content,
        source_channel=notification.source_channel,
    )
    return {
        "channel_id": notification.channel_id,
        "content": message.content,
        "author_type": message.author_type,
        "author_name": message.author_name,
        "message_id": message.id,
        "created_at": message.created_at,
    }


def _build_consent_notification(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    result: dict[str, Any],
) -> ChatNotification | None:
    """Return the in-chat host-path consent card when a new request is created."""
    if result.get("event") != "host_path_consent_required":
        return None
    if result.get("consent_reused"):
        return None
    card = result.get("host_path_consent") if isinstance(result.get("host_path_consent"), dict) else {}
    consent_id = str(card.get("id") or result.get("consent_request_id") or "").strip()
    if not consent_id:
        return None
    if db.has_consent_notification(consent_id):
        return None
    path = str(card.get("path") or "host path")
    reason = str(card.get("reason") or "").strip()
    content = f"{agent.name} needs host-path access: {path}."
    if reason:
        content = f"{content} {reason}"
    return ChatNotification(
        kind="host_path_consent",
        content=content,
        source_channel=_notification_source_channel(trigger),
        policy="all",
        prompt_visibility=False,
        consent_id=consent_id,
    )


def _build_receipt_notification(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    active_activity: Activity | None,
    action: dict[str, Any],
    result: dict[str, Any],
) -> ChatNotification | None:
    """Return the silent-action receipt notification when warranted."""
    if not _should_emit_receipt(trigger=trigger, active_activity=active_activity, action=action, result=result):
        return None

    action_name = action.get("action")
    if action_name == "walkTo":
        return ChatNotification(
            kind="receipt",
            content=f"{agent.name} is heading to the {_format_destination(action.get('destination'))}.",
            source_channel=_notification_source_channel(trigger),
            policy="all",
            prompt_visibility=False,
            activity_id=active_activity.id if active_activity else None,
        )
    if action_name == "attendMeeting":
        return ChatNotification(
            kind="receipt",
            content=f"{agent.name} joined the meeting.",
            source_channel=_notification_source_channel(trigger),
            policy="all",
            prompt_visibility=False,
            activity_id=active_activity.id if active_activity else None,
        )
    if action_name == "remoteMeeting":
        target_name = _resolve_target_name(result.get("detail", ""))
        if target_name:
            return ChatNotification(
                kind="receipt",
                content=f"{agent.name} started a remote meeting with {target_name}.",
                source_channel=_notification_source_channel(trigger),
                policy="all",
                prompt_visibility=False,
                activity_id=active_activity.id if active_activity else None,
            )
        return ChatNotification(
            kind="receipt",
            content=f"{agent.name} started a remote meeting.",
            source_channel=_notification_source_channel(trigger),
            policy="all",
            prompt_visibility=False,
            activity_id=active_activity.id if active_activity else None,
        )
    return None


def _build_task_notification(*, agent: Agent, result: dict[str, Any]) -> ChatNotification | None:
    """Return a task-lifecycle notification when the runtime says one is needed."""
    payload = result.get("chat_notification")
    if not isinstance(payload, dict) or not payload.get("human_visible"):
        return None

    kind = payload.get("kind")
    task_title = str(payload.get("task_title") or "task")
    reason = str(payload.get("reason") or "").strip()
    deliverables = payload.get("deliverables") or []
    deliverable_paths = [
        item.get("path")
        for item in deliverables
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"].strip()
    ]

    if kind == "completion":
        claim = payload.get("done_claim") if isinstance(payload.get("done_claim"), dict) else None
        claim_note = ""
        if claim:
            claim_type = str(claim.get("type") or "").strip()
            claim_path = str(claim.get("path") or "").strip()
            claim_evidence = str(claim.get("evidence") or "").strip()
            if claim_type == "artifact" and claim_path:
                claim_note = f" Claim: artifact {claim_path}."
            elif claim_type == "tests" and claim_evidence:
                claim_note = f" Claim: tests — {claim_evidence}."
            elif claim_type == "proof" and claim_evidence:
                claim_note = f" Claim: proof — {claim_evidence}."
            elif claim_type:
                claim_note = f" Claim: {claim_type}."
        if len(deliverable_paths) == 1:
            return ChatNotification(
                kind="completion",
                content=f'{agent.name} finished "{task_title}" and saved it to {deliverable_paths[0]}.{claim_note}',
                source_channel=str(payload.get("source_channel") or "chat"),
                policy=str(payload.get("policy") or "completion_blocked"),
                prompt_visibility=True,
                task_id=payload.get("task_id"),
                desk_path=deliverable_paths[0],
                channel_id=payload.get("channel_id"),
            )
        if len(deliverable_paths) > 1:
            return ChatNotification(
                kind="completion",
                content=f'{agent.name} finished "{task_title}" and saved {len(deliverable_paths)} deliverables.{claim_note}',
                source_channel=str(payload.get("source_channel") or "chat"),
                policy=str(payload.get("policy") or "completion_blocked"),
                prompt_visibility=True,
                task_id=payload.get("task_id"),
                channel_id=payload.get("channel_id"),
            )
        return ChatNotification(
            kind="completion",
            content=f'{agent.name} finished "{task_title}".{claim_note}',
            source_channel=str(payload.get("source_channel") or "chat"),
            policy=str(payload.get("policy") or "completion_blocked"),
            prompt_visibility=True,
            task_id=payload.get("task_id"),
            channel_id=payload.get("channel_id"),
        )

    if kind == "blocked":
        if reason:
            return ChatNotification(
                kind="blocked",
                content=f'{agent.name} is blocked on "{task_title}": {reason}',
                source_channel=str(payload.get("source_channel") or "chat"),
                policy=str(payload.get("policy") or "completion_blocked"),
                prompt_visibility=True,
                task_id=payload.get("task_id"),
                channel_id=payload.get("channel_id"),
            )
        return ChatNotification(
            kind="blocked",
            content=f'{agent.name} is blocked on "{task_title}".',
            source_channel=str(payload.get("source_channel") or "chat"),
            policy=str(payload.get("policy") or "completion_blocked"),
            prompt_visibility=True,
            task_id=payload.get("task_id"),
            channel_id=payload.get("channel_id"),
        )

    if kind == "handoff":
        target_name = str(payload.get("target_name") or "").strip()
        if target_name:
            return ChatNotification(
                kind="handoff",
                content=f'{agent.name} delegated "{task_title}" to {target_name}.',
                source_channel=str(payload.get("source_channel") or "chat"),
                policy=str(payload.get("policy") or "completion_blocked"),
                prompt_visibility=True,
                task_id=payload.get("task_id"),
                channel_id=payload.get("channel_id"),
            )
        return ChatNotification(
            kind="handoff",
            content=f'{agent.name} delegated "{task_title}".',
            source_channel=str(payload.get("source_channel") or "chat"),
            policy=str(payload.get("policy") or "completion_blocked"),
            prompt_visibility=True,
            task_id=payload.get("task_id"),
            channel_id=payload.get("channel_id"),
        )

    if kind == "abandoned":
        if reason:
            return ChatNotification(
                kind="abandoned",
                content=f'{agent.name} abandoned "{task_title}": {reason}',
                source_channel=str(payload.get("source_channel") or "chat"),
                policy=str(payload.get("policy") or "completion_blocked"),
                prompt_visibility=True,
                task_id=payload.get("task_id"),
                channel_id=payload.get("channel_id"),
            )
        return ChatNotification(
            kind="abandoned",
            content=f'{agent.name} abandoned "{task_title}".',
            source_channel=str(payload.get("source_channel") or "chat"),
            policy=str(payload.get("policy") or "completion_blocked"),
            prompt_visibility=True,
            task_id=payload.get("task_id"),
            channel_id=payload.get("channel_id"),
        )

    return None


def _should_emit_receipt(
    *,
    trigger: dict[str, Any],
    active_activity: Activity | None,
    action: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    """Return whether the current turn/action should emit a movement/meeting receipt."""
    action_name = action.get("action")
    if action_name not in _RECEIPT_ACTIONS:
        return False
    if result.get("event") in {"agent_error", "guardian_violation", "world_feedback"}:
        return False

    trigger_type = trigger.get("type")
    if trigger_type == "human_chat":
        return True
    if trigger_type != "activity_resumed":
        return False
    if not active_activity or active_activity.kind not in _HUMAN_VISIBLE_ACTIVITY_KINDS:
        return False
    if action_name == "walkTo" and bool((active_activity.metadata or {}).get("acknowledged_by_reply")):
        return False
    return True


def _format_destination(destination: Any) -> str:
    """Humanize a runtime destination identifier."""
    if not isinstance(destination, str):
        return "destination"
    return _DESTINATION_LABELS.get(destination, destination)


def _resolve_target_name(detail: str) -> str | None:
    """Extract the meeting target name from the result detail string."""
    prefix = " started remote meeting with "
    if prefix not in detail:
        return None
    tail = detail.split(prefix, 1)[1]
    if ":" in tail:
        tail = tail.split(":", 1)[0]
    name = tail.strip()
    return name or None


def _notification_source_channel(trigger: dict[str, Any]) -> str:
    """Normalize a trigger source channel for notification storage."""
    source_channel = str(trigger.get("source_channel") or "").strip()
    if source_channel in {"chat", "channel", "api", "slack", "telegram", "peer", "task", "work", "system"}:
        return source_channel
    trigger_type = trigger.get("type")
    if trigger_type == "peer_message":
        return "peer"
    if trigger_type == "task_follow_up":
        return "task"
    return "chat"
