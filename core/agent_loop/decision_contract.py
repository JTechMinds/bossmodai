"""BossMod AI — Unified conversation contract."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from core.bm_cli.contract import maybe_parse_bm_cli_call
from core.bm_cli.host_path_consent import maybe_parse_host_access_call
from core.models.work_contract import DeliverableSpec
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


DecisionType = Literal["answer", "accept", "clarify", "cancel", "decline", "defer", "observe"]
IntentKind = Literal[
    "question",
    "status_request",
    "meeting_request",
    "work_request",
    "relocation_request",
    "break_request",
    "social_request",
    "other",
]
CommitmentKind = Literal["none", "conversation", "meeting", "work", "break"]
Destination = Literal["desk", "meetingRoom", "breakRoom", "mainWorkspace", "southWorkspace", "hallway"]

_ACT_TO_DECISION = {
    "reply": "answer",
    "observe": "observe",
    "accept": "accept",
    "clarify": "clarify",
    "cancel": "cancel",
    "decline": "decline",
    "defer": "defer",
}
_INTENT_TO_NAME = {
    "question": "question",
    "status": "status_request",
    "meeting": "meeting_request",
    "work": "work_request",
    "move": "relocation_request",
    "break": "break_request",
    "social": "social_request",
    "other": "other",
}
_COMMIT_TO_NAME = {
    "none": "none",
    "conversation": "conversation",
    "meeting": "meeting",
    "work": "work",
    "break": "break",
}
_DEST_TO_NAME = {
    "desk": "desk",
    "meeting": "meetingRoom",
    "break": "breakRoom",
    "main": "mainWorkspace",
    "south": "southWorkspace",
    "hall": "hallway",
}
_SHARED_CONVERSATION_TRIGGER_TYPES = {
    "session_message",
    "session_response",
    "channel_message",
    "channel_response",
}

_ALLOWED_ACTS_BY_TRIGGER = {
    "human_chat": ("reply", "accept", "clarify", "cancel", "decline", "defer"),
    "peer_message": ("observe", "reply", "accept", "clarify", "decline"),
    "meeting_invite": ("accept", "clarify", "decline"),
    "task_assigned": ("accept", "clarify", "defer", "decline"),
    "task_follow_up": ("reply", "accept", "clarify", "defer", "decline"),
    "task_update": ("observe",),
    "watchdog_status_ping": ("reply",),
}
_DEFAULT_ALLOWED_ACTS = ("reply", "accept", "clarify", "decline", "defer", "observe")
_SHARED_ALLOWED_ACTS = ("observe", "reply", "accept", "clarify", "decline")

class DelegatedWorkItem(BaseModel):
    """One delegated child-task request embedded in an accepted work decision."""

    model_config = ConfigDict(extra="forbid")

    agentId: str | None = None
    agentName: str | None = None
    taskTitle: str
    taskDescription: str | None = None
    deliverables: list[DeliverableSpec] | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> "DelegatedWorkItem":
        if not ((self.agentId and self.agentId.strip()) or (self.agentName and self.agentName.strip())):
            raise ValueError('delegated work must identify the assignee with "agentId" or "agentName"')
        if not (self.taskTitle and self.taskTitle.strip()):
            raise ValueError('delegated work requires a non-empty "taskTitle"')
        return self


class WorkExecutionPlan(BaseModel):
    """Structured execution strategy for accepted work."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["self", "delegate", "mixed"] = "self"
    delegations: list[DelegatedWorkItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_shape(self) -> "WorkExecutionPlan":
        if self.mode == "self" and self.delegations:
            raise ValueError('work plan mode "self" must not include delegated work')
        if self.mode in {"delegate", "mixed"} and not self.delegations:
            raise ValueError(f'work plan mode "{self.mode}" requires at least one delegated child task')
        return self


class ConversationDecision(BaseModel):
    """Structured internal result for one conversational turn."""

    model_config = ConfigDict(extra="forbid")

    decision: DecisionType
    intentKind: IntentKind
    reply: str | None = None
    commitmentKind: CommitmentKind = "none"
    destination: Destination | None = None
    title: str | None = None
    detail: str | None = None
    taskTitle: str | None = None
    taskDescription: str | None = None
    deliverables: list[DeliverableSpec] | None = None
    executionPlan: WorkExecutionPlan | None = None
    thought: str = Field(default="")

    @model_validator(mode="after")
    def _validate_shape(self) -> "ConversationDecision":
        if self.decision in {"answer", "clarify", "cancel", "decline", "observe"} and self.commitmentKind != "none":
            raise ValueError(f'"{self.decision}" decisions must use commitmentKind="none"')
        if self.decision == "observe":
            if self.reply not in (None, ""):
                raise ValueError('"observe" decisions must not include "reply"')
            if any(
                value not in (None, "")
                for value in (self.destination, self.title, self.detail, self.taskTitle, self.taskDescription)
            ):
                raise ValueError('"observe" decisions must not include commitment fields')
            if self.deliverables:
                raise ValueError('"observe" decisions must not include deliverables')
        if self.decision == "defer" and self.commitmentKind not in {"none", "work"}:
            raise ValueError('"defer" decisions may only defer work or keep commitmentKind="none"')
        if self.deliverables and not (self.decision == "accept" and self.commitmentKind == "work"):
            raise ValueError('"deliverables" may only be provided for accepted work commitments')
        if self.executionPlan and not (self.decision == "accept" and self.commitmentKind == "work"):
            raise ValueError('"executionPlan" may only be provided for accepted work commitments')
        if self.executionPlan and self.executionPlan.mode == "delegate" and self.deliverables:
            raise ValueError('pure delegated work plans must put deliverables on delegated child tasks, not the parent task')
        if self.commitmentKind in {"meeting", "break"} and self.decision == "accept" and self.destination is None:
            raise ValueError(f'"accept" + commitmentKind="{self.commitmentKind}" requires "destination"')
        if self.commitmentKind == "break" and self.destination != "breakRoom":
            raise ValueError('break commitments must use destination="breakRoom"')
        return self


def allowed_decisions_for_trigger(trigger_type: str | None) -> tuple[str, ...]:
    """Return the valid canonical decision names for one conversation trigger type."""
    return tuple(_ACT_TO_DECISION[act] for act in allowed_conversation_acts_for_trigger(trigger_type))


def allowed_conversation_acts_for_trigger(trigger_type: str | None) -> tuple[str, ...]:
    """Return the valid final conversation act values for one trigger type."""
    if trigger_type in _SHARED_CONVERSATION_TRIGGER_TYPES:
        return _SHARED_ALLOWED_ACTS
    return _ALLOWED_ACTS_BY_TRIGGER.get(str(trigger_type or ""), _DEFAULT_ALLOWED_ACTS)


def parse_decision(raw_response: str) -> dict[str, Any]:
    """Parse one conversation JSON decision from an LLM response."""
    return _parse_conversation_response(raw_response, allow_cli=False)


def parse_direct_turn_response(raw_response: str) -> dict[str, Any]:
    """Parse either a conversation decision or a BossMod CLI call."""
    return _parse_conversation_response(raw_response, allow_cli=True)


def _parse_conversation_response(raw_response: str, *, allow_cli: bool) -> dict[str, Any]:
    """Parse a conversation response into canonical internal fields."""
    parsed = _parse_json_object(raw_response)
    if isinstance(parsed, dict) and parsed.get("decision") == "_parse_failed":
        return parsed

    assert isinstance(parsed, dict)

    if allow_cli:
        try:
            cli_call = maybe_parse_bm_cli_call(parsed)
        except (ValidationError, ValueError) as exc:
            return {
                "decision": "_parse_failed",
                "thought": _candidate_thought(parsed),
                "_raw_snippet": _validation_message(exc)[:200],
                "_candidate_payload": parsed,
            }
        if cli_call is not None:
            return cli_call.model_dump()
        try:
            host_call = maybe_parse_host_access_call(parsed)
        except (ValidationError, ValueError) as exc:
            return {
                "decision": "_parse_failed",
                "thought": _candidate_thought(parsed),
                "_raw_snippet": _validation_message(exc)[:200],
                "_candidate_payload": parsed,
            }
        if host_call is not None:
            return host_call.model_dump()

    try:
        normalized = _normalize_conversation_payload(parsed)
        decision = ConversationDecision.model_validate(normalized)
    except (ValidationError, ValueError) as exc:
        error = _validation_message(exc)
        logger.warning("Invalid decision payload: %s", error)
        return {
            "decision": "_parse_failed",
            "thought": _candidate_thought(parsed),
            "_raw_snippet": error[:200],
            "_candidate_payload": parsed,
        }

    return decision.model_dump()


def _parse_json_object(raw_response: str) -> dict[str, Any]:
    """Parse a JSON object from model output or return a structured parse failure."""
    text = raw_response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
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
                logger.warning("Failed to parse decision JSON: %s", text[:200])
                return {"decision": "_parse_failed", "thought": "", "_raw_snippet": text[:200]}
        else:
            logger.warning("No JSON found in decision response: %s", text[:200])
            return {"decision": "_parse_failed", "thought": "", "_raw_snippet": text[:200]}

    if not isinstance(parsed, dict):
        return {"decision": "_parse_failed", "thought": "", "_raw_snippet": "Decision payload must be a JSON object"}
    return parsed


def _normalize_conversation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the model-facing compact conversation payload into canonical fields."""
    if "act" not in payload:
        raise ValueError('missing "act"')
    extra_root = set(payload) - {"act", "intent", "msg", "commit", "data", "th"}
    if extra_root:
        raise ValueError(f'unexpected top-level keys: {", ".join(sorted(extra_root))}')

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError('"data" must be an object when provided')
    extra_data = set(data) - {"dst", "title", "detail", "task", "plan"}
    if extra_data:
        raise ValueError(f'unexpected data keys: {", ".join(sorted(extra_data))}')
    task = data.get("task") or {}
    if task in ("", None):
        task = {}
    if not isinstance(task, dict):
        raise ValueError('"data.task" must be an object when provided')
    extra_task = set(task) - {"title", "desc", "outs"}
    if extra_task:
        raise ValueError(f'unexpected data.task keys: {", ".join(sorted(extra_task))}')
    plan = data.get("plan") or {}
    if plan in ("", None):
        plan = {}
    if not isinstance(plan, dict):
        raise ValueError('"data.plan" must be an object when provided')
    extra_plan = set(plan) - {"mode", "children"}
    if extra_plan:
        raise ValueError(f'unexpected data.plan keys: {", ".join(sorted(extra_plan))}')

    return {
        "decision": _map_required(payload.get("act"), _ACT_TO_DECISION, "act"),
        "intentKind": _map_required(payload.get("intent"), _INTENT_TO_NAME, "intent"),
        "reply": payload.get("msg"),
        "commitmentKind": _map_optional(payload.get("commit"), _COMMIT_TO_NAME, "commit") or "none",
        "destination": _map_optional(data.get("dst"), _DEST_TO_NAME, "data.dst"),
        "title": data.get("title"),
        "detail": data.get("detail"),
        "taskTitle": task.get("title"),
        "taskDescription": task.get("desc"),
        "deliverables": _normalize_outs(task.get("outs")),
        "executionPlan": _normalize_work_plan(plan),
        "thought": payload.get("th", ""),
    }


def _normalize_outs(value: Any) -> Any:
    """Normalize model-facing deliverable outs into canonical deliverables."""
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


def _normalize_work_plan(value: Any) -> Any:
    """Normalize the compact plan payload into the canonical execution-plan shape."""
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise ValueError('"data.plan" must be an object when provided')

    children = value.get("children") or []
    if not isinstance(children, list):
        raise ValueError('"data.plan.children" must be an array when provided')

    normalized_children: list[dict[str, Any]] = []
    for item in children:
        if not isinstance(item, dict):
            raise ValueError('each item in "data.plan.children" must be an object')
        extra_item = set(item) - {"aid", "who", "task"}
        if extra_item:
            raise ValueError(f'unexpected delegated child keys: {", ".join(sorted(extra_item))}')
        child_task = item.get("task") or {}
        if not isinstance(child_task, dict):
            raise ValueError('"data.plan.children[].task" must be an object when provided')
        extra_task = set(child_task) - {"title", "desc", "outs"}
        if extra_task:
            raise ValueError(f'unexpected delegated child task keys: {", ".join(sorted(extra_task))}')
        normalized_children.append(
            {
                "agentId": item.get("aid"),
                "agentName": item.get("who"),
                "taskTitle": child_task.get("title"),
                "taskDescription": child_task.get("desc"),
                "deliverables": _normalize_outs(child_task.get("outs")),
            }
        )

    return {
        "mode": value.get("mode") or "self",
        "delegations": normalized_children,
    }


def _map_required(value: Any, mapping: dict[str, str], field_name: str) -> str:
    """Map one required model-facing string to its canonical value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'missing "{field_name}"')
    key = value.strip()
    if key not in mapping:
        raise ValueError(f'invalid "{field_name}"')
    return mapping[key]


