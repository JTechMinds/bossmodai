"""BossMod AI — Execution action handlers.

Parses flat JSON execution actions from LLM responses and executes them.
These actions only carry out existing commitments. Direct requests are handled
by the decision runtime, not by creating work or movement directly from chat.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core import config
from core.agent_loop import activity_runtime
from core.agent_loop.activity_scheduler import (
    build_task_assigned_trigger,
    build_task_follow_up_trigger,
    build_task_update_trigger,
)
from core.agent_loop.deliverables import build_work_contract, missing_deliverables, summarize_deliverable
from core.agent_loop.message_delivery import (
    resolve_peer_message_type,
    source_channel_for_message_type,
)
from core.agent_loop.meeting_orchestrator import maybe_start_meeting_kickoff_round
from core.agent_loop.task_origins import (
    task_notification_channel_id_for_trigger,
    task_notification_policy_for_trigger,
    task_source_channel_for_trigger,
)
from core.agent_loop.task_roles import (
    default_task_owner_id,
    task_has_participant,
    task_assignment_reply_target,
    task_report_recipient_ids,
    task_thread_target,
)
from core.bm_cli import execute_bm_cli
from core.default_prompts import render_default_prompt
from core.llm.client import count_tokens
from core.models.message import HUMAN_SENDER_ID
from core.models import Agent, AgentState
from core.models.work_contract import WorkContract
from core.tasking.service import (
    append_task_event,
    create_or_bind_subtask,
    create_or_bind_task,
    list_open_child_tasks,
)
from core.world.pathfinding import find_path
from core.world.tilemap import DEFAULT_DESKS, DEFAULT_ROOMS, MAP_HEIGHT, MAP_WIDTH, get_room_at
import db

logger = logging.getLogger(__name__)

# Actions that end the current multi-turn loop
TERMINAL_ACTIONS = {"idle", "waiting", "complete", "blocked", "delegated", "abandoned"}

# camelCase destination names → internal room IDs
_DESTINATIONS = {
    "desk": None,  # special: resolved to agent's desk_x/desk_y
    "meetingRoom": "meeting_room",
    "breakRoom": "break_room",
    "mainWorkspace": "workspace_main",
    "southWorkspace": "workspace_south",
    "hallway": "hallway_main",
}

_VALID_MESSAGE_RECIPIENT_TYPES = {"human", "agent"}
_VALID_TASK_MESSAGE_KINDS = {"note", "status", "question", "review"}
_TASK_LIFECYCLE_ACTIONS = {"waiting", "complete", "blocked", "delegated", "abandoned"}
_ACTION_PROMPT_ALLOWED_PATHS = {"room_name", "target", "targets"}
_SUPPORTED_ACTIONS = {
    "bm_cli",
    "work",
    "message",
    "taskMessage",
    "delegateTask",
    "walkTo",
    "attendMeeting",
    "remoteMeeting",
    "idle",
    "waiting",
    "complete",
    "blocked",
    "delegated",
    "abandoned",
}
_MODEL_ACTION_TO_NAME = {
    "cli": "bm_cli",
    "work": "work",
    "socialmsg": "message",
    "taskmsg": "taskMessage",
    "assign": "delegateTask",
    "walk": "walkTo",
    "mtg": "meeting",
    "idle": "idle",
    "wait": "waiting",
    "done": "complete",
    "block": "blocked",
    "deleg": "delegated",
    "drop": "abandoned",
}
_MESSAGE_TARGET_TO_NAME = {"human": "human", "agent": "agent"}
_MEETING_MODE_TO_NAME = {"room": "attendMeeting", "remote": "remoteMeeting"}
_DESTINATION_CODE_TO_NAME = {
    "desk": "desk",
    "meeting": "meetingRoom",
    "break": "breakRoom",
    "main": "mainWorkspace",
    "south": "southWorkspace",
    "hall": "hallway",
}
_MAX_INLINE_FILE_DELIVERABLE_WORK_CHARS = 2000


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_action(raw_response: str) -> dict[str, Any]:
    """Extract a flat JSON action from the LLM response.

    Returns the full parsed dict. Returns ``_parse_failed`` action on failure
    so callers can distinguish "agent chose idle" from "LLM returned garbage".
    """
    text = raw_response.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("Failed to parse action JSON: %s", text[:200])
                return {"action": "_parse_failed", "thought": "Failed to parse response", "_raw_snippet": text[:200]}
        else:
            logger.warning("No JSON found in response: %s", text[:200])
            return {"action": "_parse_failed", "thought": "No action in response", "_raw_snippet": text[:200]}

    if not isinstance(parsed, dict):
        logger.warning("Parsed action is not an object: %s", parsed)
        return {"action": "_parse_failed", "thought": "", "_raw_snippet": "Action payload must be a JSON object"}

    try:
        parsed = _normalize_action_payload(parsed)
    except ValueError as exc:
        logger.warning("Invalid compact action payload: %s", exc)
        return {"action": "_parse_failed", "thought": _candidate_thought(parsed), "_raw_snippet": str(exc)[:200]}

    if "action" not in parsed:
        logger.warning("No 'action' key in response: %s", parsed)
        return {"action": "_parse_failed", "thought": _candidate_thought(parsed), "_raw_snippet": str(parsed)[:200]}

    if not isinstance(parsed["action"], str):
        logger.warning("Action is not a string (%s): %s", type(parsed["action"]).__name__, parsed)
        return {"action": "_parse_failed", "thought": _candidate_thought(parsed), "_raw_snippet": str(parsed)[:200]}

    validation_error = _validate_action_payload(parsed)
    if validation_error:
        logger.warning("Invalid action payload for %s: %s", parsed["action"], validation_error)
        return {
            "action": "_parse_failed",
            "thought": _candidate_thought(parsed),
            "_raw_snippet": f'{parsed["action"]}: {validation_error}'[:200],
        }

    return parsed


def _normalize_action_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the model-facing compact action JSON into canonical runtime fields."""
    if "act" not in payload:
        raise ValueError('missing "act"')
    extra_root = set(payload) - {"act", "data", "th"}
    if extra_root:
        raise ValueError(f'unexpected top-level keys: {", ".join(sorted(extra_root))}')

    extra = payload.get("data")
    if extra is None:
        extra = {}
    if not isinstance(extra, dict):
        raise ValueError('"data" must be an object when provided')
    extra_data = set(extra) - {"cmd", "body", "out", "to", "aid", "aids", "msg", "tid", "kind", "dst", "mode", "topic", "sum", "why", "task"}
    if extra_data:
        raise ValueError(f'unexpected data keys: {", ".join(sorted(extra_data))}')

    action_code = payload.get("act")
    if not isinstance(action_code, str) or action_code not in _MODEL_ACTION_TO_NAME:
        raise ValueError('invalid "act"')

    normalized: dict[str, Any] = {
        "action": _MODEL_ACTION_TO_NAME[action_code],
        "thought": payload.get("th", ""),
    }
    task = extra.get("task") or {}
    if task in ("", None):
        task = {}
    if not isinstance(task, dict):
        raise ValueError('"data.task" must be an object when provided')
    extra_task = set(task) - {"title", "desc", "outs"}
    if extra_task:
        raise ValueError(f'unexpected data.task keys: {", ".join(sorted(extra_task))}')

    match action_code:
        case "cli":
            normalized["command"] = extra.get("cmd")
            normalized["content"] = extra.get("body")
        case "work":
            normalized["output"] = extra.get("out")
        case "socialmsg":
            normalized["recipientType"] = _map_optional_code(extra.get("to"), _MESSAGE_TARGET_TO_NAME, "data.to")
            normalized["agentId"] = extra.get("aid")
            normalized["content"] = extra.get("msg")
        case "taskmsg":
            normalized["taskId"] = extra.get("tid")
            normalized["content"] = extra.get("msg")
            normalized["messageKind"] = extra.get("kind") or "note"
        case "assign":
            normalized["agentId"] = extra.get("aid")
            normalized["taskTitle"] = task.get("title")
            normalized["taskDescription"] = task.get("desc")
            normalized["deliverables"] = _normalize_compact_deliverables(task.get("outs"))
        case "walk":
            normalized["destination"] = _map_optional_code(extra.get("dst"), _DESTINATION_CODE_TO_NAME, "data.dst")
        case "mtg":
            meeting_mode = _map_optional_code(extra.get("mode"), _MEETING_MODE_TO_NAME, "data.mode")
            if meeting_mode is None:
                raise ValueError('missing "data.mode"')
            normalized["action"] = meeting_mode
            normalized["agentIds"] = extra.get("aids")
            if extra.get("aid") not in (None, ""):
                raise ValueError('"mtg" does not accept "data.aid"; use "data.aids" (list) even for one participant')
            normalized["topic"] = extra.get("topic")
        case "done":
            normalized["summary"] = extra.get("sum")
            normalized["followUpMessage"] = extra.get("msg")
        case "wait" | "block" | "drop":
            normalized["reason"] = extra.get("why")
            normalized["followUpMessage"] = extra.get("msg")
        case "deleg":
            normalized["agentId"] = extra.get("aid")
            normalized["followUpMessage"] = extra.get("msg")
        case "idle":
            pass

    return normalized


