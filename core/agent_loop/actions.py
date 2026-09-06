"""BossMod AI — Execution action parse and dispatch.

Parses flat JSON execution actions from LLM responses and dispatches them
to domain handlers. These actions only carry out existing commitments.
Direct requests are handled by the decision runtime, not by creating work
or movement directly from chat.

Handlers live in actions_work / actions_tasks / actions_lifecycle /
actions_meetings / actions_cli. Follow-up helpers live in task_followups;
shared resolve/token helpers in actions_shared.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.agent_loop.actions_cli import _handle_bm_cli
from core.agent_loop.actions_lifecycle import (
    _handle_abandoned,
    _handle_blocked,
    _handle_complete,
    _handle_delegated,
    _handle_waiting,
)
from core.agent_loop.actions_meetings import _handle_attend_meeting, _handle_remote_meeting
from core.agent_loop.actions_tasks import _handle_delegate_task, _handle_task_message
from core.agent_loop.actions_work import (
    _handle_idle,
    _handle_message,
    _handle_walk_to,
    _handle_work,
)
from core.models import Agent, AgentState
from core.models.work_contract import WorkContract

logger = logging.getLogger(__name__)

# Actions that end the current multi-turn loop
TERMINAL_ACTIONS = {"idle", "waiting", "complete", "blocked", "delegated", "abandoned"}

_VALID_MESSAGE_RECIPIENT_TYPES = {"human", "agent"}
_VALID_TASK_MESSAGE_KINDS = {"note", "status", "question", "review"}
_TASK_LIFECYCLE_ACTIONS = {"waiting", "complete", "blocked", "delegated", "abandoned"}
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
    extra_data = set(extra) - {
        "cmd", "body", "out", "to", "aid", "aids", "msg", "tid", "kind",
        "dst", "mode", "topic", "sum", "why", "task", "claim", "confirm",
    }
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
            normalized["confirmSpecialtyMismatch"] = _as_bool(extra.get("confirm"))
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
            normalized["doneClaim"] = extra.get("claim")
        case "wait" | "block" | "drop":
            normalized["reason"] = extra.get("why")
            normalized["followUpMessage"] = extra.get("msg")
        case "deleg":
            normalized["agentId"] = extra.get("aid")
            normalized["followUpMessage"] = extra.get("msg")
            normalized["confirmSpecialtyMismatch"] = _as_bool(extra.get("confirm"))
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


def _as_bool(value: Any) -> bool:
    """Treat compact confirm flags as true only for explicit truthy values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


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