def _map_optional(value: Any, mapping: dict[str, str], field_name: str) -> str | None:
    """Map one optional model-facing string to its canonical value."""
    if value in (None, ""):
        return None
    return _map_required(value, mapping, field_name)


def _candidate_thought(payload: Any) -> str:
    """Extract the best available thought field from a candidate payload."""
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("th"), str):
        return payload.get("th", "")
    if isinstance(payload.get("thought"), str):
        return payload.get("thought", "")
    return ""


def _validation_message(exc: Exception) -> str:
    """Return the first validation message from a parsing exception."""
    if isinstance(exc, ValidationError):
        return exc.errors()[0].get("msg", "Invalid decision payload")
    return str(exc) or "Invalid decision payload"


def validate_decision_for_trigger(
    decision: ConversationDecision,
    *,
    trigger_type: str,
    active_task_id: str | None,
    trigger: dict[str, Any] | None = None,
) -> str | None:
    """Validate a parsed decision against the conversation turn context."""
    if trigger_type == "watchdog_status_ping" and decision.decision != "answer":
        return "watchdog status pings require a direct reply"

    allowed = allowed_decisions_for_trigger(trigger_type)
    if decision.decision not in allowed:
        return f'this turn only allows decisions: {", ".join(allowed)}'

    if trigger_type in {
        "human_chat",
        "peer_message",
        "meeting_invite",
        "session_message",
        "session_response",
        "channel_message",
        "channel_response",
        "task_assigned",
        "task_follow_up",
        "watchdog_status_ping",
    } and decision.decision != "observe" and not (decision.reply and decision.reply.strip()):
        return 'conversation turns require a non-empty "reply" unless you choose "observe"'

    if decision.decision == "cancel":
        if active_task_id is None:
            return 'cancel is only valid when there is an active task to close'
        if decision.intentKind != "work_request":
            return 'cancel decisions must use intentKind="work_request"'

    if decision.decision == "observe" and trigger_type not in {"peer_message", "task_update", *_SHARED_CONVERSATION_TRIGGER_TYPES}:
        return '"observe" is only valid for peer messages, task updates, or shared meeting/channel conversation turns'

    if trigger_type == "task_assigned":
        if decision.executionPlan is not None:
            return 'task assignment decisions must not include an "executionPlan"'
        if decision.commitmentKind == "work" and decision.taskTitle:
            return 'task assignment decisions must not invent a new "taskTitle"'
        if decision.taskDescription:
            return 'task assignment decisions must not invent a new "taskDescription"'
        if decision.decision == "accept" and decision.commitmentKind != "work":
            return 'accepting an assignment must use commitmentKind="work"'
        if decision.decision == "defer" and decision.commitmentKind != "work":
            return 'deferring an assignment must keep commitmentKind="work"'

    if trigger_type == "task_follow_up":
        task_status = str((trigger or {}).get("task_status") or "").strip().lower()
        task_party = str((trigger or {}).get("task_party") or "").strip().lower()
        pending_assignee_turn = task_status == "pending" and task_party == "assignee"

        if pending_assignee_turn:
            if decision.decision not in {"accept", "clarify", "defer", "decline"}:
                return "pending task decisions must accept, clarify, defer, or decline the existing task"
            if decision.executionPlan is not None:
                return 'task follow-up decisions for an existing pending task must not include an "executionPlan"'
            if decision.commitmentKind == "work" and decision.taskTitle:
                return 'task follow-up decisions for an existing pending task must not invent a new "taskTitle"'
            if decision.taskDescription:
                return 'task follow-up decisions for an existing pending task must not invent a new "taskDescription"'
            if decision.decision == "accept" and decision.commitmentKind != "work":
                return 'accepting a pending task follow-up must use commitmentKind="work"'
            if decision.decision == "defer" and decision.commitmentKind != "work":
                return 'deferring a pending task follow-up must keep commitmentKind="work"'
        else:
            if decision.decision not in {"answer", "clarify"}:
                return "task follow-up turns only allow reply or clarify unless the pending task is awaiting your decision"
            if decision.commitmentKind != "none":
                return 'task follow-up reply turns must use commitmentKind="none"'

    if trigger_type == "meeting_invite":
        if decision.decision == "accept" and decision.commitmentKind != "meeting":
            return 'accepting a meeting invite must use commitmentKind="meeting"'
        if decision.decision in {"clarify", "decline"} and decision.commitmentKind != "none":
            return 'meeting invite clarify/decline turns must use commitmentKind="none"'

    if trigger_type in {
        "human_chat",
        "peer_message",
        "meeting_invite",
        "session_message",
        "session_response",
        "channel_message",
        "channel_response",
    } and decision.commitmentKind == "work":
        if trigger_type == "peer_message":
            return 'peer messages are conversational only; use explicit task assignment instead of creating durable work from coworker chat'
        if trigger_type == "meeting_invite":
            return 'meeting invites are coordination only; accept/decline the meeting and use tasks for durable work'
        if decision.decision in {"accept", "defer"} and not (decision.taskTitle and decision.taskTitle.strip()):
            return 'conversation work requests must provide a non-empty "taskTitle"'
        if decision.executionPlan is not None and trigger_type == "peer_message":
            return 'peer messages are conversational only; delegated work plans belong on accepted task work, not coworker chat'

    if trigger_type in {"session_message", "channel_message"} and decision.decision == "defer":
        return 'shared-message intake turns may observe, reply, accept, clarify, or decline; defer only after you are actively replying'

    return None
