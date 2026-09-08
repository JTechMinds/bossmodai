"""Soft next-owner nudge for untagged multi-party thread replies.

When an agent posts a normal reply in a multi-party thread (human + ≥2
agents) without naming a next owner, the runtime emits a one-time-per-turn
world_feedback nudge. Proceed continues without a tag. This is not a hard
reject and does not invent @everyone.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import db
from core.agent_loop.decision_contract import ConversationDecision
from core.default_prompts import render_default_prompt
from core.models import Agent
from core.models.host_path_consent import WORKSPACE_PREFERENCE_KIND

NUDGE_COPY = "No next owner tagged — this thread may go stale. Proceed anyway?"
NUDGE_PROCEED_LABEL = "Proceed"
NUDGE_ADD_LABEL = "Add @Name / @everyone"
NUDGE_FEEDBACK_CODE = "next_owner_nudge"
NUDGE_SHOWN_KEY = "next_owner_nudge_shown"

HUMAN_MENTION_NAMES = ("Human", "Operator", "Human Operator")
_EVERYONE = "everyone"

_NORMAL_REPLY_DECISIONS = frozenset({"answer", "clarify"})
_FOCUS_OR_DM_TRIGGERS = frozenset({"human_chat", "peer_message"})
_CHANNEL_REPLY_TRIGGERS = frozenset({"channel_message", "channel_response"})
_TASK_REPLY_TRIGGERS = frozenset({"task_assigned", "task_follow_up"})
_EXEMPT_NOTIFICATION_KINDS = frozenset(
    {
        "completion",
        "blocked",
        "receipt",
        "handoff",
        "host_path_consent",
        WORKSPACE_PREFERENCE_KIND,
    }
)

# Locked origin-thread one-liners (Created/Accepted/Writing/…).
_SYSTEM_ONE_LINER = re.compile(
    r"^(Created|Accepted|Writing|Waiting|Stalled|Declined|Rerouted|"
    r"Cancelled|Done|Blocked)\b"
)
# Explicit park-the-ball language. Conservative: only these phrases count.
_PARK_BALL = re.compile(
    r"(?i)\b("
    r"standing by|"
    r"no action needed|"
    r"no further action|"
    r"parking (this|the ball)|"
    r"ball is parked"
    r")\b"
)
_REACTION_WORDS = frozenset(
    {"ok", "okay", "thanks", "thank you", "thx", "+1", "lgtm", "ack", "noted"}
)
_EMOJI_OR_SYMBOL = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F900-\U0001F9FF"
    r"\U00002600-\U000026FF\u200d\ufe0f\u20e3]+"
)
_NAME_TRAIL = re.compile(r"[A-Za-z0-9._-]")
_LOOP_PROMPT_PATHS = {"members"}


def is_multi_party_channel(channel_id: str | None) -> bool:
    """Return True when a channel is a multi-party thread (human + ≥2 agents)."""
    token = (channel_id or "").strip()
    if not token:
        return False
    return len(db.list_channel_member_details(token)) >= 2


def reply_channel_id(trigger: dict[str, Any] | None) -> str | None:
    """Return the channel a conversational reply would post to, if any."""
    if not isinstance(trigger, dict):
        return None
    trigger_type = str(trigger.get("type") or "")
    raw = trigger.get("channel_id")
    if trigger_type in _CHANNEL_REPLY_TRIGGERS and isinstance(raw, str) and raw.strip():
        return raw.strip()
    if trigger_type not in _TASK_REPLY_TRIGGERS:
        return None
    task_id = trigger.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return None
    task = db.get_task(task_id)
    if task is None:
        return None
    channel_id = getattr(task, "notification_channel_id", None)
    if getattr(task, "source_channel", None) != "channel":
        return None
    if not isinstance(channel_id, str) or not channel_id.strip():
        return None
    return channel_id.strip()


def mention_names_for_channel(channel_id: str) -> list[str]:
    """Return @-mention candidates: member names plus the human operator aliases."""
    names = [
        str(member.get("name") or "").strip()
        for member in db.list_channel_member_details(channel_id)
    ]
    names.extend(HUMAN_MENTION_NAMES)
    return [name for name in names if name]


def extract_next_owner_mentions(text: str, *, member_names: Iterable[str]) -> list[str]:
    """Return member/@everyone mentions found in text. Conservative: @ required."""
    blob = text or ""
    names = sorted({name.strip() for name in member_names if name and name.strip()}, key=len, reverse=True)
    found: list[str] = []
    index = 0
    while index < len(blob):
        if blob[index] != "@":
            index += 1
            continue
        rest = blob[index + 1 :]
        lowered = rest.lower()
        if lowered.startswith(_EVERYONE) and _mention_boundary(rest, len(_EVERYONE)):
            found.append(_EVERYONE)
            index += 1 + len(_EVERYONE)
            continue
        matched: str | None = None
        for name in names:
            if lowered.startswith(name.lower()) and _mention_boundary(rest, len(name)):
                matched = name
                break
        if matched is not None:
            found.append(matched)
            index += 1 + len(matched)
        else:
            index += 1
    return found


def has_next_owner_tag(
    text: str,
    *,
    member_names: Iterable[str],
    author_name: str | None = None,
) -> bool:
    """Return True when the reply names a next owner or clearly parks the ball.

    Prefer an @-mention of a thread member or @everyone. Self-only mentions
    do not count. Standing-by / no-action language is accepted only for the
    locked park phrases above.
    """
    mentions = extract_next_owner_mentions(text, member_names=member_names)
    author = (author_name or "").strip().lower()
    for mention in mentions:
        if mention == _EVERYONE:
            return True
        if author and mention.lower() == author:
            continue
        return True
    return bool(_PARK_BALL.search(text or ""))


def is_system_one_liner(text: str | None) -> bool:
    """Return True for Created/Accepted/Writing/… mirror lines."""
    return bool(_SYSTEM_ONE_LINER.match((text or "").strip()))


def is_pure_reaction(text: str | None) -> bool:
    """Return True for a short reaction with no request and no next-owner need."""
    blob = " ".join((text or "").strip().split())
    if not blob or "?" in blob:
        return False
    compact = blob.rstrip(".!").strip().lower()
    if compact in _REACTION_WORDS:
        return True
    stripped = _EMOJI_OR_SYMBOL.sub("", blob).strip()
    return not stripped and bool(_EMOJI_OR_SYMBOL.search(blob))


def is_exempt_reply(text: str | None, *, trigger: dict[str, Any] | None = None) -> bool:
    """Return True for system lines, consent/preference cards, or pure reactions."""
    payload = trigger if isinstance(trigger, dict) else {}
    kind = str(payload.get("notification_kind") or "").strip().lower()
    if kind in _EXEMPT_NOTIFICATION_KINDS:
        return True
    if payload.get("consent_id") or payload.get("host_path_consent"):
        return True
    if str(payload.get("author_type") or "").strip().lower() == "system":
        return True
    if is_system_one_liner(text):
        return True
    return is_pure_reaction(text)


def should_emit_next_owner_nudge(
    *,
    trigger: dict[str, Any],
    decision: ConversationDecision,
    author_name: str,
) -> bool:
    """Return True when this normal reply should get the one-time soft nudge."""
    if decision.decision not in _NORMAL_REPLY_DECISIONS:
        return False
    if decision.proceedUntagged:
        return False
    if trigger.get(NUDGE_SHOWN_KEY):
        return False
    trigger_type = str(trigger.get("type") or "")
    if trigger_type in _FOCUS_OR_DM_TRIGGERS:
        return False
    channel_id = reply_channel_id(trigger)
    if not channel_id or not is_multi_party_channel(channel_id):
        return False
    reply = (decision.reply or "").strip()
    if not reply:
        return False
    if is_exempt_reply(reply, trigger=trigger):
        return False
    names = mention_names_for_channel(channel_id)
    if has_next_owner_tag(reply, member_names=names, author_name=author_name):
        return False
    return True


def next_owner_nudge_result(agent: Agent, *, member_names: Iterable[str] | None = None) -> dict[str, Any]:
    """Return the soft world_feedback payload with Debra's locked copy."""
    names = [name for name in (member_names or []) if name]
    return {
        "event": "world_feedback",
        "feedback_code": NUDGE_FEEDBACK_CODE,
        "detail": NUDGE_COPY,
        "agent_name": agent.name,
        "nudge_actions": [NUDGE_PROCEED_LABEL, NUDGE_ADD_LABEL],
        "nudge_members": names,
        "expected_action": "proceed_or_tag",
    }


