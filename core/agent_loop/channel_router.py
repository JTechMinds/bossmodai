"""System AI route for one channel response round.

One short completion on ``system_ai_connection`` sees the latest message,
member specialties, pending @ ids, and a short sticky clip. The only
accepted payload is:

    {"speak": ["agent-id", ...], "stay_out": ["agent-id", ...]}

``speak`` is ordered. Any other key, an unknown id, a duplicate, overlap
between the lists, or a non-list rejects the payload. The caller then
uses the existing drain order and each member gets a normal soft-judge
turn.

``ROUTER_SPEAK_CAP`` (default 2) is the maximum extra fan-out. Human @
mentions, and any other ids the caller marks as required, stay first and
are never removed to meet the cap. When those required ids already fill
the cap, no further id is taken from ``speak``. Ids the model omits are
stay_out. Stay-out members are an engine pass: no identity-model turn.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import db
from core.agent_loop.standing_prefs import read_standing_prefs
from core.llm.system_completion import complete_text, system_ai_is_configured

logger = logging.getLogger(__name__)

# Speakers taken from one System AI ``speak`` list after required @ ids.
# Required @ ids are not dropped to satisfy this number. Default 2 stops
# a route from waking the whole channel into essays.
ROUTER_SPEAK_CAP = 2
ROUTER_KEYS = frozenset({"speak", "stay_out"})

_LATEST_MESSAGE_CHARS = 800
_STICKY_CHARS = 400
_PREF_CHARS = 80
_OPENING_CHARS = 160
_PREFS_IN_STICKY = 3


@dataclass(frozen=True, slots=True)
class RoundPlan:
    """Who speaks, who is an engine pass, and whether the router produced this."""

    speak: list[str]
    stay_out: list[str]
    mode: str
    pinned: list[str]


def plan_channel_route(
    *,
    members: list[dict[str, str]],
    fallback_order: list[str],
    latest_message: str,
    pending_mention_ids: list[str],
    forced_ids: list[str],
    opening_message: str = "",
) -> RoundPlan:
    """Return a system plan, or the drain order when System AI cannot route.

    ``fallback_order`` is the #124 member order and the only legal id set.
    ``forced_ids`` and ``pending_mention_ids`` are required. They stay at
    the front of ``speak`` in that order and are stored on ``pinned``.
    Human @ mentions are passed there so the router cannot drop them.
    """
    universe = _unique(fallback_order)
    allowed = set(universe)
    pinned = []
    for agent_id in list(forced_ids) + list(pending_mention_ids):
        if agent_id in allowed and agent_id not in pinned:
            pinned.append(agent_id)
    fallback = RoundPlan(speak=list(universe), stay_out=[], mode="fallback", pinned=list(pinned))
    if not universe or not system_ai_is_configured():
        return fallback
    roster = [member for member in members if str(member.get("id") or "") in allowed]
    opening = opening_message if opening_message.strip() != (latest_message or "").strip() else ""
    try:
        sticky = short_sticky_context(roster, opening_message=opening)
    except Exception:
        logger.warning("channel router skipped sticky context")
        sticky = ""
    raw = complete_text(
        build_router_messages(
            members=roster,
            latest_message=latest_message,
            pending_mention_ids=pinned,
            sticky=sticky,
        )
    )
    if raw is None:
        return fallback
    parsed = parse_router_payload(raw, universe)
    if parsed is None:
        logger.info("channel router rejected payload; using drain order")
        return fallback
    speak, stay_out = finalize_router_lists(
        universe,
        forced_ids=pinned,
        speak=parsed[0],
        cap=ROUTER_SPEAK_CAP,
    )
    kept_pins = [agent_id for agent_id in pinned if agent_id in set(speak)]
    return RoundPlan(speak=speak, stay_out=stay_out, mode="system", pinned=kept_pins)


def parse_router_payload(raw: str, member_ids: list[str]) -> tuple[list[str], list[str]] | None:
    """Parse a fail-closed router object. None rejects the payload."""
    try:
        payload = json.loads(_unwrap_json(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or set(payload) != ROUTER_KEYS:
        return None
    allowed = {agent_id for agent_id in member_ids if agent_id}
    speak = _id_list(payload.get("speak"), allowed)
    stay_out = _id_list(payload.get("stay_out"), allowed)
    if speak is None or stay_out is None:
        return None
    if set(speak) & set(stay_out):
        return None
    return speak, stay_out


def finalize_router_lists(
    member_ids: list[str],
    *,
    forced_ids: list[str],
    speak: list[str],
    cap: int,
) -> tuple[list[str], list[str]]:
    """Pin required ids first, then fill ``speak`` up to ``cap``.

    Required ids are kept even when they exceed ``cap``. Everyone else in
    ``member_ids`` is stay_out, including ids the model omitted.
    """
    universe = _unique(member_ids)
    allowed = set(universe)
    limit = cap if cap >= 1 else 1
    forced: list[str] = []
    for agent_id in forced_ids:
        if agent_id in allowed and agent_id not in forced:
            forced.append(agent_id)
    forced_set = set(forced)
    rest: list[str] = []
    for agent_id in speak:
        if agent_id in allowed and agent_id not in forced_set and agent_id not in rest:
            rest.append(agent_id)
    if len(forced) >= limit:
        chosen = forced
    else:
        chosen = forced + rest[: limit - len(forced)]
    chosen_set = set(chosen)
    stay_out = [agent_id for agent_id in universe if agent_id not in chosen_set]
    return chosen, stay_out


def build_router_messages(
    *,
    members: list[dict[str, str]],
    latest_message: str,
    pending_mention_ids: list[str],
    sticky: str,
) -> list[dict[str, str]]:
    """Build the short route prompt. Specialties are hire roles, not bios."""
    by_id = {str(member.get("id") or ""): member for member in members}
    member_lines = []
    for member in members:
        agent_id = str(member.get("id") or "").strip()
        if not agent_id:
            continue
        name = str(member.get("name") or "").strip() or agent_id
        specialty = str(member.get("role") or "").strip() or "unspecified"
        member_lines.append(f"{agent_id} | {name} | {specialty}")
    pending_lines = []
    for agent_id in pending_mention_ids:
        member = by_id.get(agent_id) or {}
        name = str(member.get("name") or "").strip() or agent_id
        pending_lines.append(f"{agent_id} | {name}")
    sticky_text = (sticky or "").strip() or "(none)"
    user = "\n".join(
        [
            "Latest message:",
            _clip(latest_message, _LATEST_MESSAGE_CHARS) or "(none)",
            "",
            "Members:",
            "\n".join(member_lines) or "(none)",
            "",
            "Pending @:",
            "\n".join(pending_lines) or "(none)",
            "",
            "Sticky context:",
            _clip(sticky_text, _STICKY_CHARS),
        ]
    )
    system = (
        "You route one channel round. Reply with one JSON object and no other text. "
        'The only keys are "speak" and "stay_out". '
        "Each value is an array of member ids from the list below. "
        'Order in "speak" is the order they should talk. '
        f'Put at most {ROUTER_SPEAK_CAP} ids in "speak" after the pending @ ids. '
        "Pending @ ids are required in speak and must come first, even when that "
        f"makes speak longer than {ROUTER_SPEAK_CAP}. "
        "Do not add anyone else past that cap once pending @ ids are included. "
        'Every other member id must appear in "stay_out". '
        'An empty "speak" array is valid and ends the snapshot. '
        "Do not add keys or ids."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def short_sticky_context(
    members: list[dict[str, str]],
    *,
    opening_message: str = "",
) -> str:
    """A short sticky clip: optional opening line plus a few standing prefs.

    This is router input only. It is not the warm section injected on an
    agent turn, and it does not read note bodies.
    """
    parts: list[str] = []
    opening = " ".join((opening_message or "").split())
    if opening:
        parts.append("Opening: " + _clip(opening, _OPENING_CHARS))
    kept = 0
    for member in members:
        if kept >= _PREFS_IN_STICKY:
            break
        agent_id = str(member.get("id") or "").strip()
        if not agent_id:
            continue
        agent = db.get_agent(agent_id)
        if agent is None:
            continue
        prefs = read_standing_prefs(agent.storage_key)
        if not prefs:
            continue
        text = " ".join(prefs[0].text.split())
        if not text:
            continue
        name = str(member.get("name") or "").strip() or agent.name
        parts.append(f"{name}: {_clip(text, _PREF_CHARS)}")
        kept += 1
    return _clip("\n".join(parts), _STICKY_CHARS)


def _unwrap_json(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    return "\n".join(lines[1:-1]).strip()


def _id_list(value: object, allowed: set[str]) -> list[str] | None:
    if not isinstance(value, list):
        return None
    found: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            return None
        token = item.strip()
        if not token or token not in allowed or token in seen:
            return None
        seen.add(token)
        found.append(token)
    return found


def _unique(ids: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for agent_id in ids:
        token = (agent_id or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        found.append(token)
    return found


def _clip(text: str, limit: int) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    if limit <= 3:
        return clean[:limit]
    return clean[: limit - 3].rstrip() + "..."
