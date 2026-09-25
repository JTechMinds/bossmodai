"""Fail-closed steer when a turn emits prose or an invalid decision object.

Decision turns may repair prose, invented keys, and broken JSON up to the
configured attempt limit, then fail-close. Execution turns still fail-close
prose and invented keys immediately. Soft-block behavior is unchanged.

Product envelope: ``say`` (operator chat) plus optional ``actions`` (Board /
tools / CLI). Those map onto the existing compact keys ``msg`` and
``act``/``data`` — not a second protocol. Empty ``actions`` is a valid 1:1
status wake. ``say`` alone is never Done / Blocked / F.
``work_commit`` is a boolean intent flag on that envelope, required on
every reply (see ``decision_contract``). It is not a phrase list and it is
not Board Done.
"""

from __future__ import annotations

from typing import Any, Literal

ParseFailureKind = Literal["prose_status", "invalid_json", "invalid_decision"]

PROSE_STATUS_STEER = (
    "Emit the required JSON decision/action shape for this turn "
    "(say plus optional actions). "
    "Prose status is not a valid turn result. "
    "Do not park @Operator. Do not invent a desk deny."
)

INVALID_DECISION_STEER = (
    "Emit the required JSON decision/action shape for this turn "
    "(say plus optional actions). "
    "Do not invent approval fields. "
    "Approval comes from approval_required plus a request id from the tool, "
    "not from invented JSON fields. "
    "Do not park @Operator. Do not invent a desk deny."
)

# Existing compact keys plus the product aliases. Unknown keys stay fail-closed.
# ``next_owners`` is the structured handoff pin. It is not a second protocol.
_COMPACT_ACTION_KEYS = frozenset({"act", "intent", "msg", "commit", "data", "th", "next_owners"})
_ENVELOPE_KEYS = frozenset({"say", "actions", "next_owners", "work_commit"})
_COMPACT_ROOT_KEYS = _COMPACT_ACTION_KEYS | _ENVELOPE_KEYS
_CONVERSATION_ACTS = frozenset(
    {"reply", "observe", "accept", "clarify", "cancel", "decline", "defer"}
)
_LOOKUP_ACTS = frozenset({"cli", "request_host_access"})
_EXECUTION_CHAT_ACTS = frozenset(
    {"socialmsg", "taskmsg", "done", "block", "wait", "deleg", "drop"}
)


class InvalidDecisionEnvelope(ValueError):
    """Fail-closed: invented keys or a malformed say/actions envelope."""


def classify_json_parse_failure(raw_response: str) -> ParseFailureKind:
    """Return ``prose_status`` when the model emitted no JSON object."""
    text = _strip_fences(raw_response)
    if "{" not in text:
        return "prose_status"
    return "invalid_json"


def kind_for_schema_error(
    error: str,
    payload: dict[str, Any] | None = None,
    exc: Exception | None = None,
) -> ParseFailureKind:
    """Unknown top-level keys and bad envelopes are invalid decisions, not malformed JSON."""
    extra = set(payload or ()) - _COMPACT_ROOT_KEYS
    if extra or "unexpected top-level keys" in (error or ""):
        return "invalid_decision"
    if isinstance(exc, InvalidDecisionEnvelope):
        return "invalid_decision"
    return "invalid_json"


_PARSE_KIND_LABELS = {
    "prose_status": "prose status instead of a JSON envelope",
    "invalid_decision": "invented or disallowed keys",
    "invalid_json": "truncated or broken JSON",
}


def parse_failure_should_repair(
    *,
    kind: str,
    repair_attempts: int,
    max_repairs: int,
    decision: bool = False,
) -> bool:
    """Return whether this parse failure may take another repair wake.

    Decision turns repair every parse kind until ``max_repairs``.
    Execution turns still fail-close prose and invented keys immediately.
    """
    if decision:
        return repair_attempts < max_repairs
    if kind in {"prose_status", "invalid_decision"}:
        return False
    return repair_attempts < max_repairs


