"""BossMod AI — First-class human-facing notification projection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import db
from core.agent_loop.task_origins import consent_origin_channel_id
from core.models import Activity, Agent
from core.models.channel import ChannelArchivedError
from core.models.cli_policy import CLI_APPROVAL_KIND, CliApprovalRequest
from core.models.host_path_consent import (
    SHELL_EXECUTOR_CARD_COPY,
    SHELL_EXECUTOR_KIND,
    WORKSPACE_PREFERENCE_KIND,
    consent_turn_event,
)

NotificationKind = Literal[
    "receipt",
    "completion",
    "blocked",
    "handoff",
    "abandoned",
    "task_update",
    "host_path_consent",
    "cli_approval",
    "queue_visibility",
]

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
logger = logging.getLogger(__name__)

CLI_APPROVAL_CHROME_FAIL = (
    "Approval card could not be posted to the conversation. "
    "The command was not queued for operator review."
)
CLI_APPROVAL_CREATE_FAIL = (
    "Approval request could not be created. "
    "The command was not queued for operator review."
)


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
    approval_id: str | None = None
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

    approval = _build_approval_notification(agent=agent, trigger=trigger, result=result)
    if approval is not None:
        notifications.append(approval)

    return notifications


async def emit_chat_notifications(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    active_activity: Activity | None,
    action: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Persist projected chat/channel notifications and broadcast them.

    Host-path consent and CLI approval cards stay on one origin: the shared
    channel when ``channel_id`` is set, otherwise Focus/DM. Never both surfaces.
    """
    from core.runtime.events import runtime_events as manager

    already = {
        (item.get("content") or "").strip()
        for item in result.get("origin_status_messages") or []
        if isinstance(item, dict)
    }
    for notification in project_chat_notifications(
        agent=agent,
        trigger=trigger,
        active_activity=active_activity,
        action=action,
        result=result,
    ):
        if (notification.content or "").strip() in already:
            continue
        if notification.channel_id:
            channel_notification = persist_channel_notification(agent, notification)
            if not channel_notification:
                continue
            await manager.broadcast_channel_message(
                channel_id=channel_notification["channel_id"],
                content=channel_notification["content"],
                author_type=channel_notification["author_type"],
                author_name=channel_notification["author_name"],
                author_agent_id=channel_notification.get("author_agent_id"),
                message_id=channel_notification.get("message_id"),
                created_at=channel_notification.get("created_at"),
                notification_kind=channel_notification.get("notification_kind"),
                desk_path=channel_notification.get("desk_path"),
                task_id=channel_notification.get("task_id"),
                host_path_consent=channel_notification.get("host_path_consent"),
                cli_approval=channel_notification.get("cli_approval"),
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
            task_id=chat_notification.get("task_id"),
            host_path_consent=chat_notification.get("host_path_consent"),
            cli_approval=chat_notification.get("cli_approval"),
        )
        if chat_notification.get("feed_entry"):
            await manager.broadcast_feed_update(chat_notification["feed_entry"])


async def broadcast_origin_status_messages(result: dict[str, Any], *, agent: Agent) -> None:
    """Broadcast extra origin-thread lines that did not become the primary reply."""
    from core.runtime.events import runtime_events as manager

    primary_channel = result.get("channel_message")
    primary_chat = result.get("chat_message")
    for extra in result.get("origin_status_messages") or []:
        if extra is primary_channel or extra is primary_chat:
            continue
        if extra.get("channel_id"):
            await manager.broadcast_channel_message(
                channel_id=extra["channel_id"],
                content=extra["content"],
                author_type=extra.get("author_type") or "system",
                author_name=extra.get("author_name") or agent.name,
                author_agent_id=extra.get("author_agent_id"),
                message_id=extra.get("message_id"),
                created_at=extra.get("created_at"),
                notification_kind=extra.get("notification_kind"),
                desk_path=extra.get("desk_path"),
                task_id=extra.get("task_id"),
            )
            continue
        if extra.get("agent_id"):
            await manager.broadcast_chat_message(
                agent_id=extra["agent_id"],
                content=extra["content"],
                from_type=extra.get("from_type") or "system",
                from_name=extra.get("from_name") or agent.name,
                message_type=extra.get("message_type") or "system",
                message_id=extra.get("message_id"),
                created_at=extra.get("created_at"),
                notification_kind=extra.get("notification_kind"),
                desk_path=extra.get("desk_path"),
                task_id=extra.get("task_id"),
            )


