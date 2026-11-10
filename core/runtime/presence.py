"""BossMod AI — the app process's view of who is mid-turn.

The runtime worker's dispatcher is the only producer of presence. It emits one
``agent_presence`` delta when an agent is queued for a model-call lane, when
its turn starts thinking, and when that turn ends (``idle``). A browser that
was not connected, or not mounted, when a delta was sent would never learn of
it, so the app process applies every delta here and sends the result as a
``presence_snapshot`` frame on each WebSocket connect (``api/routes/ws.py``).

Worker stdout reaches the app as one ordered, lossless pipe, so applying the
deltas in arrival order keeps this view exact. It is pure state with no I/O
and knows nothing of the worker's lifecycle: ``RuntimeServices`` clears it
when the worker exits, because the turns it describes die with the worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The phases a stored row can hold. ``idle`` is the removal, never a row.
_ROW_PHASES = frozenset({"thinking", "queued"})
_IDLE = "idle"


class PresenceRegistry:
    """Who is thinking or queued, keyed by agent and channel.

    The key is ``(agent_id, channel_id or None)``: the same key the
    dispatcher's own ``_painted_queued`` uses. A queued row that becomes a
    thinking row is therefore one key replacing itself, and one agent can hold
    rows in two threads at once.

    Not thread-safe; it is only touched from the app's event loop.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str | None], dict[str, Any]] = {}

    def apply(self, data: Mapping[str, Any]) -> None:
        """Apply one ``agent_presence`` payload.

        ``thinking`` or ``queued`` stores the row
        ``{agent_id, agent_name, channel_id, phase, ahead, since}`` under its
        key, overwriting any row there. ``idle`` removes the key; removing an
        absent key is a no-op, because idle is idempotent by contract (the
        dispatcher may retract a row its turn's own ``idle`` already cleared).

        The browser runs the same checks on every row it keeps
        (``ui/static/js/core/presence.js``), so a payload this refuses is one
        the client would refuse too, and the two can never disagree.

        Args:
            data: One payload: ``agent_id`` (non-empty str), ``agent_name``
                (non-empty str on a stored row), ``phase`` (``"thinking"``,
                ``"queued"`` or ``"idle"``), ``channel_id`` (str or None;
                absent or empty means None), ``ahead`` (how many turns wait
                ahead of a queued row: a whole number, required when queued)
                and ``since`` (ISO-8601 start of a thinking turn, or None).

        Raises:
            ValueError: Naming the field: ``agent_id`` is missing or empty,
                ``phase`` is not one of the three, ``agent_name`` is missing
                or empty on a stored row, or a queued row has no whole,
                non-negative ``ahead``. The registry is left unchanged.
        """
        agent_id = data.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError(f"presence agent_id must be a non-empty string, got {agent_id!r}")
        phase = data.get("phase")
        if phase != _IDLE and phase not in _ROW_PHASES:
            raise ValueError(f"presence phase must be thinking, queued or idle, got {phase!r}")
        key = (agent_id, data.get("channel_id") or None)
        if phase == _IDLE:
            self._rows.pop(key, None)
            return
        agent_name = data.get("agent_name")
        if not isinstance(agent_name, str) or not agent_name:
            raise ValueError(f"presence agent_name must be a non-empty string, got {agent_name!r}")
        ahead = data.get("ahead")
        if phase == "queued" and (not isinstance(ahead, int) or isinstance(ahead, bool) or ahead < 0):
            raise ValueError(f"a queued presence row needs a whole, non-negative ahead, got {ahead!r}")
        self._rows[key] = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "channel_id": key[1],
            "phase": phase,
            "ahead": ahead,
            "since": data.get("since"),
        }

    def clear(self) -> None:
        """Forget every row: the worker exited, and every turn with it."""
        self._rows.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a shallow copy of every row, oldest key first.

        Returns:
            One dict per row, safe for the caller to mutate or serialize.
        """
        return [dict(row) for row in self._rows.values()]