def describe_decision_parse_failure(kind: str, snippet: str = "") -> str:
    """Describe what failed so a repair wake can name it."""
    label = _PARSE_KIND_LABELS.get(kind, "invalid decision")
    extra = " ".join((snippet or "").split())
    if len(extra) > 180:
        extra = extra[:177].rstrip() + "..."
    if extra:
        return f"{label}: {extra}"
    return label


def parse_failure_steer(kind: str, snippet: str = "") -> str:
    """Return the fail-closed steer for a parse failure."""
    extra = (snippet or "").strip()
    if kind == "invalid_decision":
        base = INVALID_DECISION_STEER
    else:
        base = PROSE_STATUS_STEER
    if kind == "prose_status":
        return base
    if extra:
        return f"{base} Parser error: {extra}"
    return base


def parse_failed_payload(
    raw_response: str,
    *,
    decision: bool,
    snippet: str | None = None,
    kind: ParseFailureKind | None = None,
    thought: str = "",
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the structured ``_parse_failed`` payload used by both contracts."""
    resolved = kind or classify_json_parse_failure(raw_response)
    text = (snippet if snippet is not None else raw_response)[:200]
    if decision:
        payload: dict[str, Any] = {
            "decision": "_parse_failed",
            "thought": thought,
            "_raw_snippet": text,
            "_parse_kind": resolved,
        }
    else:
        if not thought:
            thought = (
                "No action in response" if resolved == "prose_status" else "Failed to parse response"
            )
        payload = {
            "action": "_parse_failed",
            "thought": thought,
            "_raw_snippet": text,
            "_parse_kind": resolved,
        }
    if candidate is not None:
        payload["_candidate_payload"] = candidate
    return payload


def parse_next_owner_ids(value: Any) -> list[str]:
    """Return ordered unique agent ids from ``next_owners``.

    Omitted or null is an empty list. A non-list, or a blank item, is
    fail-closed. Duplicates collapse. This does not read @ text.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidDecisionEnvelope('"next_owners" must be an array of ids')
    found: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise InvalidDecisionEnvelope('"next_owners" must be an array of ids')
        token = item.strip()
        if token in seen:
            continue
        seen.add(token)
        found.append(token)
    return found


def resolve_operator_chat(payload: dict[str, Any]) -> str | None:
    """Return ``say`` / ``msg`` chat text, or None when absent."""
    return _resolve_chat_alias(payload.get("say"), payload.get("msg"))


def read_work_commit(payload: dict[str, Any]) -> bool | None:
    """Return the envelope ``work_commit`` flag.

    Omitted is ``None`` (intent was not declared). A non-boolean is
    fail-closed. This does not read the say text.
    """
    if "work_commit" not in payload:
        return None
    value = payload.get("work_commit")
    if isinstance(value, bool):
        return value
    raise InvalidDecisionEnvelope('"work_commit" must be a boolean when provided')


def peel_decision_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Map ``say`` / ``actions`` onto the existing compact act/msg object.

    Empty ``actions`` is a no-work chat envelope. One nested compact action
    unwraps to that same object (not a second schema). Invented keys stay
    fail-closed. ``work_commit`` is validated and stripped here so lookup
    acts keep their existing shape; the conversation parser reattaches it.
    """
    extra = set(payload) - _COMPACT_ROOT_KEYS
    if extra:
        raise InvalidDecisionEnvelope(
            f'unexpected top-level keys: {", ".join(sorted(extra))}'
        )
    read_work_commit(payload)

    chat = _resolve_chat_alias(payload.get("say"), payload.get("msg"))
    owners = parse_next_owner_ids(payload.get("next_owners")) if "next_owners" in payload else None
    action_items = _coerce_actions(payload.get("actions"))
    remainder = {key: value for key, value in payload.items() if key not in _ENVELOPE_KEYS}
    if chat is not None:
        remainder["msg"] = chat

    if action_items:
        remainder = _unwrap_single_action(remainder, action_items[0], chat)

    folded = _fold_chat_onto_act(remainder)
    return _merge_next_owners(folded, owners)


def _coerce_actions(value: Any) -> list[Any] | None:
    """Return an actions list, or None when the field is omitted."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise InvalidDecisionEnvelope('"actions" must be an array when provided')
    if len(value) > 1:
        raise InvalidDecisionEnvelope('"actions" may contain at most one compact action')
    return value


def _unwrap_single_action(
    remainder: dict[str, Any],
    inner: Any,
    chat: str | None,
) -> dict[str, Any]:
    """Flatten one nested compact action into the existing top-level shape."""
    if not isinstance(inner, dict):
        raise InvalidDecisionEnvelope('each item in "actions" must be an object')
    inner_extra = set(inner) - _COMPACT_ACTION_KEYS
    if inner_extra:
        raise InvalidDecisionEnvelope(
            f'unexpected top-level keys: {", ".join(sorted(inner_extra))}'
        )
    if remainder.get("act") not in (None, ""):
        raise InvalidDecisionEnvelope(
            'do not combine top-level "act" with non-empty "actions"'
        )

    merged = dict(inner)
    inner_chat = _optional_chat_text(merged.get("msg"), "msg")
    if chat is not None and inner_chat is not None and chat != inner_chat:
        raise InvalidDecisionEnvelope('"say" and "msg" must match when both are provided')
    if chat is not None and inner_chat is None:
        merged["msg"] = chat
    for key in ("th", "intent", "commit", "data"):
        if remainder.get(key) not in (None, "", {}) and merged.get(key) in (None, "", {}):
            merged[key] = remainder[key]
    return merged


def _resolve_chat_alias(say: Any, msg: Any) -> str | None:
    """Return the operator-visible chat text from ``say`` and/or ``msg``."""
    say_text = _optional_chat_text(say, "say")
    msg_text = _optional_chat_text(msg, "msg")
    if say_text is not None and msg_text is not None and say_text != msg_text:
        raise InvalidDecisionEnvelope('"say" and "msg" must match when both are provided')
    return say_text if say_text is not None else msg_text


def _optional_chat_text(value: Any, field: str) -> str | None:
    """Treat omitted/blank chat as absent; reject non-strings."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidDecisionEnvelope(f'"{field}" must be a non-empty string when provided')
    return value


def _fold_chat_onto_act(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep ``msg`` on conversation acts; map it onto execution follow-up fields."""
    act = payload.get("act")
    chat = payload.get("msg")
    if not isinstance(chat, str) or not chat.strip():
        return payload
    if act in _CONVERSATION_ACTS or act in (None, ""):
        return payload

    folded = dict(payload)
    if act in _EXECUTION_CHAT_ACTS:
        data = folded.get("data")
        if data in (None, ""):
            data = {}
        if isinstance(data, dict) and data.get("msg") in (None, ""):
            folded["data"] = {**data, "msg": chat}
        folded.pop("msg", None)
        return folded

    if act in _LOOKUP_ACTS or isinstance(act, str):
        if folded.get("th") in (None, ""):
            folded["th"] = chat
        folded.pop("msg", None)
        folded.pop("intent", None)
        folded.pop("commit", None)
        return folded
    return payload


def _merge_next_owners(payload: dict[str, Any], owners: list[str] | None) -> dict[str, Any]:
    """Keep one ``next_owners`` list. Envelope and action must agree."""
    if owners is None:
        return payload
    if "next_owners" in payload:
        inner = parse_next_owner_ids(payload.get("next_owners"))
        if inner != owners:
            raise InvalidDecisionEnvelope(
                '"next_owners" must match when set on both the envelope and the action'
            )
        return payload
    merged = dict(payload)
    merged["next_owners"] = owners
    return merged


def _strip_fences(raw_response: str) -> str:
    text = (raw_response or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    lines = [line for line in lines if not line.strip().startswith("```")]
    return "\n".join(lines).strip()