def _normalize_compact_deliverables(value: Any) -> Any:
    """Normalize compact outs payload into canonical deliverables."""
    if value in (None, ""):
        return None
    if not isinstance(value, list):
        raise ValueError('"data.task.outs" must be an array when provided')

    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('each item in "data.task.outs" must be an object')
        extra_item = set(item) - {"type", "path", "desc"}
        if extra_item:
            raise ValueError(f'unexpected deliverable keys: {", ".join(sorted(extra_item))}')
        if item.get("type") != "file":
            raise ValueError('deliverable "type" must be "file"')
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError('deliverable "path" must be a non-empty string')
        normalized.append(
            {
                "type": "file",
                "path": path,
                "description": item.get("desc"),
            }
        )
    return normalized


def _map_optional_code(value: Any, mapping: dict[str, str], field_name: str) -> str | None:
    """Map an optional compact enum code to its canonical value."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or value not in mapping:
        raise ValueError(f'invalid "{field_name}"')
    return mapping[value]


def _candidate_thought(payload: Any) -> str:
    """Extract the best available thought field from an action payload."""
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("th"), str):
        return payload.get("th", "")
    if isinstance(payload.get("thought"), str):
        return payload.get("thought", "")
    return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_action_payload(action: dict[str, Any]) -> str | None:
    """Validate shape and required fields for parsed actions."""
    action_name = action["action"]
    if action_name not in _SUPPORTED_ACTIONS:
        return f'unsupported action "{action_name}"'

    if action_name == "bm_cli":
        command = action.get("command")
        if not isinstance(command, str) or not command.strip():
            return '"bm_cli" requires a non-empty "command"'
        if action.get("content") is not None and not isinstance(action.get("content"), str):
            return '"bm_cli" "content" must be a string when provided'

    if action_name == "message":
        recipient_type = action.get("recipientType")
        if not isinstance(recipient_type, str) or recipient_type.strip().lower() not in _VALID_MESSAGE_RECIPIENT_TYPES:
            return 'missing or invalid "recipientType"'
        if recipient_type.strip().lower() == "agent":
            agent_id = action.get("agentId")
            if not isinstance(agent_id, str) or not agent_id.strip():
                return '"message" to an agent requires a non-empty "agentId"'
        else:
            if action.get("agentId") not in (None, ""):
                return '"message" to the human operator must not include "agentId"'
        content = action.get("content")
        if not isinstance(content, str) or not content.strip():
            return '"message" requires a non-empty "content"'

    if action_name == "taskMessage":
        task_id = action.get("taskId")
        if not isinstance(task_id, str) or not task_id.strip():
            return '"taskMessage" requires a non-empty "taskId"'
        content = action.get("content")
        if not isinstance(content, str) or not content.strip():
            return '"taskMessage" requires a non-empty "content"'
        message_kind = action.get("messageKind")
        if not isinstance(message_kind, str) or message_kind not in _VALID_TASK_MESSAGE_KINDS:
            return '"taskMessage" requires "messageKind" to be one of: note, status, question, review'

    if action_name == "delegateTask":
        agent_id = action.get("agentId")
        if not isinstance(agent_id, str) or not agent_id.strip():
            return '"delegateTask" requires a non-empty "agentId"'
        task_title = action.get("taskTitle")
        if not isinstance(task_title, str) or not task_title.strip():
            return '"delegateTask" requires a non-empty "taskTitle"'
        task_description = action.get("taskDescription")
        if not isinstance(task_description, str) or not task_description.strip():
            return '"delegateTask" requires a non-empty "taskDescription"'
        project = action.get("project")
        if project is not None and not isinstance(project, str):
            return '"delegateTask" "project" must be a string when provided'
        deliverables = action.get("deliverables")
        if deliverables is not None:
            if not isinstance(deliverables, list):
                return '"delegateTask" "deliverables" must be a list when provided'
            try:
                WorkContract(deliverables=deliverables)
            except Exception as exc:
                return f'"delegateTask" invalid deliverables: {exc}'

    if action_name == "remoteMeeting":
        if action.get("agentId") not in (None, ""):
            return '"remoteMeeting" no longer accepts "agentId"; use "agentIds" (list) even for one participant'
        agent_ids = action.get("agentIds")
        has_many = isinstance(agent_ids, list) and any(isinstance(item, str) and item.strip() for item in agent_ids)
        if not has_many:
            return '"remoteMeeting" requires a non-empty "agentIds" list'

    if action_name == "delegated":
        agent_id = action.get("agentId")
        if not isinstance(agent_id, str) or not agent_id.strip():
            return '"delegated" requires a non-empty "agentId"'

    if action_name == "remoteMeeting" and action.get("agentIds") is not None:
        agent_ids = action.get("agentIds")
        if not isinstance(agent_ids, list) or not all(isinstance(item, str) and item.strip() for item in agent_ids):
            return '"remoteMeeting" "agentIds" must be a list of non-empty strings when provided'
        if len(agent_ids) != 1:
            return '"remoteMeeting" requires "agentIds" to contain exactly one participant'

    if action_name == "attendMeeting" and action.get("agentIds") is not None:
        agent_ids = action.get("agentIds")
        if not isinstance(agent_ids, list) or not all(isinstance(item, str) and item.strip() for item in agent_ids):
            return '"attendMeeting" "agentIds" must be a list of non-empty strings when provided'

    if action_name == "waiting":
        reason = action.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return '"waiting" requires a non-empty "reason"'

    if action_name in _TASK_LIFECYCLE_ACTIONS:
        follow_up_message = action.get("followUpMessage")
        if follow_up_message is not None and (not isinstance(follow_up_message, str) or not follow_up_message.strip()):
            return f'"{action_name}" "followUpMessage" must be a non-empty string when provided'

    if action_name == "attendMeeting" and action.get("agentId") not in (None, ""):
        return '"attendMeeting" no longer accepts "agentId"; use "agentIds" (list) even for one participant'

    if action_name in _TASK_LIFECYCLE_ACTIONS and action.get("taskId") not in (None, ""):
        return f'"{action_name}" must not include "taskId"; the runtime binds the active task'

    return None


def _resolve_agent_by_id(agent_id: Any) -> Agent | None:
    """Resolve an explicit agent ID from an action payload."""
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    return db.get_agent(agent_id.strip())


def _resolve_task_lifecycle_target(
    agent: Agent,
    action: dict[str, Any],
    *,
    action_name: str,
) -> tuple[str | None, str | None]:
    """Resolve the task targeted by a lifecycle action.

    Task lifecycle actions always act on the currently bound active task.
    """
    active_task_id = activity_runtime.get_active_task_id(agent.id)
    if not active_task_id:
        return None, f'"{action_name}" requires an active task'

    return active_task_id, None


def _build_trigger_request(
    *,
    agent_id: str,
    trigger_type: str,
    source_channel: str,
    payload: dict[str, Any],
    task_id: str | None = None,
) -> dict[str, Any]:
    """Build a normalized trigger request emitted by an action."""
    return {
        "agent_id": agent_id,
        "trigger_type": trigger_type,
        "source_channel": source_channel,
        "payload": payload,
        "task_id": task_id,
    }


def _resolve_token_model(agent: Agent, action: dict[str, Any]) -> str | None:
    """Resolve the tokenizer model for action-side token accounting."""
    explicit_model = action.get("_token_model")
    if isinstance(explicit_model, str) and explicit_model.strip():
        return explicit_model.strip()
    for field in (
        agent.model_work,
        agent.model_social,
        agent.model_reasoning,
        agent.model_extraction,
        agent.model_self_queue,
    ):
        if field and field.strip():
            return field.strip()
    return config.get("default_model_work")


def _count_action_tokens(agent: Agent, action: dict[str, Any], text: str) -> int:
    """Count tokens for persisted artifacts/messages without heuristic fallback."""
    return count_tokens(text, model=_resolve_token_model(agent, action))


def _task_is_human_visible(task: Any | None) -> bool:
    """Return whether task lifecycle notifications should reach the human chat."""
    return bool(
        task
        and task.requester_id == HUMAN_SENDER_ID
        and (task.notification_policy or "none") != "none"
    )


def _normalize_delegate_work_contract(
    *,
    agent: Agent,
    action: dict[str, Any],
) -> WorkContract | None:
    """Normalize any delegateTask deliverables against the delegator's CLI cwd."""
    deliverables = action.get("deliverables")
    if not isinstance(deliverables, list) or not deliverables:
        return None
    cli_state = db.ensure_agent_cli_state(agent.id)
    return build_work_contract(
        deliverables,
        agent_storage_key=agent.storage_key,
        cwd=cli_state.cwd,
    )


