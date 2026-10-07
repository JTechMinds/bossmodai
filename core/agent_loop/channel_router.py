"""System AI route for one channel response round.

One short completion on ``system_ai_connection`` sees the latest message
and its author, the last N thread lines, member specialties and role
summaries, pending @ members, and a short sticky clip. Members
are numbered ``1…N`` for that one call; no agent id appears in the
prompt. The only accepted payload is:

    {"speak": [2, 5]}

``speak`` is ordered and lists member numbers. Any other key, an unknown
number, a duplicate, a non-integer, or a non-list rejects the payload.
Stay-out is not asked of the model: it is every member left out of
``speak``. On a rejected payload, or no completion at all, the caller
uses the existing drain order and each member gets a normal soft-judge
turn.

Required ids stay first; the model's speak list is kept whole. Operator
@ ids, and any other ids the caller marks as required (structured
``next_owners``, Board next-card owners), lead ``speak``, followed by
every member the model named, in the model's order. Ids the model omits
are stay_out on that decision. Stay-out is not a permanent skip: a plan
the model ordered as 1…N still runs one at a time, and someone left out
of this slice can be named when the route runs again. Stay-out with
nothing left to do is an engine pass: no identity-model turn.

A Done/handoff route, and an agent-line route, whose parsed ``speak`` is
empty and which has no required pin, gets one repair completion ("who
speaks next?") and then stops. It does not fall back to drain order and
it does not invent an @. An agent line that is settled status, an echo,
or a no-op is that empty speak: every member stays out. A peer @ on that
line is not a pin. Operator @ ids stay first.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import db
from core.agent_loop.standing_prefs import read_memories
from core.llm.system_completion import complete_text, system_ai_is_configured

logger = logging.getLogger(__name__)

ROUTER_KEYS = frozenset({"speak"})

_LATEST_MESSAGE_CHARS = 800
_STICKY_CHARS = 400
_PREF_CHARS = 80
_PREFS_IN_STICKY = 3
# First description paragraph on the member roster. Not a prompt or note body.
_ROLE_SUMMARY_CHARS = 320
# One prior thread line in Recent thread.
_TRANSCRIPT_LINE_CHARS = 280

# Intent gate for a peer round opened from an agent speak. Not a phrase list.
AGENT_LINE_ROUTE = (
    "The latest message is an agent speak. Judge that line. Recent thread is context for it, not a new request. "
    "Put an id in speak only when the line adds new work, a question, or a handoff (naming who is next, even without @). "
    "Settled status, an echo of a line the thread already shows, or a no-op is an empty speak array. "
    "A peer @ on that line is not a pending pin and does not open another round."
)

# Re-route / later slice. Engine facts are Already spoke and Work-bound.
# Echo versus new substance is the guess. Unsure stays out.
REROUTE_ECHO_ROUTE = (
    "Already spoke lists members who already took a turn on this message from the boss. "
    "Work-bound lists members on live work. "
    "Being work-bound never excludes a member: wake them when the latest line addresses them; "
    "their work resumes after they answer. "
    "Already spoke never excludes a member the latest message addresses: if it hands them work, "
    "answers their question, or gives them the go-ahead they asked for, wake them even though they "
    "spoke before. "
    "For members the latest message does not address, do not wake an already-spoke member to restate "
    "what the thread already shows; if unsure whether they would add new substance, leave them out. "
    "An empty speak array is the stop when nobody is addressed and nobody has new substance."
)


@dataclass(frozen=True, slots=True)
class RouterLine:
    """One prior thread line for the router's Recent thread section.

    ``status`` marks a system task card (Accepted, Writing, Done, Blocked)
    so the model reads it as state, not speech. ``author_agent_id`` is the
    row's agent author, or empty; it is engine data and is never printed.
    """

    author: str
    text: str
    status: bool
    author_agent_id: str


@dataclass(frozen=True, slots=True)
class RoundPlan:
    """Who speaks, who is an engine pass, and whether the router produced this.

    ``named_speak`` is the speak list the model returned, before required
    @ ids are pinned. An empty list on a system plan is a hard stop: callers
    must not put those pins back.
    """

    speak: list[str]
    stay_out: list[str]
    mode: str
    pinned: list[str]
    named_speak: tuple[str, ...] = ()


def plan_channel_route(
    *,
    members: list[dict[str, str]],
    fallback_order: list[str],
    latest_message: str,
    latest_author: str,
    transcript: list[RouterLine],
    pending_mention_ids: list[str],
    forced_ids: list[str],
    handoff: bool = False,
    agent_line: bool = False,
    sticky_note: str = "",
    repair_empty: bool = True,
    already_spoke_ids: list[str] | None = None,
    work_bind_ids: list[str] | None = None,
) -> RoundPlan:
    """Return a system plan, or the drain order when System AI cannot route.

    ``fallback_order`` is the #124 member order and the only legal id set.
    ``forced_ids`` and ``pending_mention_ids`` are required. They stay at
    the front of ``speak`` in that order and are stored on ``pinned``.
    Operator @ ids and other hard pins are passed there so the router
    cannot drop them.

    The model answers with per-call member numbers, mapped back to ids
    here. ``stay_out`` on the plan is the complement of the speak list. No completion, or a rejected payload, logs a warning and
    returns the drain order.

    ``already_spoke_ids`` and ``work_bind_ids`` are engine facts for a
    re-route or later slice; they reach the prompt as member numbers only.

    ``latest_author`` labels the latest message and ``transcript`` is the
    prior thread lines, oldest first. Both are required so a caller cannot
    route without the conversation.

    ``handoff`` is a Done/handoff round. ``agent_line`` is a peer round
    opened from an agent speak. An empty parsed speak with no pin gets
    one repair on either path when ``repair_empty`` is set. A still-empty
    speak is a system hard stop, not the drain order. Operator pins stay
    in speak. A peer @ does not. A mid-round re-route passes
    ``repair_empty`` false so an empty slice does not spend the repair.
    """
    from core.floors import keep_one_floor

    members = keep_one_floor(members)
    allowed_ids = {str(member.get("id") or "") for member in members}
    fallback_order = [agent_id for agent_id in fallback_order if agent_id in allowed_ids]
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
    try:
        sticky = short_sticky_context(roster)
    except Exception:
        logger.warning("channel router skipped sticky context")
        sticky = ""
    note = (sticky_note or "").strip()
    if note:
        sticky = _clip("\n".join(part for part in (sticky, note) if part), _STICKY_CHARS)
    messages, number_map = build_router_messages(
        members=roster,
        latest_message=latest_message,
        latest_author=latest_author,
        transcript=transcript,
        pending_mention_ids=pinned,
        sticky=sticky,
        agent_line=agent_line,
        already_spoke_ids=already_spoke_ids,
        work_bind_ids=work_bind_ids,
    )
    raw = complete_text(messages)
    if raw is None:
        logger.warning("channel router fell back to drain order: %s", "no_completion")
        return fallback
    parsed = parse_router_payload(raw, number_map)
    if parsed is None:
        logger.warning("channel router fell back to drain order: %s", "rejected")
        return fallback
    if repair_empty and (handoff or agent_line) and not parsed and not pinned:
        parsed = _repair_empty_speak(messages, number_map) or parsed
    named = tuple(parsed)
    speak, stay_out = finalize_router_lists(
        universe,
        forced_ids=pinned,
        speak=parsed,
    )
    kept_pins = [agent_id for agent_id in pinned if agent_id in set(speak)]
    return RoundPlan(
        speak=speak,
        stay_out=stay_out,
        mode="system",
        pinned=kept_pins,
        named_speak=named,
    )


def parse_router_payload(raw: str, number_map: dict[int, str]) -> list[str] | None:
    """Parse a fail-closed ``{"speak": [numbers]}`` object into ordered agent ids.

    Args:
        raw: Completion text. One code fence around the JSON is unwrapped.
        number_map: The per-call member number to agent id map returned by
            :func:`build_router_messages` for the same prompt.

    Returns:
        The speak ids in the model's order, or ``None`` when the payload is
        rejected: not JSON, not an object, keys other than ``speak``, a
        non-list, a non-integer item (``bool`` included), an unknown number,
        or a duplicate.
    """
    try:
        payload = json.loads(unwrap_json(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or set(payload) != ROUTER_KEYS:
        return None
    return _number_list(payload.get("speak"), number_map)


def finalize_router_lists(
    member_ids: list[str],
    *,
    forced_ids: list[str],
    speak: list[str],
) -> tuple[list[str], list[str]]:
    """Pin required ids first, then every other id the model named, in its order.

    Ids outside ``member_ids`` and duplicates are dropped. Everyone else in
    ``member_ids`` is stay_out, including ids the model omitted.

    Returns:
        ``(speak, stay_out)``.
    """
    universe = _unique(member_ids)
    allowed = set(universe)
    forced: list[str] = []
    for agent_id in forced_ids:
        if agent_id in allowed and agent_id not in forced:
            forced.append(agent_id)
    forced_set = set(forced)
    rest: list[str] = []
    for agent_id in speak:
        if agent_id in allowed and agent_id not in forced_set and agent_id not in rest:
            rest.append(agent_id)
    chosen = forced + rest
    chosen_set = set(chosen)
    stay_out = [agent_id for agent_id in universe if agent_id not in chosen_set]
    return chosen, stay_out


def _repair_empty_speak(
    messages: list[dict[str, str]],
    number_map: dict[int, str],
) -> list[str] | None:
    """One "who speaks next?" repair. None keeps the empty speak (hard stop).

    Used for a Done/handoff round and for an agent-line round. A bad
    repair payload does not fall through to drain order. Only a parsed
    non-empty speak replaces the empty list.
    """
    repair = [dict(item) for item in messages]
    if repair:
        repair[-1] = {
            "role": repair[-1].get("role") or "user",
            "content": (repair[-1].get("content") or "") + "\n\nWho speaks next?",
        }
    raw = complete_text(repair)
    if raw is None:
        return None
    parsed = parse_router_payload(raw, number_map)
    if not parsed:
        return None
    return parsed


def role_summary(text: str | None) -> str:
    """Return the first paragraph of a hire description, flattened and clipped.

    The paragraph ends at the first blank line, so standing-note bodies
    and prompt text after it stay off the roster. Multi-line bullets in
    that paragraph (``Specialty:``, ``Description:``, ``Mission:``) are
    kept. Empty input stays empty so the member row keeps
    ``number | name | specialty``.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    lines: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            break
        lines.append(line)
    return _clip(" ".join(" ".join(lines).split()), _ROLE_SUMMARY_CHARS)


