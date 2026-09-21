"""Order and dispatch mode for one channel response round.

Status and discussion use ordered rounds. Fan-out stays on a narrow
allowlist: @everyone, an explicit parallel ask, or distinct specialty
tools aimed at different members. Anything ambiguous stays on rounds.
"""

from __future__ import annotations

import re
from typing import Any

from core import config
from core.agent_loop.next_owner import HUMAN_MENTION_NAMES, extract_next_owner_mentions
from core.agent_loop.runtime_core import ALLOWED_TOOLS
from core.agent_loop.specialty import work_kind_label

DISPATCH_ROUNDS = "rounds"
DISPATCH_FANOUT = "fanout"

DEFAULT_ROUND_CAP = 4
ROUND_CAP_SETTING = "channel_response_round_cap"
MAX_CONCURRENT_AGENT_TURNS_SETTING = "max_concurrent_agent_turns"
DEFAULT_MAX_CONCURRENT_AGENT_TURNS = 2

_EVERYONE = "everyone"
_STATUS_DISCUSS = re.compile(
    r"(?i)\b("
    r"where are we|status|thoughts|discuss|discussion|feedback|"
    r"what do you think|how should we|check in|check-in"
    r")\b"
)
_PARALLEL = re.compile(r"(?i)\b(in parallel|at the same time|simultaneously)\b")
_PASS_REPLY = re.compile(
    r"(?is)^\s*(?:"
    r"i['’]?ll stay quiet\.?|"
    r"i will stay quiet\.?|"
    r"staying quiet\.?|"
    r"i['’]?ll pass\.?|"
    r"i will pass\.?|"
    r"i pass\.?|"
    r"pass\.?|"
    r"passing\.?|"
    r"stepping out\.?|"
    r"step out\.?|"
    r"nothing to add\.?|"
    r"nothing from me\.?|"
    r"no comment\.?|"
    r"sitting this one out\.?"
    r")\s*$"
)
_HUMAN_NAMES = {name.lower() for name in HUMAN_MENTION_NAMES}


def channel_response_round_cap() -> int:
    """Return the soft cap on rounds for one human message, including round 1."""
    configured = config.get_int(ROUND_CAP_SETTING)
    if configured is None or configured < 1:
        return DEFAULT_ROUND_CAP
    return configured


def max_concurrent_agent_turns() -> int:
    """Return the global cap on agent turns that may run at once."""
    configured = config.get_int(MAX_CONCURRENT_AGENT_TURNS_SETTING)
    if configured is None or configured < 1:
        return DEFAULT_MAX_CONCURRENT_AGENT_TURNS
    return configured


def is_pass_reply(text: str | None) -> bool:
    """Return True when a reply is only a pass and must not hit the channel."""
    return bool(_PASS_REPLY.match(text or ""))


def order_round_members(
    member_ids: list[str],
    *,
    lead_id: str | None,
    mentioned_ids: list[str],
    round_index: int,
) -> list[str]:
    """Mentioned members first, then a stable lead ring rotated per round.

    ``member_ids`` is membership order (oldest first). The lead starts the
    ring. Mentions keep the order they were written. The non-mentioned tail
    rotates by ``round_index - 1`` so follow-up rounds round-robin.
    """
    pool = []
    seen: set[str] = set()
    for agent_id in member_ids:
        token = (agent_id or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        pool.append(token)
    if not pool:
        return []

    lead = (lead_id or "").strip()
    ring = [lead] if lead in seen else []
    ring.extend(agent_id for agent_id in pool if agent_id != lead)

    prefix: list[str] = []
    for agent_id in mentioned_ids:
        token = (agent_id or "").strip()
        if token in seen and token not in prefix:
            prefix.append(token)
    tail = [agent_id for agent_id in ring if agent_id not in prefix]
    if tail:
        shift = (max(round_index, 1) - 1) % len(tail)
        tail = tail[shift:] + tail[:shift]
    return prefix + tail


def classify_channel_dispatch(
    content: str,
    members: list[dict[str, Any]],
) -> str:
    """Return ``fanout`` only for the narrow allowlist. Ambiguous stays on rounds."""
    names = [str(member.get("name") or "").strip() for member in members]
    names = [name for name in names if name]
    mentions = extract_next_owner_mentions(content or "", member_names=names)
    if _EVERYONE in mentions:
        return DISPATCH_FANOUT

    by_name = {
        str(member.get("name") or "").strip().lower(): str(member.get("id") or "").strip()
        for member in members
        if str(member.get("name") or "").strip() and str(member.get("id") or "").strip()
    }
    agent_mentions = [
        mention
        for mention in mentions
        if mention.lower() not in _HUMAN_NAMES and mention != _EVERYONE and mention.lower() in by_name
    ]
    if _PARALLEL.search(content or "") and len(agent_mentions) >= 2:
        return DISPATCH_FANOUT
    if _distinct_specialty_tools(content or "", agent_mentions):
        return DISPATCH_FANOUT
    return DISPATCH_ROUNDS


def mention_ids_in_order(
    content: str,
    members: list[dict[str, Any]],
) -> list[str]:
    """Return channel member ids @mentioned in the text, in mention order."""
    names = [str(member.get("name") or "").strip() for member in members]
    mentions = extract_next_owner_mentions(content or "", member_names=names)
    by_name = {
        str(member.get("name") or "").strip().lower(): str(member.get("id") or "").strip()
        for member in members
        if str(member.get("name") or "").strip() and str(member.get("id") or "").strip()
    }
    ordered: list[str] = []
    for mention in mentions:
        if mention == _EVERYONE:
            for member in members:
                agent_id = str(member.get("id") or "").strip()
                if agent_id and agent_id not in ordered:
                    ordered.append(agent_id)
            continue
        if mention.lower() in _HUMAN_NAMES:
            continue
        agent_id = by_name.get(mention.lower())
        if agent_id and agent_id not in ordered:
            ordered.append(agent_id)
    return ordered


def _distinct_specialty_tools(content: str, agent_mentions: list[str]) -> bool:
    """Return True when two @members are given different tool or specialty work.

    Status and discussion copy does not qualify. Missing labels stay ambiguous
    and therefore stay on rounds.
    """
    if len(agent_mentions) < 2 or _STATUS_DISCUSS.search(content):
        return False
    labels: list[str] = []
    for mention, clause in _mention_clauses(content, agent_mentions):
        del mention
        label = _clause_label(clause)
        if label:
            labels.append(label)
    return len(labels) >= 2 and len(set(labels)) >= 2


def _mention_clauses(content: str, agent_mentions: list[str]) -> list[tuple[str, str]]:
    """Split the text into the span that follows each agent @mention."""
    clauses: list[tuple[str, str]] = []
    cursor = 0
    blob = content or ""
    for mention in agent_mentions:
        needle = f"@{mention}"
        found = blob.lower().find(needle.lower(), cursor)
        if found < 0:
            clauses.append((mention, ""))
            continue
        start = found + len(needle)
        next_at = blob.find("@", start)
        clause = blob[start: next_at if next_at >= 0 else len(blob)]
        clauses.append((mention, clause))
        cursor = start
    return clauses


def _clause_label(clause: str) -> str | None:
    """Map one mention's following words onto a tool or specialty family."""
    for tool in ALLOWED_TOOLS:
        if re.search(rf"(?i)\b{re.escape(tool)}\b", clause or ""):
            return f"tool:{tool}"
    family = work_kind_label(clause)
    if family:
        return f"family:{family}"
    return None