def _append_task_stakeholder_reports(
    *,
    result: dict[str, Any],
    actor: Agent,
    state: AgentState,
    task: Any | None,
    content: str,
    skip_recipient_ids: set[str] | None = None,
    attention_kind: str | None = None,
    source_task_event_id: str | None = None,
) -> None:
    """Send durable task updates to the requesting/owning agents."""
    if task is None or not isinstance(content, str) or not content.strip():
        return

    skipped = skip_recipient_ids or set()
    recipients = task_report_recipient_ids(task, actor_id=actor.id)
    if not recipients:
        return

    trigger_requests = result.setdefault("trigger_requests", [])
    for recipient_id in recipients:
        db.create_notification(
            agent_id=recipient_id,
            task_id=task.id,
            kind="task_update",
            content=content.strip(),
            source_channel="task",
            policy="none",
            chat_visible=False,
            prompt_visibility=False,
        )
    if attention_kind is None:
        return

    reply_target = task_assignment_reply_target(task, assignee_id=actor.id)
    if reply_target["kind"] != "agent" or not reply_target["agent_id"]:
        return
    if reply_target["agent_id"] in skipped:
        return
    effective_kind = _effective_attention_kind(attention_kind, content)
    requires_response = _attention_kind_requires_response(effective_kind) or _content_asks_question(content)
    builder = build_task_follow_up_trigger if requires_response else build_task_update_trigger
    trigger_requests.append(
        builder(
            task,
            recipient_agent_id=reply_target["agent_id"],
            from_agent=actor.id,
            from_name=actor.name,
            content=content.strip(),
            attention_kind=str(effective_kind or "").strip() or "task_update",
            source_task_event_id=source_task_event_id,
            source_channel="work",
        )
    )


def _task_requires_conversational_follow_up(task: Any | None, *, actor_id: str) -> bool:
    """Return whether the current task should send a natural follow-up reply."""
    if task is None:
        return False
    if task.source_channel not in {"chat", "peer", "meeting", "channel"}:
        return False
    if task.notification_channel_id and task.source_channel == "channel":
        return True
    target = task_assignment_reply_target(task, assignee_id=actor_id)
    return target["kind"] in {"human", "agent"}


def _append_task_follow_up_message(
    *,
    result: dict[str, Any],
    actor: Agent,
    state: AgentState,
    task: Any | None,
    content: str | None,
    attention_kind: str | None = None,
    source_trigger_id: str | None = None,
) -> set[str]:
    """Persist one natural follow-up message for a task lifecycle update."""
    if task is None or not isinstance(content, str) or not content.strip():
        return set()

    if task.notification_channel_id and task.source_channel == "channel":
        message = db.create_channel_message(
            channel_id=task.notification_channel_id,
            author_type="agent",
            author_agent_id=actor.id,
            author_name=actor.name,
            content=content.strip(),
            source_channel="channel",
        )
        result["channel_message"] = {
            "channel_id": task.notification_channel_id,
            "content": message.content,
            "author_type": message.author_type,
            "author_name": actor.name,
            "message_id": message.id,
            "created_at": message.created_at,
        }
        return set()

    target = task_assignment_reply_target(task, assignee_id=actor.id)
    if target["kind"] == "human":
        message = db.create_message(
            from_agent=actor.id,
            to_agent=HUMAN_SENDER_ID,
            content=content.strip(),
            message_type="social",
            location_x=state.x,
            location_y=state.y,
        )
        result["chat_message"] = {
            "agent_id": actor.id,
            "content": message.content,
            "from_type": "agent",
            "from_name": actor.name,
            "message_type": message.message_type,
            "message_id": message.id,
            "created_at": message.created_at,
        }
        return set()

    if target["kind"] == "agent" and target["agent_id"]:
        db.create_notification(
            agent_id=target["agent_id"],
            task_id=task.id,
            kind="task_update",
            content=content.strip(),
            source_channel="task",
            policy="none",
            chat_visible=False,
            prompt_visibility=False,
        )
        persisted = append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=actor.id,
            author_name=actor.name,
            event_type="answer" if attention_kind == "completion_report" else "status_update",
            content=content.strip(),
            source_trigger_id=source_trigger_id,
        )
        if attention_kind is None:
            return {target["agent_id"]}
        effective_kind = _effective_attention_kind(attention_kind, content)
        requires_response = _attention_kind_requires_response(effective_kind) or _content_asks_question(content)
        builder = build_task_follow_up_trigger if requires_response else build_task_update_trigger
        result.setdefault("trigger_requests", []).append(
            builder(
                task,
                recipient_agent_id=target["agent_id"],
                from_agent=actor.id,
                from_name=actor.name,
                content=content.strip(),
                attention_kind=str(effective_kind or "").strip() or "task_update",
                source_task_event_id=persisted.id if persisted is not None else None,
                source_channel="work",
            )
        )
        return {target["agent_id"]}

    return set()


def _task_message_event_type(message_kind: str) -> str:
    """Map execution task-message kinds onto durable task-event types."""
    if message_kind == "question":
        return "clarification"
    if message_kind == "review":
        return "status_update"
    if message_kind == "status":
        return "status_update"
    return "comment"


def _task_message_attention_kind(message_kind: str) -> str | None:
    """Return the attention kind for one task-thread execution message."""
    if message_kind == "question":
        return "question"
    if message_kind == "review":
        return "review_request"
    return None


_CHILD_UPDATES_TO_PARENT_EVENT_TYPES = {
    "completion": "status_update",
    "blocker": "status_update",
}

_RESPONSE_REQUIRED_ATTENTION_KINDS = {
    "question",
    "review_request",
    "decision_needed",
    "clarification_requested",
}


def _attention_kind_requires_response(attention_kind: str | None) -> bool:
    """Return whether one task attention kind should require a follow-up response."""
    return str(attention_kind or "").strip().lower() in _RESPONSE_REQUIRED_ATTENTION_KINDS


def _content_asks_question(content: str | None) -> bool:
    """Heuristic: treat content with a question mark as requiring a response."""
    return isinstance(content, str) and "?" in content


def _effective_attention_kind(attention_kind: str | None, content: str | None) -> str | None:
    """Prefer explicit question attention when the message asks one."""
    if attention_kind is None:
        return None
    if _content_asks_question(content):
        return "question"
    return attention_kind


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

async def execute_action(
    action: dict[str, Any],
    agent: Agent,
    state: AgentState,
    trigger: dict[str, Any] | None = None,
    token_model: str | None = None,
) -> dict[str, Any]:
    """Execute a flat action dict and return the result."""
    if token_model:
        action = {**action, "_token_model": token_model}
    action_type = action["action"]

    handler = _ACTION_HANDLERS.get(action_type)
    if not handler:
        logger.warning("Unknown action '%s' from agent %s", action_type, agent.name)
        return {"event": "status_changed", "detail": f"Unknown action: {action_type}", "agent_name": agent.name}

    return await handler(agent, state, action, trigger)


# ---------------------------------------------------------------------------
# Handlers — each receives the full flat action dict
# ---------------------------------------------------------------------------

