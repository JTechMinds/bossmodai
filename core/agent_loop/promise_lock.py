"""Debra's lock: a say that commits to work must not go silent.

``landing G0 now``, ``writing … now``, and ``running pytest now`` are work
promises. A bad envelope, empty actions, or an exhausted Decision Repair
budget then posts one Needs note and re-queues the open commitment. Ambient
say is left alone. The promise is not Board Done, and runtime is not wiped.
"""

from __future__ import annotations

import re
from typing import Any

from core.agent_loop.decision_parse_fail import surface_commitment_recovery
from core.agent_loop.task_origin_mirrors import persist_unbound_status_line
from core.models import Agent

_WORK_PROMISE = re.compile(
    r"(?is)\b(?:landing|writing|running\s+pytest)\b.{0,80}?\bnow\b"
)

EMPTY_ACTIONS_WHY = "actions were empty"
BAD_JSON_WHY = "the envelope was not valid JSON"
REPAIR_EXHAUSTED_WHY = "Decision Repair Attempts were exhausted"
ENVELOPE_WHY = "the turn did not return one JSON envelope"

PROMISE_RESUME_REASON = "Work was claimed in say. Continue the committed work."


def say_commits_to_work(text: str | None) -> bool:
    """Return whether ``say`` promises immediate work, not ambient status."""
    return bool(_WORK_PROMISE.search(text or ""))


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


def _trigger_channel_id(trigger: dict[str, Any] | None) -> str | None:
    if not isinstance(trigger, dict):
        return None
    raw = trigger.get("channel_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
