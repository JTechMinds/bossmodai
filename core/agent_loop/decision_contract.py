"""BossMod AI — Unified conversation contract."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from core.bm_cli.contract import maybe_parse_bm_cli_call, render_bm_cli_guidance
from core.models.work_contract import DeliverableSpec
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


DecisionType = Literal["answer", "accept", "clarify", "decline", "defer", "observe"]
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
    "human_chat": ("reply", "accept", "clarify", "decline", "defer"),
    "peer_message": ("reply", "accept", "clarify", "decline"),
    "task_assigned": ("accept", "clarify", "defer", "decline"),
}
_DEFAULT_ALLOWED_ACTS = ("reply", "accept", "clarify", "decline", "defer", "observe")
_SHARED_ALLOWED_ACTS = ("observe", "reply", "accept", "clarify", "decline")


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
    thought: str = Field(default="")

    @model_validator(mode="after")
    def _validate_shape(self) -> "ConversationDecision":
        if self.decision in {"answer", "clarify", "decline", "observe"} and self.commitmentKind != "none":
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
        if self.commitmentKind in {"meeting", "break"} and self.decision == "accept" and self.destination is None:
            raise ValueError(f'"accept" + commitmentKind="{self.commitmentKind}" requires "destination"')
        if self.commitmentKind == "break" and self.destination != "breakRoom":
            raise ValueError('break commitments must use destination="breakRoom"')
        return self


def allowed_decisions_for_trigger(trigger_type: str | None) -> tuple[str, ...]:
    """Return the valid canonical decision names for one conversation trigger type."""
    return tuple(_ACT_TO_DECISION[act] for act in allowed_acts_for_trigger(trigger_type))


def allowed_acts_for_trigger(trigger_type: str | None) -> tuple[str, ...]:
    """Return the valid model-facing act values for one conversation trigger type."""
    if trigger_type in _SHARED_CONVERSATION_TRIGGER_TYPES:
        return _SHARED_ALLOWED_ACTS
    return _ALLOWED_ACTS_BY_TRIGGER.get(str(trigger_type or ""), _DEFAULT_ALLOWED_ACTS)


def default_decision_contract_template() -> str:
    """Return the default authored decision contract template."""
    human_shape = _render_decision_shape(_ALLOWED_ACTS_BY_TRIGGER["human_chat"])
    peer_shape = _render_decision_shape(_ALLOWED_ACTS_BY_TRIGGER["peer_message"])
    assignment_shape = _render_decision_shape(_ALLOWED_ACTS_BY_TRIGGER["task_assigned"])
    shared_shape = _render_decision_shape(_SHARED_ALLOWED_ACTS)
    default_shape = _render_decision_shape(_DEFAULT_ALLOWED_ACTS)

    lines = [
        "CONVERSATION TURN",
        "Return exactly one JSON object.",
        "Use the same schema for direct chat, peer chat, shared threads, and task assignments.",
        "The runtime already knows who spoke, which thread/channel this is, and who else is present.",
        "",
        "{{if trigger.type = 'human_chat'}}",
        "ALLOWED act FOR THIS TURN: reply | accept | clarify | decline | defer",
        "{{elseif trigger.type = 'peer_message'}}",
        "ALLOWED act FOR THIS TURN: reply | accept | clarify | decline",
        "{{elseif trigger.type = 'task_assigned'}}",
        "ALLOWED act FOR THIS TURN: accept | clarify | defer | decline",
        "{{elseif trigger.type = 'session_message'}}",
        "ALLOWED act FOR THIS TURN: observe | reply | accept | clarify | decline",
        "{{elseif trigger.type = 'session_response'}}",
        "ALLOWED act FOR THIS TURN: observe | reply | accept | clarify | decline",
        "{{elseif trigger.type = 'channel_message'}}",
        "ALLOWED act FOR THIS TURN: observe | reply | accept | clarify | decline",
        "{{elseif trigger.type = 'channel_response'}}",
        "ALLOWED act FOR THIS TURN: observe | reply | accept | clarify | decline",
        "{{else}}",
        "ALLOWED act FOR THIS TURN: reply | accept | clarify | decline | defer | observe",
        "{{end}}",
        "  reply = send a conversational reply",
        "  observe = stay silent in a shared thread",
        "  accept / clarify / decline / defer = commit-level conversation decisions",
        "",
        "REQUIRED JSON SHAPE:",
        "Do not output the schema itself. Output one JSON object matching this shape:",
        "{{if trigger.type = 'human_chat'}}",
        human_shape,
        "{{elseif trigger.type = 'peer_message'}}",
        peer_shape,
        "{{elseif trigger.type = 'task_assigned'}}",
        assignment_shape,
        "{{elseif trigger.type = 'session_message'}}",
        shared_shape,
        "{{elseif trigger.type = 'session_response'}}",
        shared_shape,
        "{{elseif trigger.type = 'channel_message'}}",
        shared_shape,
        "{{elseif trigger.type = 'channel_response'}}",
        shared_shape,
        "{{else}}",
        default_shape,
        "{{end}}",
        "",
        "FIELD DEFINITIONS:",
        "  act = the response mode you choose for this turn",
        "  intent = what the incoming message is about",
        "  msg = the outward-facing text reply; null only when staying silent",
        "  commit = the durable commitment created or preserved by this turn",
        "  data = extra fields used only when the chosen act/commit needs them",
        "  data.dst = destination for meeting, break, or relocation commitments",
        "  data.title = short commitment label when useful",
        "  data.detail = longer commitment note when useful",
        "  data.task.title = title for newly accepted work",
        "  data.task.desc = description for newly accepted work",
        "  data.task.outs = deliverables required for newly accepted work",
        "  th = short admin-visible note",
        "",
        "FIELD VALUES:",
        "  intent = question | status | meeting | work | move | break | social | other",
        "  commit = none | conversation | meeting | work | break",
        "  data.dst = desk | meeting | break | main | south | hall",
        "",
        "RULES:",
        '  - act="reply" is the normal response mode for direct chat, peer chat, and status answers.',
        '  - act="observe" is only valid in shared thread turns.',
        '  - intent="status" means a live current-state question. Use the AUTHORITATIVE COMMUNICATION SNAPSHOT when present. Use BossMod CLI only if the snapshot lacks the needed fact.',
        '  - accept work: require msg, commit="work", and data.task.title + data.task.desc unless accepting an existing assignment.',
        '  - accept meeting: require msg, commit="meeting", and data.dst.',
        '  - accept break: require msg, commit="break", and data.dst="break".',
        '  - clarify / decline: require msg and commit="none".',
        '  - defer: require msg and commit="none" or "work".',
        '  - "act" is the response mode. "status" belongs in "intent", never in "act".',
        '  - Do not invent keys that are not listed.',
        "",
        render_bm_cli_guidance(),
        "{{if trigger.type = 'peer_message'}}",
        "",
        "PEER NOTE:",
        "  - ordinary coworker chat is conversational only; durable work should arrive as an explicit assignment",
        "{{elseif trigger.type = 'task_assigned'}}",
        "",
        "ASSIGNMENT NOTE:",
        "  - this is an offered assignment; use accept | clarify | defer | decline",
        '  - accept/defer must keep commit="work"; clarify/decline must keep commit="none"',
        "  - do not invent a new data.task.title or data.task.desc for an existing assignment",
        "{{end}}",
        "",
        "EXAMPLES:",
        '  {"act":"reply","intent":"status","msg":"I am idle right now.","commit":"none","th":"share status"}',
        '  {"act":"accept","intent":"work","msg":"I will draft it.","commit":"work","data":{"task":{"title":"Write memo","desc":"Draft the memo.","outs":[{"type":"file","path":"memo.md"}]}},"th":"accept work"}',
    ]
    return "\n".join(lines)


def render_decision_contract(trigger_type: str | None = None) -> str:
    """Render the unified prompt contract for all conversation turns."""
    allowed_acts = allowed_acts_for_trigger(trigger_type)
    lines = [
        "CONVERSATION TURN",
        "Return exactly one JSON object.",
        "Use the same schema for direct chat, peer chat, shared threads, and task assignments.",
        "The runtime already knows who spoke, which thread/channel this is, and who else is present.",
        "",
        f'ALLOWED act FOR THIS TURN: {" | ".join(allowed_acts)}',
        "  reply = send a conversational reply",
        "  observe = stay silent in a shared thread",
        "  accept / clarify / decline / defer = commit-level conversation decisions",
        "",
        "REQUIRED JSON SHAPE:",
        "Do not output the schema itself. Output one JSON object matching this shape:",
        _render_decision_shape(allowed_acts),
        "",
        "FIELD DEFINITIONS:",
        "  act = the response mode you choose for this turn",
        "  intent = what the incoming message is about",
        "  msg = the outward-facing text reply; null only when staying silent",
        "  commit = the durable commitment created or preserved by this turn",
        "  data = extra fields used only when the chosen act/commit needs them",
        "  data.dst = destination for meeting, break, or relocation commitments",
        "  data.title = short commitment label when useful",
        "  data.detail = longer commitment note when useful",
        "  data.task.title = title for newly accepted work",
        "  data.task.desc = description for newly accepted work",
        "  data.task.outs = deliverables required for newly accepted work",
        "  th = short admin-visible note",
        "",
        "FIELD VALUES:",
        "  intent = question | status | meeting | work | move | break | social | other",
        "  commit = none | conversation | meeting | work | break",
        "  data.dst = desk | meeting | break | main | south | hall",
        "",
        "RULES:",
        '  - act="reply" is the normal response mode for direct chat, peer chat, and status answers.',
        '  - act="observe" is only valid in shared thread turns.',
        '  - intent="status" means a live current-state question. Use the AUTHORITATIVE COMMUNICATION SNAPSHOT when present. Use BossMod CLI only if the snapshot lacks the needed fact.',
        '  - accept work: require msg, commit="work", and data.task.title + data.task.desc unless accepting an existing assignment.',
        '  - accept meeting: require msg, commit="meeting", and data.dst.',
        '  - accept break: require msg, commit="break", and data.dst="break".',
        '  - clarify / decline: require msg and commit="none".',
        '  - defer: require msg and commit="none" or "work".',
        '  - "act" is the response mode. "status" belongs in "intent", never in "act".',
        '  - Do not invent keys that are not listed.',
        "",
        render_bm_cli_guidance(),
    ]

    if trigger_type == "peer_message":
        lines.extend(
            [
                "",
                "PEER NOTE:",
                "  - ordinary coworker chat is conversational only; durable work should arrive as an explicit assignment",
            ]
        )

    if trigger_type == "task_assigned":
        lines.extend(
            [
                "",
                "ASSIGNMENT NOTE:",
                "  - this is an offered assignment; use accept | clarify | defer | decline",
                '  - accept/defer must keep commit="work"; clarify/decline must keep commit="none"',
                "  - do not invent a new data.task.title or data.task.desc for an existing assignment",
            ]
        )

    lines.extend(
        [
            "",
            "EXAMPLES:",
            '  {"act":"reply","intent":"status","msg":"I am idle right now.","commit":"none","th":"share status"}',
            '  {"act":"accept","intent":"work","msg":"I will draft it.","commit":"work","data":{"task":{"title":"Write memo","desc":"Draft the memo.","outs":[{"type":"file","path":"memo.md"}]}},"th":"accept work"}',
        ]
    )
    return "\n".join(lines)


def _render_decision_shape(allowed_acts: tuple[str, ...]) -> str:
    """Render the actual model-facing JSON shape for conversation turns."""
    shape = {
        "act": " | ".join(allowed_acts),
        "intent": "question | status | meeting | work | move | break | social | other",
        "msg": "string | null",
        "commit": "none | conversation | meeting | work | break",
        "data": {
            "dst": "desk | meeting | break | main | south | hall | null",
            "title": "string | null",
            "detail": "string | null",
            "task": {
                "title": "string | null",
                "desc": "string | null",
                "outs": [{"type": "file", "path": "string", "desc": "string | null"}],
            },
        },
        "th": "string",
    }
    return "```json\n" + json.dumps(shape, indent=2) + "\n```"


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
    extra_data = set(data) - {"dst", "title", "detail", "task"}
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

    return {
        "decision": _map_required(payload.get("act"), _ACT_TO_DECISION, "act"),
        "intentKind": _map_required(payload.get("intent"), _INTENT_TO_NAME, "intent"),
        "reply": payload.get("msg"),
        "commitmentKind": _map_required(payload.get("commit"), _COMMIT_TO_NAME, "commit"),
        "destination": _map_optional(data.get("dst"), _DEST_TO_NAME, "data.dst"),
        "title": data.get("title"),
        "detail": data.get("detail"),
        "taskTitle": task.get("title"),
        "taskDescription": task.get("desc"),
        "deliverables": _normalize_outs(task.get("outs")),
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
) -> str | None:
    """Validate a parsed decision against the conversation turn context."""
    allowed = allowed_decisions_for_trigger(trigger_type)
    if decision.decision not in allowed:
        return f'this turn only allows decisions: {", ".join(allowed)}'

    if trigger_type in {
        "human_chat",
        "peer_message",
        "session_message",
        "session_response",
        "channel_message",
        "channel_response",
        "task_assigned",
    } and decision.decision != "observe" and not (decision.reply and decision.reply.strip()):
        return 'conversation turns require a non-empty "reply" unless you choose "observe"'

    if trigger_type in {"human_chat", "peer_message", "task_assigned"} and decision.decision == "observe":
        return '"observe" is only valid for shared meeting/channel conversation turns'

    if trigger_type == "task_assigned":
        if decision.commitmentKind == "work" and decision.taskTitle:
            return 'task assignment decisions must not invent a new "taskTitle"'
        if decision.taskDescription:
            return 'task assignment decisions must not invent a new "taskDescription"'
        if decision.decision == "accept" and decision.commitmentKind != "work":
            return 'accepting an assignment must use commitmentKind="work"'
        if decision.decision == "defer" and decision.commitmentKind != "work":
            return 'deferring an assignment must keep commitmentKind="work"'

    if trigger_type in {
        "human_chat",
        "peer_message",
        "session_message",
        "session_response",
        "channel_message",
        "channel_response",
    } and decision.commitmentKind == "work":
        if trigger_type == "peer_message":
            return 'peer messages are conversational only; use explicit task assignment instead of creating durable work from coworker chat'
        if decision.decision in {"accept", "defer"} and not (decision.taskTitle and decision.taskTitle.strip()):
            return 'conversation work requests must provide a non-empty "taskTitle"'

    if trigger_type in {"session_message", "channel_message"} and decision.decision == "defer":
        return 'shared-message intake turns may observe, reply, accept, clarify, or decline; defer only after you are actively replying'

    return None