def format_transcript(lines: list[RouterLine]) -> str:
    """Render prior thread lines oldest first, one per line.

    Speech is ``Name: text``. A status card is ``[status] text``. Each
    text is flattened and clipped. An empty list renders ``(none)``.
    """
    rendered: list[str] = []
    for line in lines:
        text = _clip(" ".join((line.text or "").split()), _TRANSCRIPT_LINE_CHARS)
        if line.status:
            rendered.append(f"[status] {text}")
        else:
            rendered.append(f"{line.author}: {text}")
    return "\n".join(rendered) or "(none)"


def format_member_line(member: dict[str, str], number: int) -> str:
    """``number | name | specialty``, plus `` — summary`` when a description exists.

    ``number`` is the member's per-call router number. The agent id is
    never printed; a member with no name shows as ``Member <number>``.
    """
    name = str(member.get("name") or "").strip() or f"Member {number}"
    specialty = str(member.get("role") or "").strip() or "unspecified"
    line = f"{number} | {name} | {specialty}"
    summary = role_summary(member.get("description") or member.get("blurb"))
    if summary:
        return f"{line} — {summary}"
    return line


def build_router_messages(
    *,
    members: list[dict[str, str]],
    latest_message: str,
    latest_author: str,
    transcript: list[RouterLine],
    pending_mention_ids: list[str],
    sticky: str,
    agent_line: bool = False,
    already_spoke_ids: list[str] | None = None,
    work_bind_ids: list[str] | None = None,
) -> tuple[list[dict[str, str]], dict[int, str]]:
    """Build the short route prompt and its per-call member number map.

    Members are numbered ``1…N`` in roster order. The same numbers are used
    under Pending @, Already spoke and Work-bound, so the model only ever
    sees numbers, never agent ids. An id in those sections that is not a
    member is left out. Specialties and the first description paragraph,
    not full prompts. Recent thread comes first, then the latest message
    under its author label. Already spoke and Work-bound are appended only
    when either is non-empty. No message or round id is printed: the model
    cannot act on either.

    Returns:
        ``(messages, number_map)``. ``number_map`` maps each number to its
        agent id and is only valid for this one completion; pass it to
        :func:`parse_router_payload`.
    """
    number_map: dict[int, str] = {}
    number_by_id: dict[str, int] = {}
    by_id: dict[str, dict[str, str]] = {}
    member_lines = []
    for member in members:
        agent_id = str(member.get("id") or "").strip()
        if not agent_id or agent_id in number_by_id:
            continue
        number = len(number_map) + 1
        number_map[number] = agent_id
        number_by_id[agent_id] = number
        by_id[agent_id] = member
        member_lines.append(format_member_line(member, number))
    spoke = _unique(list(already_spoke_ids or []))
    bound = _unique(list(work_bind_ids or []))
    pending_lines = _fact_lines(_unique(list(pending_mention_ids)), number_by_id, by_id)
    sticky_text = (sticky or "").strip() or "(none)"
    author = (latest_author or "").strip() or "(unknown)"
    user = "\n".join(
        [
            "Recent thread (oldest first):",
            format_transcript(transcript),
            "",
            f"Latest message from {author}:",
            _clip(latest_message, _LATEST_MESSAGE_CHARS) or "(none)",
            "",
            "Members:",
            "\n".join(member_lines) or "(none)",
            "",
            "Pending @:",
            pending_lines,
            "",
            "Sticky context:",
            _clip(sticky_text, _STICKY_CHARS),
        ]
    )
    system = (
        "You choose who should wake for this channel round from intent, not wording. "
        "Read the latest message and sticky context: who is being handed work, who "
        "must answer, who is only being discussed. Prefer Board/task next owners when "
        "the sticky implies them. "
        "A member is addressed when the line hands them work, asks them something, or names them "
        "as next ('Next up: Brian…', 'Brian, can you…', 'waiting on Brian'): wake them. "
        "A member who is only referred to ('per Brian's spec', 'Brian said') is not addressed. "
        "An ask for one volunteer ('can someone…', 'anyone…') addresses the member whose role fits best, or two when it spans two roles. "
        "An ask to every member ('each of you', 'everyone', 'all of you', '@all', 'team, each…') addresses every member: name all of them. "
        "When the ask is to help or review a member, the helpers are addressed; wake the member being helped too only when the line also asks them to act. "
        "Use Recent thread to resolve pronouns and 'me' / 'you' from the Latest message author. "
        "Status lines show who is already working. "
        "When one handoff needs two related members in sequence (e.g. an author then a reviewer), "
        "name both in that order. "
        "Use the role summary to match the work to who owns it. "
        "Members the boss @-mentioned (pending) are already required — keep them first. "
        "Reply with only one JSON object and no other text. "
        'The only key is "speak". '
        "Its value is an array of member numbers (integers) from the list below, "
        'for example {"speak": [2, 1]}. '
        "Leave everyone who should not talk out of speak. "
        'Order in "speak" is the order they should talk. '
        "speak is ordered. Name everyone who should talk now and no one else. "
        "Pending @ members are required in speak and must come first. "
        "Someone left out of this slice is not finished: a later route can name them if they still need to act. "
        "An empty speak array ends the snapshot only when no one needs to act next. "
        "Do not add keys or numbers that are not in Members."
    )
    if agent_line:
        system = f"{system} {AGENT_LINE_ROUTE}"
    if spoke or bound:
        system = f"{system} {REROUTE_ECHO_ROUTE}"
        user = "\n".join(
            [
                user,
                "",
                "Already spoke:",
                _fact_lines(spoke, number_by_id, by_id),
                "",
                "Work-bound:",
                _fact_lines(bound, number_by_id, by_id),
            ]
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return messages, number_map


def _fact_lines(
    agent_ids: list[str],
    number_by_id: dict[str, int],
    by_id: dict[str, dict[str, str]],
) -> str:
    """``number | name`` lines for engine facts. Empty stays ``(none)``.

    An id that is not on the Members roster has no number and is left out,
    so the model cannot copy it into ``speak``.
    """
    lines: list[str] = []
    for agent_id in agent_ids:
        number = number_by_id.get(agent_id)
        if number is None:
            continue
        name = str((by_id.get(agent_id) or {}).get("name") or "").strip()
        lines.append(f"{number} | {name}" if name else str(number))
    return "\n".join(lines) or "(none)"


def short_sticky_context(members: list[dict[str, str]]) -> str:
    """A short sticky clip: the first memory of a few members.

    This is router input only. It is not the warm section injected on an
    agent turn, and it does not read note bodies.
    """
    parts: list[str] = []
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
        prefs = read_memories(agent.storage_key)
        if not prefs:
            continue
        text = " ".join(prefs[0].text.split())
        if not text:
            continue
        name = str(member.get("name") or "").strip() or agent.name
        parts.append(f"{name}: {_clip(text, _PREF_CHARS)}")
        kept += 1
    return _clip("\n".join(parts), _STICKY_CHARS)


def unwrap_json(raw: str) -> str:
    """Strip one code fence around a JSON completion. Unfenced text is returned trimmed."""
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    return "\n".join(lines[1:-1]).strip()


def _number_list(value: object, number_map: dict[int, str]) -> list[str] | None:
    if not isinstance(value, list):
        return None
    found: list[str] = []
    seen: set[int] = set()
    for item in value:
        # bool is an int subclass; true/false is not a member number.
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        if item not in number_map or item in seen:
            return None
        seen.add(item)
        found.append(number_map[item])
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