def persist_chat_notification(agent: Agent, notification: ChatNotification) -> dict[str, Any]:
    """Persist one chat notification and return broadcast data.

    The returned dict includes a ``feed_entry`` key with the unified feed
    shape so the caller can broadcast it to the activity panel.
    """
    if notification.approval_id:
        existing = db.get_notification_for_approval(notification.approval_id)
        if existing is not None:
            return _chat_notification_payload(agent, notification, existing)
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
            label="open",
        )
    if notification.consent_id:
        db.create_notification_link(
            notification_id=stored.id,
            target_kind="host_path_consent",
            target_path=notification.consent_id,
            label="Host path consent",
        )
    if notification.approval_id:
        db.create_notification_link(
            notification_id=stored.id,
            target_kind="cli_approval",
            target_path=notification.approval_id,
            label="CLI approval",
        )

    return _chat_notification_payload(agent, notification, stored)


def persist_channel_notification(agent: Agent, notification: ChatNotification) -> dict[str, Any]:
    """Persist one shared-channel notification as a system transcript message.

    Completion / blocked / handoff cards stay transcript-only. They must not
    open a response round or enqueue peer ``channel_message`` wakes.
    """
    if not notification.channel_id:
        raise ValueError("channel notifications require a channel_id")
    if db.is_channel_archived(notification.channel_id):
        return {}
    if notification.approval_id:
        existing = db.get_channel_message_for_approval(notification.approval_id)
        if existing is not None:
            return _channel_notification_payload(agent, notification, existing)
    try:
        message = db.create_channel_message(
            channel_id=notification.channel_id,
            author_type="system",
            author_name=agent.name,
            content=notification.content,
            source_channel=notification.source_channel,
            author_agent_id=agent.id,
            notification_kind=notification.kind,
            consent_id=notification.consent_id,
            approval_id=notification.approval_id,
            desk_path=notification.desk_path,
            task_id=notification.task_id,
        )
    except ChannelArchivedError:
        return {}
    return _channel_notification_payload(agent, notification, message)


def _chat_notification_payload(
    agent: Agent,
    notification: ChatNotification,
    stored: Any,
) -> dict[str, Any]:
    """Broadcast payload for one Focus notification row."""
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
        "task_id": stored.task_id,
        "host_path_consent": (
            db.get_consent_request(notification.consent_id).as_card()
            if notification.consent_id and db.get_consent_request(notification.consent_id)
            else None
        ),
        "cli_approval": (
            db.get_cli_approval_request(notification.approval_id).as_card()
            if notification.approval_id and db.get_cli_approval_request(notification.approval_id)
            else None
        ),
        "feed_entry": feed_entry,
    }


def _channel_notification_payload(
    agent: Agent,
    notification: ChatNotification,
    message: Any,
) -> dict[str, Any]:
    """Broadcast payload for one shared-channel notification row."""
    del agent
    return {
        "channel_id": notification.channel_id,
        "content": message.content,
        "author_type": message.author_type,
        "author_name": message.author_name,
        "author_agent_id": message.author_agent_id,
        "message_id": message.id,
        "created_at": message.created_at,
        "notification_kind": notification.kind,
        "desk_path": getattr(message, "desk_path", None) or notification.desk_path,
        "task_id": getattr(message, "task_id", None) or notification.task_id,
        "host_path_consent": (
            db.get_consent_request(notification.consent_id).as_card()
            if notification.consent_id and db.get_consent_request(notification.consent_id)
            else None
        ),
        "cli_approval": (
            db.get_cli_approval_request(notification.approval_id).as_card()
            if notification.approval_id and db.get_cli_approval_request(notification.approval_id)
            else None
        ),
    }


def ensure_cli_approval_chrome(
    agent: Agent,
    approval: CliApprovalRequest,
    *,
    channel_id: str | None,
    source_channel: str = "chat",
) -> bool:
    """Persist origin-thread or Focus Approve/Reject chrome for one request.

    Returns True only when a pending card is actually on a conversation
    surface. Create-time callers must fail the CLI result if this is False.
    """
    try:
        notification = _approval_chrome_notification(
            agent,
            approval,
            channel_id=channel_id,
            source_channel=source_channel,
        )
        if notification.channel_id:
            persisted = persist_channel_notification(agent, notification)
            return bool(persisted) and db.has_approval_notification(approval.id)
        persist_chat_notification(agent, notification)
        return db.has_approval_notification(approval.id)
    except Exception:
        logger.exception("CLI approval chrome persist failed for %s", approval.id)
        return False