async def _handle_work(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Agent produces work output. Must be at a workspace."""
    output = action.get("output", "")
    if not output:
        return {"event": "status_changed", "detail": "Empty work output", "agent_name": agent.name}
    task_id = activity_runtime.get_active_task_id(agent.id)
    if task_id is None:
        return {"event": "agent_error", "detail": "No active work activity is bound", "agent_name": agent.name}

    # Desk rule: must be in a workspace
    room = get_room_at(state.x, state.y)
    if not room or room["room_type"] not in ("workspace",):
        room_name = room["name"] if room else "unknown area"
        return {
            "event": "world_feedback",
            "detail": render_default_prompt(
                "internal_action_requires_workspace",
                {"room_name": room_name},
                allowed_paths=_ACTION_PROMPT_ALLOWED_PATHS,
            ),
            "agent_name": agent.name,
            "feedback_code": "walk_to_desk_first",
        }

    task = db.get_task(task_id)
    if task and task.status == "accepted":
        db.update_task(task.id, status="active", status_note=None, watchdog_pinged_at=None)
        task = db.get_task(task.id)

    pending_deliverables = missing_deliverables(
        agent_id=agent.id,
        agent_storage_key=agent.storage_key,
        task=task,
    )
    file_deliverables = [item for item in pending_deliverables if item.type == "file" and item.path]
    if file_deliverables and len(output) > _MAX_INLINE_FILE_DELIVERABLE_WORK_CHARS:
        if len(file_deliverables) == 1:
            target = summarize_deliverable(file_deliverables[0])
            detail = render_default_prompt(
                "internal_action_large_work_single_file_guidance",
                {"target": target},
                allowed_paths=_ACTION_PROMPT_ALLOWED_PATHS,
            )
        else:
            targets = ", ".join(summarize_deliverable(item) for item in file_deliverables[:3])
            if len(file_deliverables) > 3:
                targets = f"{targets}, ..."
            detail = render_default_prompt(
                "internal_action_large_work_multi_file_guidance",
                {"targets": targets},
                allowed_paths=_ACTION_PROMPT_ALLOWED_PATHS,
            )
        return {
            "event": "world_feedback",
            "detail": detail,
            "agent_name": agent.name,
            "missing_deliverables": [item.model_dump() for item in pending_deliverables],
        }

    db.create_message(
        from_agent=agent.id,
        to_agent=None,
        content=output,
        message_type="work",
        location_x=state.x,
        location_y=state.y,
        token_count=_count_action_tokens(agent, action, output),
    )

    result = {
        "event": "agent_updated",
        "detail": f"{agent.name} produced work output ({len(output)} chars)",
        "agent_name": agent.name,
    }
    if pending_deliverables:
        result["missing_deliverables"] = [item.model_dump() for item in pending_deliverables]
    return result


async def _handle_bm_cli(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded BossMod CLI query and return a turn-local result."""
    command = str(action.get("command") or "").strip()
    content = action.get("content")
    cli_result = execute_bm_cli(
        agent,
        state,
        command,
        content if isinstance(content, str) else None,
        trigger_type=(trigger or {}).get("type") if isinstance(trigger, dict) else None,
    )
    result = {
        "event": "bm_cli_result" if cli_result.ok else "bm_cli_error",
        "detail": cli_result.detail,
        "agent_name": agent.name,
        "cli_prompt_content": cli_result.prompt_content,
        "suppress_world_broadcast": True,
        "suppress_activity_broadcast": not cli_result.approval_required,
    }
    if cli_result.approval_required:
        result["approval_required"] = True
        result["approval_request_id"] = cli_result.approval_request_id
        result["event"] = "cli_approval_required"
        result["detail"] = f"{agent.name} requests approval: {command}"
    return result


async def _handle_message(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a message to another agent. Works from any location."""
    recipient_type = (action.get("recipientType") or "").strip().lower()
    content = action.get("content", "")

    if not content:
        return {"event": "status_changed", "detail": "Empty message content", "agent_name": agent.name}

    target = None
    if recipient_type == "human":
        to_agent_id = HUMAN_SENDER_ID
        to_display = "Human Operator"
    else:
        target = _resolve_agent_by_id(action.get("agentId"))
        if target is None:
            return {"event": "status_changed", "detail": "Agent not found for provided agentId", "agent_name": agent.name}
        to_agent_id = target.id
        to_display = target.name

    active = activity_runtime.get_active_activity(agent.id)
    if (
        target is not None
        and active is not None
        and active.kind in {"assignment", "work"}
    ):
        return _reject_work_lane_agent_chat(
            agent=agent,
            target=target,
            active_task_id=active.task_id,
        )

    if to_agent_id == HUMAN_SENDER_ID:
        message_type = "social" if state.status == "social_active" else "work"
    else:
        message_type = resolve_peer_message_type(state=state, trigger=trigger)
    msg = db.create_message(
        from_agent=agent.id,
        to_agent=to_agent_id,
        content=content,
        message_type=message_type,
        location_x=state.x,
        location_y=state.y,
        token_count=_count_action_tokens(agent, action, content),
    )

    result = {
        "event": "message_sent",
        "detail": f"{agent.name} → {to_display}: {content[:80]}{'...' if len(content) > 80 else ''}",
        "agent_name": agent.name,
    }
    if to_agent_id == HUMAN_SENDER_ID:
        result["chat_message"] = {
            "agent_id": agent.id,
            "content": content,
            "from_type": "agent",
            "from_name": agent.name,
            "message_type": message_type,
            "message_id": msg.id,
            "created_at": msg.created_at,
        }
    elif target:
        result["trigger_requests"] = [_build_trigger_request(
            agent_id=target.id,
            trigger_type="peer_message",
            source_channel=source_channel_for_message_type(message_type),
            payload={
                "content": content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": message_type,
                "source_message_id": msg.id,
            },
        )]
    return result


def _reject_work_lane_agent_chat(
    *,
    agent: Agent,
    target: Agent,
    active_task_id: str | None,
) -> dict[str, Any]:
    """Return deterministic repair feedback when work execution tries to use generic coworker chat."""
    if isinstance(active_task_id, str) and active_task_id.strip():
        existing_children = list_open_child_tasks(parent_task_id=active_task_id, assigned_to=target.id)
        if len(existing_children) == 1:
            child = existing_children[0]
            return {
                "event": "world_feedback",
                "detail": (
                    f'There is already an open task thread with {target.name} on "{child.title}" '
                    f'({child.id}). Use "taskmsg" with that task id instead of "socialmsg".'
                ),
                "agent_name": agent.name,
                "task_id": child.id,
                "expected_action": "taskMessage",
            }
        if len(existing_children) > 1:
            return {
                "event": "world_feedback",
                "detail": (
                    f'There is more than one open delegated task for {target.name} under the current work. '
                    'Use "taskmsg" with the specific task id from Task Board, or use "assign" only if this is a new task.'
                ),
                "agent_name": agent.name,
                "task_ids": [task.id for task in existing_children],
                "expected_action": "taskMessage",
            }

    return {
        "event": "world_feedback",
        "detail": (
            'Agent-to-agent communication during work execution must stay in the task system. '
            'Use "assign" to create delegated work, or "taskmsg" to continue an existing task thread. '
            'Use "socialmsg" only for non-task social chat.'
        ),
        "agent_name": agent.name,
        "expected_actions": ["delegateTask", "taskMessage"],
    }


async def _handle_task_message(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a task-thread event and only wake the other side when needed."""
    task_id = str(action.get("taskId") or "").strip()
    content = str(action.get("content") or "").strip()
    message_kind = str(action.get("messageKind") or "note").strip().lower()
    task = db.get_task(task_id)
    if task is None:
        return {"event": "world_feedback", "detail": f'Task "{task_id}" no longer exists.', "agent_name": agent.name}
    if not task_has_participant(task, agent_id=agent.id):
        return {
            "event": "world_feedback",
            "detail": "You can only write on task threads for tasks you participate in.",
            "agent_name": agent.name,
        }

    persisted = append_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=agent.id,
        author_name=agent.name,
        event_type=_task_message_event_type(message_kind),
        content=content,
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )

    target = task_thread_target(task, actor_id=agent.id)
    if target["kind"] == "human":
        message = db.create_message(
            from_agent=agent.id,
            to_agent=HUMAN_SENDER_ID,
            content=content,
            message_type="social",
            location_x=state.x,
            location_y=state.y,
            token_count=_count_action_tokens(agent, action, content),
        )
        return {
            "event": "message_sent",
            "detail": f"{agent.name} updated the human on task \"{task.title}\"",
            "agent_name": agent.name,
            "chat_message": {
                "agent_id": agent.id,
                "content": message.content,
                "from_type": "agent",
                "from_name": agent.name,
                "message_type": message.message_type,
                "message_id": message.id,
                "created_at": message.created_at,
            },
        }

    target_agent_id = target.get("agent_id")
    if not isinstance(target_agent_id, str) or not target_agent_id.strip():
        return {
            "event": "world_feedback",
            "detail": "No valid task-thread recipient is available for that task update.",
            "agent_name": agent.name,
        }
    target_agent = db.get_agent(target_agent_id)
    if target_agent is None:
        return {
            "event": "world_feedback",
            "detail": "The task-thread recipient no longer exists.",
            "agent_name": agent.name,
        }

    db.create_notification(
        agent_id=target_agent_id,
        task_id=task.id,
        kind="task_update",
        content=content,
        source_channel="task",
        policy="none",
        chat_visible=False,
        prompt_visibility=False,
    )
    attention_kind = _task_message_attention_kind(message_kind)
    if attention_kind is None:
        return {
            "event": "message_sent",
            "detail": f'{agent.name} added a passive {message_kind} update to "{task.title}" for {target_agent.name}',
            "agent_name": agent.name,
        }

    return {
        "event": "message_sent",
        "detail": f'{agent.name} requested a task-thread response from {target_agent.name} on "{task.title}"',
        "agent_name": agent.name,
        "trigger_requests": [
            build_task_follow_up_trigger(
                task,
                recipient_agent_id=target_agent_id,
                from_agent=agent.id,
                from_name=agent.name,
                content=content,
                attention_kind=attention_kind,
                source_task_event_id=persisted.id if persisted is not None else None,
                source_channel="work",
            )
        ],
    }


async def _handle_delegate_task(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an explicit task assignment for another agent."""
    target = _resolve_agent_by_id(action.get("agentId"))
    if target is None:
        return {"event": "agent_error", "detail": "No valid delegate target specified", "agent_name": agent.name}
    if target.id == agent.id:
        return {"event": "agent_error", "detail": "Cannot delegate a task to yourself", "agent_name": agent.name}

    parent_task_id = activity_runtime.get_active_task_id(agent.id)
    parent_task = db.get_task(parent_task_id) if parent_task_id else None
    if parent_task is not None:
        owner_id = str(parent_task.owner_id or "").strip()
        if owner_id and owner_id != agent.id:
            owner = db.get_agent(owner_id)
            owner_name = owner.name if owner is not None else owner_id
            return {
                "event": "world_feedback",
                "detail": (
                    "Only the task owner can delegate new child tasks under an existing delegated workstream. "
                    f'This task is owned by {owner_name}. Use "taskmsg" (kind=question/review) on this task thread '
                    "to ask for clarification or request a new delegation."
                ),
                "agent_name": agent.name,
                "task_id": parent_task.id,
                "expected_actions": ["taskMessage"],
            }

    task_title = str(action.get("taskTitle") or "").strip()
    task_description = str(action.get("taskDescription") or "").strip()
    project = action.get("project")
    project_name = str(project).strip() if isinstance(project, str) and project.strip() else (parent_task.project if parent_task else None)
    work_contract = _normalize_delegate_work_contract(agent=agent, action=action)
    source_channel = task_source_channel_for_trigger(trigger or {}) or (parent_task.source_channel if parent_task else None)
    notification_policy = task_notification_policy_for_trigger(trigger or {}) or (parent_task.notification_policy if parent_task else None)
    notification_channel_id = (
        task_notification_channel_id_for_trigger(trigger or {}) or (parent_task.notification_channel_id if parent_task else None)
    )
    owner_id = default_task_owner_id(
        assignee_id=target.id,
        requester_id=agent.id,
        created_by=agent.id,
        parent_task=parent_task,
    )

    if parent_task is not None:
        creation = create_or_bind_subtask(
            parent_task=parent_task,
            title=task_title,
            description=task_description,
            project=project_name,
            assigned_to=target.id,
            requester_id=agent.id,
            owner_id=owner_id,
            created_by=agent.id,
            work_contract=work_contract,
            source_channel=source_channel,
            notification_policy=notification_policy,
            notification_channel_id=notification_channel_id,
            audit_author_name=agent.name,
            audit_author_type="agent",
            audit_author_agent_id=agent.id,
            audit_source_trigger_id=(trigger or {}).get("trigger_id"),
        )
    else:
        creation = create_or_bind_task(
            title=task_title,
            description=task_description,
            project=project_name,
            assigned_to=target.id,
            requester_id=agent.id,
            owner_id=owner_id,
            created_by=agent.id,
            parent_task_id=None,
            work_contract=work_contract,
            source_channel=source_channel,
            notification_policy=notification_policy,
            notification_channel_id=notification_channel_id,
            audit_author_name=agent.name,
            audit_author_type="agent",
            audit_author_agent_id=agent.id,
            audit_source_trigger_id=(trigger or {}).get("trigger_id"),
        )
    task = creation.task

    if creation.outcome != "create_new_task":
        return {
            "event": "world_feedback",
            "detail": (
                f'There is already an open task thread with {target.name} on "{task.title}" ({task.id}). '
                'Use "taskmsg" with that task id instead of delegating a duplicate assignment.'
            ),
            "agent_name": agent.name,
            "task_id": task.id,
            "expected_action": "taskMessage",
        }

    result = {
        "event": "status_changed",
        "detail": f'{agent.name} assigned "{task.title}" to {target.name}',
        "agent_name": agent.name,
        "trigger_requests": [build_task_assigned_trigger(task)] if task.status == "pending" else [],
        "chat_notification": {
            "kind": "handoff",
            "task_title": task.title,
            "target_name": target.name,
            "task_id": parent_task.id if parent_task else task.id,
            "source_channel": task.source_channel or "peer",
            "channel_id": task.notification_channel_id,
            "policy": task.notification_policy or "none",
            "human_visible": _task_is_human_visible(task),
        },
    }
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=f'Assigned subtask "{task.title}" to {target.name}.',
    )
    append_task_event(
        task_id=task.id,
        author_type="agent",
        author_agent_id=agent.id,
        author_name=agent.name,
        event_type="assignment",
        content=f'Assigned "{task.title}" to {target.name}.',
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    return result


async def _handle_walk_to(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Move avatar to a destination."""
    destination = action.get("destination", "")

    if destination not in _DESTINATIONS:
        return {
            "event": "world_feedback",
            "detail": f"Unknown destination: {destination}. Valid: {', '.join(_DESTINATIONS.keys())}",
            "agent_name": agent.name,
        }

    # Resolve destination to coordinates
    if destination == "desk":
        # Use agent's assigned desk
        if agent.desk_x is not None and agent.desk_y is not None:
            dest_x, dest_y = agent.desk_x, agent.desk_y
        else:
            # Find first unassigned desk chair
            desk = next((d for d in DEFAULT_DESKS), None)
            if desk:
                dest_x, dest_y = desk["chair_xy"]
            else:
                return {"event": "world_feedback", "detail": "No desk available", "agent_name": agent.name}
    else:
        room_id = _DESTINATIONS[destination]
        room = next((r for r in DEFAULT_ROOMS if r["id"] == room_id), None)
        if not room:
            return {"event": "world_feedback", "detail": f"Room not found: {room_id}", "agent_name": agent.name}
        bounds = room["bounds"]
        dest_x = (bounds[0] + bounds[2]) // 2
        dest_y = (bounds[1] + bounds[3]) // 2

    if not (0 <= dest_x < MAP_WIDTH and 0 <= dest_y < MAP_HEIGHT):
        return {"event": "world_feedback", "detail": f"Destination out of bounds", "agent_name": agent.name}

    path = find_path(state.x, state.y, dest_x, dest_y)
    if not path:
        return {"event": "world_feedback", "detail": f"No path to {destination}", "agent_name": agent.name}

    if len(path) <= 1:
        destination_label = destination
        if destination == "desk":
            destination_label = "your desk"
        return {
            "event": "world_feedback",
            "detail": f"You're already at {destination_label}. Choose the next action.",
            "agent_name": agent.name,
        }

    active = activity_runtime.get_active_activity(agent.id)
    if trigger and trigger.get("type") == "social" and active is None:
        active = db.create_runtime_activity(
            agent_id=agent.id,
            kind="social",
            title="Social interaction",
            detail=trigger.get("content"),
        )
        activity_runtime.refresh_agent_status(agent.id)

    activity_runtime.start_movement_activity(
        agent.id,
        destination=destination,
        parent_activity_id=active.id if active else None,
        detail=f"Walking to {destination}",
        metadata={
            "destination": destination,
            "destination_x": dest_x,
            "destination_y": dest_y,
        },
    )

    return {
        "event": "agent_moved",
        "detail": f"{agent.name} walking to {destination} ({len(path)-1} steps)",
        "agent_name": agent.name,
        "path": path,
        "agent_id": agent.id,
        "activity_extra": {
            "agent_id": agent.id,
            "path": path,
            "tiles_per_second": config.get_float("movement_tiles_per_second") or 4.0,
        },
    }


async def _handle_remote_meeting(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a remote meeting from the agent's current location."""
    target = None
    agent_ids = []
    if isinstance(action.get("agentIds"), list):
        agent_ids = [item.strip() for item in action.get("agentIds") if isinstance(item, str) and item.strip()]
    if len(agent_ids) == 1:
        target = _resolve_agent_by_id(agent_ids[0])
    topic = action.get("topic", "")

    if target is None:
        return {"event": "status_changed", "detail": "No valid meeting participant specified", "agent_name": agent.name}

    room = get_room_at(state.x, state.y)
    if not room or room["room_type"] != "workspace":
        room_name = room["name"] if room else "unknown area"
        return {
            "event": "world_feedback",
            "detail": render_default_prompt(
                "internal_action_requires_workspace",
                {"room_name": room_name},
                allowed_paths=_ACTION_PROMPT_ALLOWED_PATHS,
            ),
            "agent_name": agent.name,
            "feedback_code": "walk_to_desk_first",
        }

    # Send a meeting request message
    meeting_content = f"Remote meeting requested: {topic}" if topic else "Remote meeting requested"
    msg = db.create_message(
        from_agent=agent.id,
        to_agent=target.id,
        content=meeting_content,
        message_type="meeting",
        location_x=state.x,
        location_y=state.y,
        token_count=_count_action_tokens(agent, action, meeting_content),
    )

    active = activity_runtime.get_active_activity(agent.id)
    if active and active.kind == "meeting":
        current_detail = str(active.detail or "").strip()
        next_detail = current_detail or meeting_content
        db.update_activity(
            active.id,
            title=topic or active.title,
            detail=next_detail,
            metadata={**active.metadata, "topic": topic, "meeting_mode": "remote"}
            if topic
            else {**active.metadata, "meeting_mode": "remote"},
        )
    else:
        parent = activity_runtime.get_active_activity(agent.id)
        if parent and parent.kind in {"assignment", "break", "conversation", "social", "work"}:
            db.update_activity(parent.id, status="paused")
        activity_runtime.start_meeting_activity(
            agent.id,
            title=topic or "Remote meeting",
            detail=meeting_content,
            parent_activity_id=parent.id if parent else None,
            metadata={"topic": topic, "meeting_mode": "remote"} if topic else {"meeting_mode": "remote"},
        )

    return {
        "event": "meeting_started",
        "detail": f"{agent.name} started remote meeting with {target.name}" + (f": {topic}" if topic else ""),
        "agent_name": agent.name,
        "trigger_requests": [_build_trigger_request(
            agent_id=target.id,
            trigger_type="peer_message",
            source_channel="work",
            payload={
                "content": meeting_content,
                "from_agent": agent.id,
                "from_name": agent.name,
                "message_type": "meeting",
                "source_message_id": msg.id,
            },
        )],
    }


async def _handle_attend_meeting(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attend an in-person meeting from the meeting room."""
    topic = (action.get("topic") or "").strip()
    agent_ids: list[str] = []
    room = get_room_at(state.x, state.y)

    if not room or room["room_type"] != "meeting":
        room_name = room["name"] if room else "unknown area"
        return {
            "event": "world_feedback",
            "detail": f"You're in the {room_name}. Walk to the meetingRoom first.",
            "agent_name": agent.name,
        }

    if isinstance(action.get("agentIds"), list):
        agent_ids = [item.strip() for item in action.get("agentIds") if isinstance(item, str) and item.strip()]

    if not agent_ids:
        active = activity_runtime.get_active_activity(agent.id)
        existing_session_id = None
        if active and active.kind == "meeting":
            existing_session_id = str((active.metadata or {}).get("session_id") or "").strip() or None

        if existing_session_id:
            # Joining an already-orchestrated meeting (invited participants may arrive before others).
            pass
        else:
            existing_session = db.get_active_meeting_session_by_room(room["id"])
            if existing_session:
                meta = db.get_meeting_session_meta(existing_session.id)
                if meta is not None:
                    # Joining an orchestrated meeting session even if you're early/alone.
                    pass
                else:
                    other_participants = [
                        participant
                        for participant in db.list_active_meeting_participants(room["id"])
                        if str(participant.get("id") or "").strip() and str(participant.get("id")) != agent.id
                    ]
                    if not other_participants:
                        return {
                            "event": "world_feedback",
                            "detail": (
                                "No one else is currently in the Meeting Room. "
                                'If you were asked to meet with someone, invite them by re-running `mtg` with `data.mode="room"` '
                                "and the teammate's `data.aids` (list), or send them a `socialmsg` asking them to join the Meeting Room. "
                                "If you don't actually need a meeting right now, end the meeting commitment with `idle`."
                            ),
                            "agent_name": agent.name,
                            "feedback_code": "meeting_requires_participant",
                        }
            else:
                other_participants = [
                    participant
                    for participant in db.list_active_meeting_participants(room["id"])
                    if str(participant.get("id") or "").strip() and str(participant.get("id")) != agent.id
                ]
                if not other_participants:
                    return {
                        "event": "world_feedback",
                        "detail": (
                            "No one else is currently in the Meeting Room. "
                            'If you were asked to meet with someone, invite them by re-running `mtg` with `data.mode="room"` '
                            "and the teammate's `data.aids` (list), or send them a `socialmsg` asking them to join the Meeting Room. "
                            "If you don't actually need a meeting right now, end the meeting commitment with `idle`."
                        ),
                        "agent_name": agent.name,
                        "feedback_code": "meeting_requires_participant",
                    }

    meeting_content = f"In-person meeting in Meeting Room: {topic}" if topic else "In-person meeting in Meeting Room"
    msg = db.create_message(
        from_agent=agent.id,
        to_agent=None,
        content=meeting_content,
        message_type="meeting",
        location_x=state.x,
        location_y=state.y,
        token_count=_count_action_tokens(agent, action, meeting_content),
    )

    detail = f"{agent.name} joined an in-person meeting"
    if topic:
        detail += f": {topic}"

    result = {
        "event": "meeting_started",
        "detail": detail,
        "agent_name": agent.name,
    }
    session_title = topic or "In-person meeting"
    active = activity_runtime.get_active_activity(agent.id)
    session = None
    if active and active.kind == "meeting":
        session_id_hint = str((active.metadata or {}).get("session_id") or "").strip()
        if session_id_hint:
            session = db.get_meeting_session(session_id_hint)
    if session is None:
        session = db.ensure_room_meeting_session(
            room["id"],
            title=session_title,
            created_by_agent_id=agent.id,
        )

    if active and active.kind == "meeting":
        metadata = {**active.metadata, "session_id": session.id}
        current_detail = str(active.detail or "").strip()
        next_detail = current_detail or meeting_content
        db.update_activity(
            active.id,
            title=topic or active.title,
            detail=next_detail,
            metadata={**metadata, "topic": topic} if topic else metadata,
        )
        current_meeting = db.get_activity(active.id) or active
    else:
        parent = activity_runtime.get_active_activity(agent.id)
        if parent and parent.kind in {"assignment", "break", "conversation", "social", "work"}:
            db.update_activity(parent.id, status="paused")
        current_meeting = activity_runtime.start_meeting_activity(
            agent.id,
            title=session_title,
            detail=meeting_content,
            parent_activity_id=parent.id if parent else None,
            metadata={"session_id": session.id, "topic": topic} if topic else {"session_id": session.id},
        )

    current_metadata = current_meeting.metadata or {}
    if not current_metadata.get("session_join_announced"):
        session_message = db.create_meeting_session_message(
            session_id=session.id,
            author_type="system",
            author_name="BossMod",
            content=f"{agent.name} joined the meeting.",
            source_channel="meeting",
        )
        db.update_activity(
            current_meeting.id,
            metadata={**current_metadata, "session_id": session.id, "session_join_announced": True},
        )
        result["meeting_message"] = {
            "agent_id": agent.id,
            "session_id": session.id,
            "content": session_message.content,
            "author_type": session_message.author_type,
            "author_name": session_message.author_name,
            "message_id": session_message.id,
            "created_at": session_message.created_at,
        }
    if agent_ids:
        # Orchestrated meeting: create a durable context packet + invite triggers.
        now = datetime.now(timezone.utc)
        meta = db.get_meeting_session_meta(session.id)
        if meta is None:
            context_summary = topic or (active.title if active else "") or "Meeting"
            context_payload = {
                "topic": topic,
                "purpose": (active.detail if active else "") or meeting_content,
                "host_agent_id": agent.id,
                "host_name": agent.name,
                "room_id": room["id"],
                "meeting_mode": "room",
                "created_at": now.isoformat(),
            }
            packet = db.create_meeting_context_packet(
                session_id=session.id,
                summary=context_summary,
                payload=context_payload,
            )
            db.upsert_meeting_session_meta(
                session_id=session.id,
                host_agent_id=agent.id,
                meeting_mode="room",
                phase="assembling",
                context_packet_id=str(packet.get("id")),
            )
            db.upsert_meeting_session_participant(session_id=session.id, agent_id=agent.id, state="arrived", required=True)
            for invited_id in sorted({*agent_ids}):
                if invited_id == agent.id:
                    continue
                db.upsert_meeting_session_participant(
                    session_id=session.id,
                    agent_id=invited_id,
                    state="invited",
                    required=True,
                )
            roster = db.list_meeting_participant_details(session.id)
            roster_names = ", ".join([str(item.get("name") or "") for item in roster if item.get("name")]) or "unknown"
            db.create_meeting_session_message(
                session_id=session.id,
                author_type="system",
                author_name="BossMod",
                content=(
                    f"MEETING PRE-READ\n"
                    f"topic: {context_summary}\n"
                    f"host: {agent.name}\n"
                    f"required participants: {roster_names}\n"
                    f"notes: arrive in the Meeting Room; accept/decline invite with a reason.\n"
                ),
                source_channel="meeting",
            )
            invite_content = (
                f'Meeting invite from {agent.name}: "{context_summary}". '
                f"Please accept or decline with a reason. If you accept, walk to the Meeting Room and join."
            )
            result["trigger_requests"] = [
                _build_trigger_request(
                    agent_id=invited_id,
                    trigger_type="meeting_invite",
                    source_channel="meeting",
                    payload={
                        "content": invite_content,
                        "from_agent": agent.id,
                        "from_name": agent.name,
                        "session_id": session.id,
                        "meeting_title": session_title,
                        "meeting_mode": "room",
                        "room_id": room["id"],
                        "context_summary": context_summary,
                        "context_packet_id": str(packet.get("id")),
                    },
                )
                for invited_id in sorted({*agent_ids})
                if invited_id != agent.id
            ]
        else:
            result["detail"] = f"{agent.name} is waiting for invited participants to arrive"

    # If this is an orchestrated meeting session, update arrival state and kick off the first structured round
    # once everyone is accounted for.
    meta = db.get_meeting_session_meta(session.id)
    if meta is not None:
        now = datetime.now(timezone.utc)
        participant = db.get_meeting_session_participant(session.id, agent.id)
        if participant is not None and participant.get("state") != "arrived":
            db.update_meeting_session_participant_state(
                session_id=session.id,
                agent_id=agent.id,
                state="arrived",
                arrived_at=now,
            )
            db.create_meeting_session_message(
                session_id=session.id,
                author_type="system",
                author_name="BossMod",
                content=f"{agent.name} arrived in the Meeting Room.",
                source_channel="meeting",
            )

        kickoff_requests = maybe_start_meeting_kickoff_round(session_id=session.id)
        for req in kickoff_requests:
            result.setdefault("trigger_requests", []).append(req)
    return result


async def _handle_idle(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Yield the current turn without changing the active work commitment."""
    active = activity_runtime.get_active_activity(agent.id)
    if active and active.kind == "work":
        return {
            "event": "agent_error",
            "detail": 'Idle is not valid while a task is active. Use "wait", "done", "block", or keep working.',
            "agent_name": agent.name,
        }

    if active and active.kind != "work":
        activity_runtime.complete_activity(active.id, detail=active.detail)
    activity_runtime.refresh_agent_status(agent.id)
    return {
        "event": "status_changed",
        "detail": f"{agent.name} is idle",
        "agent_name": agent.name,
    }


async def _handle_waiting(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pause the current task in a waiting state until another event resumes it."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="waiting")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    reason = action.get("reason", "")

    task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if _task_requires_conversational_follow_up(task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "wait" action.',
            "agent_name": agent.name,
        }

    paused = activity_runtime.pause_active_work(agent.id, reason or "Waiting on a dependency.", task_status="waiting")
    if paused is None:
        return {"event": "agent_error", "detail": '"wait" requires an active task', "agent_name": agent.name}

    task = db.get_task(task_id)
    result = {
        "event": "status_changed",
        "detail": f'{agent.name} is waiting on "{task.title if task else "the current task"}"' + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
    }
    if task is not None:
        append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="status_update",
            content=reason or f'Waiting on "{task.title}".',
            source_trigger_id=(trigger or {}).get("trigger_id"),
        )
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=follow_up_message,
        attention_kind=None,
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=(f'Waiting on "{task.title}": {reason}' if task and reason else f'Waiting on "{task.title}".' if task else ""),
        skip_recipient_ids=skipped,
        attention_kind=None,
    )
    return result


async def _handle_complete(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark current task as complete."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="complete")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    summary = action.get("summary", "")
    active = activity_runtime.get_active_work_activity(agent.id)
    task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if task is not None:
        open_children = [
            child
            for child in list_open_child_tasks(parent_task_id=task.id)
            if child.status not in {"blocked", "stalled"}
        ]
        if open_children:
            child_titles = ", ".join(f'"{child.title}"' for child in open_children[:3])
            if len(open_children) > 3:
                child_titles += ", ..."
            return {
                "event": "world_feedback",
                "detail": (
                    "This coordination task still has open delegated child work. "
                    f"Resolve or replan {child_titles} before completing the parent task."
                ),
                "agent_name": agent.name,
                "task_ids": [child.id for child in open_children],
            }
    if _task_requires_conversational_follow_up(task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "done" action.',
            "agent_name": agent.name,
        }
    pending_deliverables = missing_deliverables(
        agent_id=agent.id,
        agent_storage_key=agent.storage_key,
        task=task,
    )
    if pending_deliverables:
        first = summarize_deliverable(pending_deliverables[0])
        return {
            "event": "world_feedback",
            "detail": f'Required deliverable missing: {first}. Satisfy all declared deliverables before complete.',
            "agent_name": agent.name,
            "missing_deliverables": [item.model_dump() for item in pending_deliverables],
        }

    db.update_task(
        task_id,
        status="complete",
        completion_summary=summary or None,
        status_note=None,
        watchdog_pinged_at=None,
    )
    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=summary or active.detail)
    else:
        activity_runtime.refresh_agent_status(agent.id)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} completed task" + (f" — {summary}" if summary else ""),
        "agent_name": agent.name,
        "chat_notification": {
            "kind": "completion",
            "task_title": task.title if task else "task",
            "deliverables": [item.model_dump() for item in (task.work_contract.deliverables if task and task.work_contract else [])],
            "task_id": task.id if task else None,
            "source_channel": task.source_channel if task else "chat",
            "channel_id": task.notification_channel_id if task else None,
            "policy": task.notification_policy if task else "completion_blocked",
            "human_visible": _task_is_human_visible(task),
        },
    }
    if task is not None:
        completion_event = append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="completion",
            content=summary or f'Completed "{task.title}".',
            source_trigger_id=(trigger or {}).get("trigger_id"),
        )
        parent = db.get_task(task.parent_task_id) if task.parent_task_id else None
        if parent is not None:
            completion_detail = None
            if isinstance(follow_up_message, str) and follow_up_message.strip():
                completion_detail = follow_up_message.strip()
            elif summary:
                completion_detail = summary
            parent_note = (
                f'Child task "{task.title}" completed by {agent.name}: {completion_detail}'.strip()
                if completion_detail
                else f'Child task "{task.title}" completed by {agent.name}.'
            )
            parent_event = append_task_event(
                task_id=parent.id,
                author_type="system",
                author_name="BossMod",
                event_type=_CHILD_UPDATES_TO_PARENT_EVENT_TYPES["completion"],
                content=parent_note,
                source_trigger_id=(trigger or {}).get("trigger_id"),
            )
            if parent.assigned_to and parent.assigned_to != agent.id:
                result.setdefault("trigger_requests", []).append(
                    build_task_update_trigger(
                        parent,
                        recipient_agent_id=parent.assigned_to,
                        from_agent=agent.id,
                        from_name=agent.name,
                        content=parent_note,
                        attention_kind="completion_report",
                        source_task_event_id=parent_event.id if parent_event is not None else None,
                        source_channel="work",
                    )
                )
    else:
        completion_event = None
        parent = None
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=follow_up_message,
        attention_kind="completion_report" if parent is None else None,
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=(f'Completed "{task.title}": {summary}' if task and summary else f'Completed "{task.title}".' if task else ""),
        skip_recipient_ids=skipped,
        attention_kind="completion_report" if parent is None else None,
        source_task_event_id=completion_event.id if completion_event is not None else None,
    )
    return result


