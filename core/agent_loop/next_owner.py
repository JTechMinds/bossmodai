"""Thread mention and reply-shape helpers.

@-mention extraction for members and ``@everyone``/``@all``, the mention
candidates on a thread's floor, the multi-party test, and the shape checks
for locked status one-liners and pure reactions. Who is addressed and who
speaks next is the System AI router's call; nothing here asks an agent to
tag someone.
"""

from __future__ import annotations

import re
from typing import Iterable

import db
from core.boss import boss_mention_names

_EVERYONE = "everyone"
# Typed forms of the canonical everyone token. Checked after member names.
_EVERYONE_ALIASES = ("everyone", "all")

# Locked origin-thread one-liners ({Name} Created/Accepted/Writing/…).
_STATUS_VERB = (
    r"Created|Accepted|Writing|Waiting|Stalled|Declined|Rerouted|"
    r"Cancelled|Done|Blocked|Busy"
)
_SYSTEM_ONE_LINER = re.compile(
    rf"^(?:[A-Z][\w.-]*(?: [A-Z][\w.-]*)? )?(?:{_STATUS_VERB})\b"
)
_REACTION_WORDS = frozenset(
    {"ok", "okay", "thanks", "thank you", "thx", "+1", "lgtm", "ack", "noted"}
)
_EMOJI_OR_SYMBOL = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F900-\U0001F9FF"
    r"\U00002600-\U000026FF\u200d\ufe0f\u20e3]+"
)
_NAME_TRAIL = re.compile(r"[A-Za-z0-9._-]")


def is_multi_party_channel(channel_id: str | None) -> bool:
    """Return True when a channel is a multi-party thread (human + ≥2 agents)."""
    token = (channel_id or "").strip()
    if not token:
        return False
    return len(db.list_channel_member_details(token)) >= 2


def mention_names_for_channel(channel_id: str) -> list[str]:
    """Return @-mention candidates on this thread's floor, plus the boss.

    A member whose home floor is not the thread's floor is not a candidate.
    A thread with no floor names nobody but the boss. The boss's names
    (``core.boss.boss_mention_names``) are read per call, so a rename is
    live on the next turn.
    """
    from core.floors import channel_floor_id, on_floor

    floor_id = channel_floor_id(channel_id)
    names = []
    if floor_id:
        names = [
            str(member.get("name") or "").strip()
            for member in db.list_channel_member_details(channel_id)
            if on_floor(str(member.get("id") or ""), floor_id)
        ]
    names.extend(boss_mention_names())
    return [name for name in names if name]


def extract_next_owner_mentions(text: str, *, member_names: Iterable[str]) -> list[str]:
    """Return member/@everyone mentions found in text. Conservative: @ required.

    ``@everyone`` and ``@all`` both mean every member and are returned as the
    one canonical ``everyone`` token. Member names are matched first, so a
    member whose name starts with "All" still resolves to that member.
    """
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
        matched: str | None = None
        for name in names:
            if lowered.startswith(name.lower()) and _mention_boundary(rest, len(name)):
                matched = name
                break
        if matched is not None:
            found.append(matched)
            index += 1 + len(matched)
            continue
        alias = next(
            (
                token
                for token in _EVERYONE_ALIASES
                if lowered.startswith(token) and _mention_boundary(rest, len(token))
            ),
            None,
        )
        if alias is not None:
            found.append(_EVERYONE)
            index += 1 + len(alias)
        else:
            index += 1
    return found


def is_system_one_liner(text: str | None) -> bool:
    """Return True for {Name} Created/Accepted/Writing/… mirror lines."""
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


def _mention_boundary(rest: str, length: int) -> bool:
    """Return True when a mention ends at ``length`` rather than mid-token."""
    if length > len(rest):
        return False
    if length == len(rest):
        return True
    return _NAME_TRAIL.match(rest[length]) is None
