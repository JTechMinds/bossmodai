"""Debra's lock: a say that commits to work must not go silent.

Intent is a structured ``work_commit`` flag on the decision envelope, or one
short System AI check when that flag is omitted. Phrase lists and mention
pills are not the gate. A bad envelope, empty actions, or an exhausted
Decision Repair budget then posts one Needs note and re-queues the open
commitment. Ambient say is left alone. The promise is not Board Done, and
runtime is not wiped.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from core.agent_loop.decision_parse_fail import surface_commitment_recovery
from core.agent_loop.task_origin_mirrors import persist_unbound_status_line
from core.llm.system_completion import complete_text
from core.models import Agent

logger = logging.getLogger(__name__)

EMPTY_ACTIONS_WHY = "actions were empty"
BAD_JSON_WHY = "the envelope was not valid JSON"
REPAIR_EXHAUSTED_WHY = "Decision Repair Attempts were exhausted"
ENVELOPE_WHY = "the turn did not return one JSON envelope"

PROMISE_RESUME_REASON = "Work was claimed in say. Continue the committed work."

# Structural scan of a broken blob. Not a say-phrase list.
_WORK_COMMIT_FLAG = re.compile(r'"work_commit"\s*:\s*(true|false)\b')
_SAY_CHARS = 500
_INTENT_SYSTEM = (
    "Judge whether this say commits to doing the work on this turn. "
    'Return one JSON object and nothing else: {"commits_to_work": true} '
    'or {"commits_to_work": false}. '
    "true means the speaker is starting that work now. "
    "false means status, a finished report, a question, a later maybe, or casual chat. "
    "Judge the intent. Do not use a phrase list or mention pills."
)


def say_commits_to_work(
    text: str | None = None,
    *,
    work_commit: bool | None = None,
) -> bool:
    """Return whether this say commits to immediate work.

    An explicit ``work_commit`` flag is the decision. When the flag is
    omitted, one System AI check reads the say. No flag and no usable
    System AI answer stays quiet, including phrasing a list would have
    matched.
    """
    if work_commit is True:
        return True
    if work_commit is False:
        return False
    return _system_commits_to_work(text)


def response_commits_to_work(raw: str | None) -> bool:
    """Return whether a raw model blob commits to immediate work."""
    say, flag = commitment_signal(raw)
    return say_commits_to_work(say, work_commit=flag)


def commitment_signal(raw: str | None) -> tuple[str | None, bool | None]:
    """Return ``(say, work_commit)`` from a model blob, including broken JSON.

    The flag is only an explicit JSON boolean. This does not judge the say.
    """
    text = _strip_fences(raw or "")
    if not text:
        return None, None
    payload = _json_object(text)
    if payload is not None:
        return _say_from_object(payload), _flag_from_object(payload)
    say = _json_string_value(text, "say") or _json_string_value(text, "msg")
    flag = _flag_from_broken(text)
    if say is None and "{" not in text:
        say = text.strip() or None
    return say, flag


def promise_gap_note(why: str) -> str:
    """One Needs line: the why, and that the commitment was re-queued."""
    reason = " ".join((why or "").split()) or "the work did not run"
    return (
        f"Needs — work was claimed in say but {reason}. "
        "The commitment was re-queued."
    )


def promise_fail_why(*, repair_attempts: int, kind: str) -> str:
    """Name the terminal envelope failure after a committed say."""
    if repair_attempts > 0:
        return REPAIR_EXHAUSTED_WHY
    if kind == "invalid_json":
        return BAD_JSON_WHY
    return ENVELOPE_WHY


def surface_promise_gap(
    *,
    agent: Agent,
    trigger: dict[str, Any] | None,
    why: str,
) -> dict[str, Any]:
    """Post one Needs note and re-queue an open commitment. Does not mark Done."""
    note = promise_gap_note(why)
    surfaced = surface_commitment_recovery(
        agent=agent,
        trigger=trigger,
        note=note,
        resume_reason=PROMISE_RESUME_REASON,
    )
    if surfaced.get("chat_message") or surfaced.get("channel_message"):
        return surfaced
    posted = persist_unbound_status_line(
        agent=agent,
        content=note,
        kind="task_update",
        channel_id=_trigger_channel_id(trigger),
    )
    if posted.get("chat_message"):
        surfaced["chat_message"] = posted["chat_message"]
    if posted.get("channel_message"):
        surfaced["channel_message"] = posted["channel_message"]
    return surfaced


def merge_promise_gap(result: dict[str, Any], surfaced: dict[str, Any]) -> None:
    """Attach one re-queue and the Needs note without clobbering a posted say."""
    requests = result.setdefault("trigger_requests", [])
    seen = {
        (item.get("trigger_type"), item.get("task_id"))
        for item in requests
        if isinstance(item, dict)
    }
    for item in surfaced.get("trigger_requests") or []:
        if not isinstance(item, dict):
            continue
        key = (item.get("trigger_type"), item.get("task_id"))
        if key in seen:
            continue
        requests.append(item)
        seen.add(key)
    for key in ("chat_message", "channel_message"):
        payload = surfaced.get(key)
        if not isinstance(payload, dict):
            continue
        if result.get(key):
            result.setdefault("origin_status_messages", []).append(payload)
        else:
            result[key] = payload


def _system_commits_to_work(say: str | None) -> bool:
    """One cheap System AI intent check. Unreadable or missing stays quiet."""
    text = " ".join((say or "").split())
    if not text:
        return False
    if len(text) > _SAY_CHARS:
        text = text[:_SAY_CHARS].rstrip()
    raw = complete_text(
        [
            {"role": "system", "content": _INTENT_SYSTEM},
            {"role": "user", "content": text},
        ],
        max_tokens=64,
    )
    parsed = _parse_intent(raw)
    if raw is not None and parsed is None:
        logger.debug("work-commit intent check ignored an unreadable System AI payload")
    return parsed is True


def _parse_intent(raw: str | None) -> bool | None:
    text = _strip_fences(raw or "")
    if not text:
        return None
    payload = _json_object(text)
    if payload is None or set(payload) != {"commits_to_work"}:
        return None
    value = payload.get("commits_to_work")
    if isinstance(value, bool):
        return value
    return None


def _flag_from_object(payload: dict[str, Any]) -> bool | None:
    if "work_commit" not in payload:
        return None
    value = payload.get("work_commit")
    if isinstance(value, bool):
        return value
    return None


def _flag_from_broken(text: str) -> bool | None:
    match = _WORK_COMMIT_FLAG.search(text)
    if match is None:
        return None
    return match.group(1) == "true"


def _say_from_object(payload: dict[str, Any]) -> str | None:
    for key in ("say", "msg"):
        found = _plain_text(payload.get(key))
        if found is not None:
            return found
    actions = payload.get("actions")
    if isinstance(actions, list):
        for item in actions:
            if not isinstance(item, dict):
                continue
            for key in ("msg", "say"):
                found = _plain_text(item.get(key))
                if found is not None:
                    return found
    return None


def _plain_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end])
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _json_string_value(text: str, key: str) -> str | None:
    marker = f'"{key}"'
    start = 0
    while True:
        idx = text.find(marker, start)
        if idx < 0:
            return None
        colon = text.find(":", idx + len(marker))
        if colon < 0:
            return None
        cursor = colon + 1
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor < len(text) and text[cursor] == '"':
            return _decode_json_string(text, cursor)
        start = idx + len(marker)


def _decode_json_string(text: str, quote_at: int) -> str | None:
    chars: list[str] = []
    cursor = quote_at + 1
    while cursor < len(text):
        char = text[cursor]
        if char == '"':
            decoded = "".join(chars).strip()
            return decoded or None
        if char != "\\":
            chars.append(char)
            cursor += 1
            continue
        if cursor + 1 >= len(text):
            return None
        nxt = text[cursor + 1]
        mapped = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "b": "\b",
            "f": "\f",
        }
        if nxt in mapped:
            chars.append(mapped[nxt])
            cursor += 2
            continue
        if nxt == "u" and cursor + 6 <= len(text):
            hexes = text[cursor + 2 : cursor + 6]
            try:
                chars.append(chr(int(hexes, 16)))
            except ValueError:
                return None
            cursor += 6
            continue
        return None
    return None


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = [line for line in text.split("\n") if not line.strip().startswith("```")]
    return "\n".join(lines).strip()


def _trigger_channel_id(trigger: dict[str, Any] | None) -> str | None:
    if not isinstance(trigger, dict):
        return None
    raw = trigger.get("channel_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