async def _handle_blocked(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark current task as blocked."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="blocked")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    reason = action.get("reason", "")

    task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if _task_requires_conversational_follow_up(task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "block" action.',
            "agent_name": agent.name,
        }
    db.update_task(
        task_id,
        status="blocked",
        status_note=reason or None,
        completion_summary=None,
        watchdog_pinged_at=None,
    )
    active = activity_runtime.pause_active_work(agent.id, reason or "Blocked.", task_status="blocked")
    if active is None:
        activity_runtime.refresh_agent_status(agent.id)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} blocked" + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
        "chat_notification": {
            "kind": "blocked",
            "task_title": task.title if task else "task",
            "reason": reason,
            "task_id": task.id if task else None,
            "source_channel": task.source_channel if task else "chat",
            "channel_id": task.notification_channel_id if task else None,
            "policy": task.notification_policy if task else "completion_blocked",
            "human_visible": _task_is_human_visible(task),
        },
    }
    parent = None
    if task is not None:
        blocker_event = append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="blocker",
            content=reason or f'Blocked on "{task.title}".',
            source_trigger_id=(trigger or {}).get("trigger_id"),
        )
        if task.parent_task_id:
            parent = db.get_task(task.parent_task_id)
            if parent is not None:
                blocker_detail = None
                if isinstance(follow_up_message, str) and follow_up_message.strip():
                    blocker_detail = follow_up_message.strip()
                elif reason:
                    blocker_detail = reason
                parent_note = (
                    f'Child task "{task.title}" blocked by {agent.name}: {blocker_detail}'.strip()
                    if blocker_detail
                    else f'Child task "{task.title}" blocked by {agent.name}.'
                )
                parent_event = append_task_event(
                    task_id=parent.id,
                    author_type="system",
                    author_name="BossMod",
                    event_type=_CHILD_UPDATES_TO_PARENT_EVENT_TYPES["blocker"],
                    content=parent_note,
                    source_trigger_id=(trigger or {}).get("trigger_id"),
                )
                if parent.assigned_to and parent.assigned_to != agent.id:
                    result.setdefault("trigger_requests", []).append(
                        build_task_update_trigger(
                            parent,
                            recipient_agent_id=parent.assigned_to,
                            from_agent=agent.id,
                            from_name=agent.name,
                            content=parent_note,
                            attention_kind="blocker",
                            source_task_event_id=parent_event.id if parent_event is not None else None,
                            source_channel="work",
                        )
                    )
    else:
        blocker_event = None
        parent = None
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=follow_up_message,
        attention_kind="blocker" if parent is None else None,
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=(f'Blocked on "{task.title}": {reason}' if task and reason else f'Blocked on "{task.title}".' if task else ""),
        skip_recipient_ids=skipped,
        attention_kind="blocker" if parent is None else None,
        source_task_event_id=blocker_event.id if blocker_event is not None else None,
    )
    return result