def mark_next_owner_nudge_shown(trigger: dict[str, Any]) -> None:
    """Record that this agent turn already showed the nudge."""
    trigger[NUDGE_SHOWN_KEY] = True


def maybe_next_owner_nudge(
    agent: Agent,
    trigger: dict[str, Any],
    decision: ConversationDecision,
) -> dict[str, Any] | None:
    """Return the nudge result and mark the turn, or None when send may continue."""
    if not should_emit_next_owner_nudge(trigger=trigger, decision=decision, author_name=agent.name):
        return None
    channel_id = reply_channel_id(trigger)
    names = mention_names_for_channel(channel_id) if channel_id else []
    mark_next_owner_nudge_shown(trigger)
    return next_owner_nudge_result(agent, member_names=names)


def next_owner_nudge_continuation(*, member_names: Iterable[str] | None = None) -> list[dict[str, str]]:
    """Build the same-turn continuation after the soft nudge."""
    visible = [
        name
        for name in (member_names or [])
        if name and name.lower() not in {_EVERYONE}
    ]
    # Deduplicate while keeping roster order, then the human aliases.
    seen: set[str] = set()
    labels: list[str] = []
    for name in visible:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        labels.append(f"@{name}")
    members = ", ".join(labels) if labels else "@Name"
    return [
        {
            "role": "system",
            "content": render_default_prompt(
                "internal_loop_decision_next_owner_nudge",
                {"members": members},
                allowed_paths=_LOOP_PROMPT_PATHS,
            ),
        }
    ]


def _mention_boundary(rest: str, length: int) -> bool:
    """Return True when a mention ends at ``length`` rather than mid-token."""
    if length > len(rest):
        return False
    if length == len(rest):
        return True
    return _NAME_TRAIL.match(rest[length]) is None