def _approval_chrome_notification(
    agent: Agent,
    approval: CliApprovalRequest,
    *,
    channel_id: str | None,
    source_channel: str,
) -> ChatNotification:
    """Build the in-thread / Focus card payload for one CLI approval."""
    command = str(approval.command or "").strip() or "a command"
    content = f"{agent.name} wants to run a command"
    if command != "a command":
        content = f"{agent.name} wants to run {command}"
    origin = (channel_id or "").strip() or None
    if origin and db.is_channel_archived(origin):
        origin = None
    return ChatNotification(
        kind=CLI_APPROVAL_KIND,
        content=content,
        source_channel=source_channel,
        policy="all",
        prompt_visibility=False,
        approval_id=approval.id,
        channel_id=origin,
    )


def _build_consent_notification(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    result: dict[str, Any],
) -> ChatNotification | None:
    """Return the in-chat host-path consent card when a new request is created."""
    if result.get("event") not in {
        "host_path_consent_required",
        "workspace_preference_required",
        "shell_executor_consent_required",
    }:
        return None
    if result.get("consent_reused"):
        return None
    card = result.get("host_path_consent") if isinstance(result.get("host_path_consent"), dict) else {}
    consent_id = str(card.get("id") or result.get("consent_request_id") or "").strip()
    if not consent_id:
        return None
    if db.has_consent_notification(consent_id):
        return None
    channel_id = _consent_channel_id(trigger, card)
    if channel_id and db.is_channel_archived(channel_id):
        return None
    if channel_id:
        bound = db.bind_consent_channel(consent_id, channel_id)
        if bound is not None:
            card = bound.as_card()
    grant_root = str(card.get("grant_root") or "").strip()
    path = str(card.get("path") or "host path")
    # Host-path waiters coalesce per grant root. Workspace preference is a
    # different question (clone / branch / edit-host / cancel) and must not
    # be swallowed by an unrelated host-path card on the same root — but two
    # nested preference writes under one grant share one card.
    if card.get("kind") == WORKSPACE_PREFERENCE_KIND:
        if _workspace_preference_card_already_open(
            grant_root, path, channel_id, consent_id
        ):
            return None
    elif card.get("kind") == SHELL_EXECUTOR_KIND:
        if _shell_executor_card_already_open(channel_id, consent_id):
            return None
    elif grant_root and _grant_root_already_has_card(grant_root, channel_id, consent_id):
        return None
    reason = str(card.get("reason") or "").strip()
    if card.get("kind") == SHELL_EXECUTOR_KIND:
        _, content = consent_turn_event(agent.name, card)
        if not content:
            content = f"{agent.name} {SHELL_EXECUTOR_CARD_COPY}"
    elif card.get("kind") == WORKSPACE_PREFERENCE_KIND:
        content = f"{agent.name} needs a workspace preference for {path}."
        if reason:
            content = f"{content} {reason}"
    else:
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
        channel_id=channel_id,
    )