async def _handle_delegated(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delegate current task to another agent."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="delegated")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    target = _resolve_agent_by_id(action.get("agentId"))
    if target is None:
        return {"event": "status_changed", "detail": "No valid delegate target specified", "agent_name": agent.name}
    original_task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if _task_requires_conversational_follow_up(original_task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "deleg" action.',
            "agent_name": agent.name,
        }

    db.update_task(
        task_id,
        status="delegated",
        status_note=f"Delegated to {target.name}",
        watchdog_pinged_at=None,
    )

    # Create a child task for the target agent (vision doc: delegation
    # creates a formal task record with its own watchdog)
    if original_task:
        child = create_or_bind_subtask(
            parent_task=original_task,
            title=original_task.title,
            description=original_task.description,
            project=original_task.project,
            assigned_to=target.id,
            requester_id=agent.id,
            owner_id=default_task_owner_id(
                assignee_id=target.id,
                requester_id=agent.id,
                created_by=agent.id,
                parent_task=original_task,
            ),
            created_by=agent.id,
            work_contract=original_task.work_contract,
            source_channel=original_task.source_channel,
            notification_policy=original_task.notification_policy,
            notification_channel_id=original_task.notification_channel_id,
            audit_author_name=agent.name,
            audit_author_type="agent",
            audit_author_agent_id=agent.id,
            audit_source_trigger_id=(trigger or {}).get("trigger_id"),
        ).task
    else:
        child = None

    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=f"Delegated to {target.name}")
    else:
        activity_runtime.refresh_agent_status(agent.id)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} delegated task to {target.name}",
        "agent_name": agent.name,
        "chat_notification": {
            "kind": "handoff",
            "task_title": original_task.title if original_task else "task",
            "target_name": target.name,
            "task_id": original_task.id if original_task else None,
            "source_channel": original_task.source_channel if original_task else "chat",
            "channel_id": original_task.notification_channel_id if original_task else None,
            "policy": original_task.notification_policy if original_task else "completion_blocked",
            "human_visible": _task_is_human_visible(original_task),
        },
    }
    if original_task is not None:
        append_task_event(
            task_id=original_task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="status_update",
            content=f'Delegated "{original_task.title}" to {target.name}.',
            source_trigger_id=(trigger or {}).get("trigger_id"),
        )
    if child and child.status == "pending":
        result["trigger_requests"] = [build_task_assigned_trigger(child)]
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=original_task,
        content=follow_up_message,
        attention_kind="handoff",
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=original_task,
        content=(
            f'Delegated "{original_task.title}" to {target.name}.'
            if original_task
            else ""
        ),
        skip_recipient_ids=skipped,
    )
    return result


