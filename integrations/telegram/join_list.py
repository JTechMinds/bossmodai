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


_snapshots: dict[int, tuple[str, ...]] = {}


def reset_channel_list_snapshots() -> None:
    """Drop every remembered list. Tests start from no join target."""
    _snapshots.clear()


def remember_channel_list(telegram_user_id: int, channel_ids: list[str]) -> None:
    """Store the channel ids in the order just shown to this user."""
    _snapshots[telegram_user_id] = tuple(channel_ids)


def resolve_join_ordinal(
    telegram_user_id: int,
    ordinal: int,
    current_channel_ids: list[str],
) -> tuple[JoinListStatus, str | None]:
    """Return the channel id for ``ordinal`` only when the list is unchanged.

    ``ordinal`` is 1-based and matches the number printed on that row.
    """
    snapshot = _snapshots.get(telegram_user_id)
    if snapshot is None:
        return JoinListStatus.MISSING, None
    if tuple(current_channel_ids) != snapshot:
        return JoinListStatus.STALE, None
    if ordinal < 1 or ordinal > len(snapshot):
        return JoinListStatus.UNKNOWN, None
    return JoinListStatus.OK, snapshot[ordinal - 1]