def _build_approval_notification(
    *,
    agent: Agent,
    trigger: dict[str, Any],
    result: dict[str, Any],
) -> ChatNotification | None:
    """Return the in-thread CLI approval card so emit can persist + broadcast.

    Create-time chrome may already exist. Persist is idempotent; skipping here
    would drop the live WebSocket push.
    """
    if result.get("event") != "cli_approval_required":
        return None
    card = result.get("cli_approval") if isinstance(result.get("cli_approval"), dict) else {}
    approval_id = str(card.get("id") or result.get("approval_request_id") or "").strip()
    if not approval_id:
        return None
    approval = db.get_cli_approval_request(approval_id)
    if approval is None:
        return None
    channel_id = _consent_channel_id(trigger, card)
    if channel_id and db.is_channel_archived(channel_id):
        return None
    if channel_id:
        bound = db.bind_cli_approval_channel(approval_id, channel_id)
        if bound is not None:
            approval = bound
    return _approval_chrome_notification(
        agent,
        approval,
        channel_id=channel_id or approval.channel_id,
        source_channel=_notification_source_channel(trigger),
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
        from core.agent_loop.task_origin_mirrors import format_origin_status_line, openable_done_claim_path

        claim = payload.get("done_claim") if isinstance(payload.get("done_claim"), dict) else None
        fallback_path = deliverable_paths[0] if len(deliverable_paths) == 1 else None
        return ChatNotification(
            kind="completion",
            content=format_origin_status_line(
                kind="completion",
                agent=agent,
                task={"title": task_title},
                path=fallback_path,
                claim=claim,
            ),
            source_channel=str(payload.get("source_channel") or "chat"),
            policy=str(payload.get("policy") or "completion_blocked"),
            prompt_visibility=True,
            task_id=payload.get("task_id"),
            desk_path=openable_done_claim_path(claim=claim, path=fallback_path),
            channel_id=payload.get("channel_id"),
        )

    if kind == "blocked":
        from core.agent_loop.task_origin_mirrors import format_origin_status_line

        return ChatNotification(
            kind="task_update",
            content=format_origin_status_line(
                kind="waiting",
                agent=agent,
                task={"title": task_title},
                reason=reason,
            ),
            source_channel=str(payload.get("source_channel") or "chat"),
            policy=str(payload.get("policy") or "completion_blocked"),
            prompt_visibility=False,
            task_id=payload.get("task_id"),
            channel_id=payload.get("channel_id"),
        )

    if kind == "handoff":
        from core.agent_loop.task_origin_mirrors import format_origin_status_line

        target_name = str(payload.get("target_name") or "").strip()
        return ChatNotification(
            kind="handoff",
            content=format_origin_status_line(
                kind="rerouted",
                agent=agent,
                task={"title": task_title},
                reason=reason,
                target_name=target_name,
            ),
            source_channel=str(payload.get("source_channel") or "chat"),
            policy=str(payload.get("policy") or "completion_blocked"),
            prompt_visibility=True,
            task_id=payload.get("task_id"),
            channel_id=payload.get("channel_id"),
        )

    if kind == "abandoned":
        from core.agent_loop.task_origin_mirrors import format_origin_status_line

        return ChatNotification(
            kind="abandoned",
            content=format_origin_status_line(
                kind="cancelled",
                agent=agent,
                task={"title": task_title},
                reason=reason,
            ),
            source_channel=str(payload.get("source_channel") or "chat"),
            policy=str(payload.get("policy") or "completion_blocked"),
            prompt_visibility=True,
            task_id=payload.get("task_id"),
            channel_id=payload.get("channel_id"),
        )

    if kind == "waiting":
        from core.agent_loop.task_origin_mirrors import format_origin_status_line

        return ChatNotification(
            kind="task_update",
            content=format_origin_status_line(
                kind="waiting",
                agent=agent,
                task={"title": task_title},
                reason=reason,
            ),
            source_channel=str(payload.get("source_channel") or "chat"),
            policy=str(payload.get("policy") or "completion_blocked"),
            prompt_visibility=False,
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


def _consent_channel_id(trigger: dict[str, Any], card: dict[str, Any] | None = None) -> str | None:
    """Return the originating shared channel when consent was asked there.

    Thread-born work keeps the Assign origin stamp even when the live trigger
    is an execution/CLI resume that omitted ``channel_id``.
    """
    found = consent_origin_channel_id(trigger)
    if found:
        return found
    if isinstance(card, dict):
        raw = card.get("channel_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _grant_root_already_has_card(
    grant_root: str,
    channel_id: str | None,
    consent_id: str,
) -> bool:
    """Return True when another waiter in this scope already opened the card."""
    for sibling in db.list_pending_for_grant_root_scope(grant_root, channel_id):
        if sibling.id == consent_id:
            continue
        if db.has_consent_notification(sibling.id):
            return True
    return False


def _workspace_preference_card_already_open(
    grant_root: str,
    path: str,
    channel_id: str | None,
    consent_id: str,
) -> bool:
    """Return True when a nested preference write already has a live card."""
    from core.bm_cli.workspace_preference import workspace_preference_scopes_match

    current = db.get_consent_request(consent_id)
    agent_id = current.agent_id if current is not None else None
    pending = db.list_consent_requests(agent_id=agent_id, status="pending", limit=80)
    for sibling in pending:
        if sibling.id == consent_id:
            continue
        if (sibling.card_kind or "") != WORKSPACE_PREFERENCE_KIND:
            continue
        if channel_id and sibling.channel_id and sibling.channel_id != channel_id:
            continue
        if not workspace_preference_scopes_match(
            sibling, path=path, grant_root=grant_root
        ):
            continue
        if db.has_consent_notification(sibling.id):
            return True
    return False


def _shell_executor_card_already_open(
    channel_id: str | None,
    consent_id: str,
) -> bool:
    """Return True when another Shell Executor card is already in this thread."""
    current = db.get_consent_request(consent_id)
    agent_id = current.agent_id if current is not None else None
    pending = db.list_consent_requests(agent_id=agent_id, status="pending", limit=80)
    for sibling in pending:
        if sibling.id == consent_id:
            continue
        if (sibling.card_kind or "") != SHELL_EXECUTOR_KIND:
            continue
        if channel_id and sibling.channel_id and sibling.channel_id != channel_id:
            continue
        if db.has_consent_notification(sibling.id):
            return True
    return False


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