async def _handle_abandoned(
    agent: Agent,
    state: AgentState,
    action: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Abandon current task."""
    task_id, error = _resolve_task_lifecycle_target(agent, action, action_name="abandoned")
    if error:
        return {"event": "agent_error", "detail": error, "agent_name": agent.name}
    reason = action.get("reason", "")

    task = db.get_task(task_id)
    follow_up_message = action.get("followUpMessage")
    if _task_requires_conversational_follow_up(task, actor_id=agent.id) and not (
        isinstance(follow_up_message, str) and follow_up_message.strip()
    ):
        return {
            "event": "world_feedback",
            "detail": 'This task needs a short requester-facing update. Include data.msg in your "drop" action.',
            "agent_name": agent.name,
        }
    db.update_task(
        task_id,
        status="abandoned",
        status_note=reason or None,
        completion_summary=None,
        watchdog_pinged_at=None,
    )
    active = activity_runtime.get_active_work_activity(agent.id)
    if active:
        activity_runtime.complete_activity(active.id, detail=reason or active.detail)
    else:
        activity_runtime.refresh_agent_status(agent.id)

    result = {
        "event": "status_changed",
        "detail": f"{agent.name} abandoned task" + (f" — {reason}" if reason else ""),
        "agent_name": agent.name,
        "chat_notification": {
            "kind": "abandoned",
            "task_title": task.title if task else "task",
            "reason": reason,
            "task_id": task.id if task else None,
            "source_channel": task.source_channel if task else "chat",
            "channel_id": task.notification_channel_id if task else None,
            "policy": task.notification_policy if task else "completion_blocked",
            "human_visible": _task_is_human_visible(task),
        },
    }
    if task is not None:
        append_task_event(
            task_id=task.id,
            author_type="agent",
            author_agent_id=agent.id,
            author_name=agent.name,
            event_type="status_update",
            content=reason or f'Abandoned "{task.title}".',
            source_trigger_id=(trigger or {}).get("trigger_id"),
        )
    skipped = _append_task_follow_up_message(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=follow_up_message,
        attention_kind="abandoned",
        source_trigger_id=(trigger or {}).get("trigger_id"),
    )
    _append_task_stakeholder_reports(
        result=result,
        actor=agent,
        state=state,
        task=task,
        content=(f'Abandoned "{task.title}": {reason}' if task and reason else f'Abandoned "{task.title}".' if task else ""),
        skip_recipient_ids=skipped,
    )
    return result


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_ACTION_HANDLERS = {
    "bm_cli": _handle_bm_cli,
    "work": _handle_work,
    "message": _handle_message,
    "taskMessage": _handle_task_message,
    "delegateTask": _handle_delegate_task,
    "walkTo": _handle_walk_to,
    "attendMeeting": _handle_attend_meeting,
    "remoteMeeting": _handle_remote_meeting,
    "idle": _handle_idle,
    "waiting": _handle_waiting,
    "complete": _handle_complete,
    "blocked": _handle_blocked,
    "delegated": _handle_delegated,
    "abandoned": _handle_abandoned,
}
