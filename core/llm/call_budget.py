"""One global budget for inflight model calls.

Agent turns, System AI routes, and repairs share
``max_concurrent_agent_turns`` (the operator-facing "Max concurrent model
calls" knob). The count lives in SQLite so the app process and the runtime
worker honor the same number.

A system route may hold at most one lane (``SYSTEM_LANE_RESERVE``). That
lane is taken from the knob. It is never an extra call above it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass

from core.agent_loop.channel_round_plan import max_concurrent_agent_turns
from core.runtime.services import request_dispatcher_wake
from db.connection import transaction

logger = logging.getLogger(__name__)

# At most one System AI route at a time. Counted inside the knob.
SYSTEM_LANE_RESERVE = 1


@dataclass(frozen=True, slots=True)
class Lane:
    """One acquired model-call lane."""

    id: str
    owner: str
    kind: str


_current_turn_lane: ContextVar[Lane | None] = ContextVar(
    "model_call_turn_lane",
    default=None,
)


def max_concurrent_model_calls() -> int:
    """Return the single operator knob."""
    return max_concurrent_agent_turns()


def format_queued_ahead(ahead: int) -> str:
    """Debra's locked no-lane line."""
    return f"Queued ({max(int(ahead), 0)} ahead)"


def local_capacity_warning(observed: int | None, *, limit: int | None = None) -> str | None:
    """Return a health warning when a local server allows fewer calls than the knob.

    Observed capacity is a report from the server. It is not a setting.
    """
    if observed is None:
        return None
    try:
        slots = int(observed)
    except (TypeError, ValueError):
        return None
    if slots < 1:
        return None
    knob = max_concurrent_model_calls() if limit is None else int(limit)
    if slots < knob:
        noun = "call" if slots == 1 else "calls"
        return (
            f"Local server allows {slots} parallel model {noun}, "
            f"below Max concurrent model calls ({knob})."
        )
    return None


def slots_from_payload(payload: object) -> int | None:
    """Read a llama.cpp-style slot list. Unknown shapes are not a capacity."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        slots = payload.get("slots")
        if isinstance(slots, list):
            return len(slots)
    return None


def current_turn_lane() -> Lane | None:
    """Return the lane the running turn already holds, if any."""
    return _current_turn_lane.get()


def bind_turn_lane(lane: Lane) -> Token[Lane | None]:
    """Remember that this turn already holds a lane. Repairs reuse it."""
    return _current_turn_lane.set(lane)


def reset_turn_lane(token: Token[Lane | None]) -> None:
    """Drop the turn's lane binding."""
    _current_turn_lane.reset(token)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ModelCallBudget:
    """Cross-process lane table plus in-process waiters."""

    def __init__(self) -> None:
        self._waiters: list[asyncio.Event] = []

    def inflight(self) -> int:
        """Return how many lanes are held right now."""
        from db.crud import query_one

        row = query_one("SELECT COUNT(*) AS cnt FROM model_call_lanes")
        if row is None:
            return 0
        return int(row["cnt"])

    def reset(self) -> None:
        """Drop every lane and wake local waiters. Tests use this."""
        try:
            from db.crud import execute

            execute("DELETE FROM model_call_lanes")
        except Exception:
            logger.debug("model-call budget reset skipped", exc_info=True)
        waiters = list(self._waiters)
        self._waiters.clear()
        for event in waiters:
            event.set()

    def try_acquire(self, *, kind: str, owner: str) -> Lane | None:
        """Take one lane when the knob allows it. None means the budget is full."""
        limit = max_concurrent_model_calls()
        lane_id = str(uuid.uuid4())
        pid = os.getpid()
        with transaction() as con:
            rows = con.execute("SELECT id, pid, kind FROM model_call_lanes").fetchall()
            dead = [row[0] for row in rows if not _pid_alive(int(row[1]))]
            if dead:
                con.execute(
                    f"DELETE FROM model_call_lanes WHERE id IN ({', '.join('?' for _ in dead)})",
                    dead,
                )
                rows = [row for row in rows if row[0] not in set(dead)]
            if len(rows) >= limit:
                return None
            if kind == "system":
                reserve = min(SYSTEM_LANE_RESERVE, limit)
                held = sum(1 for row in rows if row[2] == "system")
                if held >= reserve:
                    return None
            con.execute(
                "INSERT INTO model_call_lanes (id, kind, owner, pid) VALUES (?, ?, ?, ?)",
                [lane_id, kind, str(owner), pid],
            )
        return Lane(id=lane_id, owner=str(owner), kind=kind)

    def release(self, lane: Lane | None) -> None:
        """Return a lane to the budget and wake one local waiter."""
        if lane is None:
            return
        from db.crud import execute

        execute("DELETE FROM model_call_lanes WHERE id = $1", [lane.id])
        if self._waiters:
            self._waiters.pop(0).set()
        else:
            request_dispatcher_wake()

    async def acquire(self, *, kind: str, owner: str) -> Lane:
        """Wait until a lane is free, then take it.

        A release in this process wakes the waiter. A release in the other
        process is noticed on a short poll, matching the worker command loop.
        """
        while True:
            lane = self.try_acquire(kind=kind, owner=owner)
            if lane is not None:
                return lane
            event = asyncio.Event()
            self._waiters.append(event)
            try:
                await asyncio.wait_for(event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
            finally:
                if event in self._waiters:
                    self._waiters.remove(event)


budget = ModelCallBudget()
