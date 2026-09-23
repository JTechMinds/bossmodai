"""Ordinal join targets for one Telegram user's last channel list.

``/channels`` stamps rows 1..N and remembers that id sequence. ``/join N``
rejoins that row only when the live channel ids are still the same sequence.
A missing list, an unknown row, or any change in the sequence is stale:
re-list, and never substitute another channel.
"""

from __future__ import annotations

from enum import Enum


class JoinListStatus(str, Enum):
    """Why a ``/join`` ordinal did or did not resolve."""

    OK = "ok"
    MISSING = "missing"
    STALE = "stale"
    UNKNOWN = "unknown"


# user -> (floor id, channel ids in the order just printed)
_snapshots: dict[int, tuple[str, tuple[str, ...]]] = {}


def reset_channel_list_snapshots() -> None:
    """Drop every remembered list. Tests start from no join target."""
    _snapshots.clear()


def remember_channel_list(
    telegram_user_id: int,
    channel_ids: list[str],
    *,
    floor_id: str,
) -> None:
    """Store one floor's channel ids in the order just shown to this user."""
    _snapshots[telegram_user_id] = (floor_id, tuple(channel_ids))


def remembered_floor_id(telegram_user_id: int) -> str | None:
    """The floor the last list was scoped to, if a list was shown."""
    snapshot = _snapshots.get(telegram_user_id)
    if snapshot is None:
        return None
    return snapshot[0]


def resolve_join_ordinal(
    telegram_user_id: int,
    ordinal: int,
    current_channel_ids: list[str],
    *,
    floor_id: str,
) -> tuple[JoinListStatus, str | None]:
    """Return the channel id for ``ordinal`` only when that floor's list is unchanged.

    ``ordinal`` is 1-based and matches the number printed on that row.
    A different floor than the one just listed is stale: the number must
    not reach a thread the operator was not shown.
    """
    snapshot = _snapshots.get(telegram_user_id)
    if snapshot is None:
        return JoinListStatus.MISSING, None
    remembered_floor, channel_ids = snapshot
    if remembered_floor != floor_id or tuple(current_channel_ids) != channel_ids:
        return JoinListStatus.STALE, None
    if ordinal < 1 or ordinal > len(channel_ids):
        return JoinListStatus.UNKNOWN, None
    return JoinListStatus.OK, channel_ids[ordinal - 1]
