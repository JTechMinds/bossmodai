"""Virtual Desktop — the power-down reminder: idle stretches per agent, in memory.

A stretch starts at the agent's last ``vm`` command (or a desktop start).
Once it has lasted the agent's reminder minutes, ``due`` hands the host's
wake service one batch; the agent is reminded once per stretch and decides
itself whether to power down. Nothing here stops a desktop.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from core.extensions.contract import WakeBatch

REMINDER_LINE = (
    "It is still running and using memory. If you are done with it for now, "
    "save anything open and run vm stop; your files and programs are kept."
)


class IdleTracker:
    """Thread-safe idle stretches per agent.

    Args:
        clock: Monotonic seconds (injectable for tests).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._last_activity: dict[str, float] = {}
        # The stretch (its start stamp) the agent was last reminded about.
        self._reminded_stretch: dict[str, float] = {}

    def touch(self, agent_id: str) -> None:
        """Start a new idle stretch now (a ``vm`` command ran or the desktop started)."""
        with self._lock:
            self._last_activity[agent_id] = self._clock()

    def idle_minutes(self, agent_id: str) -> int | None:
        """Whole minutes since the stretch started, or ``None`` when there is none."""
        with self._lock:
            last = self._last_activity.get(agent_id)
            return None if last is None else int((self._clock() - last) // 60)

    def due(self, agent_id: str, minutes: int) -> WakeBatch | None:
        """Return the reminder batch when this stretch has reached ``minutes`` and is unreminded.

        Args:
            agent_id: The agent.
            minutes: The agent's reminder minutes; 0 means never.

        Returns:
            The batch (``cursor`` is the stretch's start stamp), or ``None``.
        """
        with self._lock:
            last = self._last_activity.get(agent_id)
            if minutes == 0 or last is None or self._reminded_stretch.get(agent_id) == last:
                return None
            idle_s = self._clock() - last
            if idle_s < minutes * 60:
                return None
        return WakeBatch(
            agent_id=agent_id,
            title=f"Your desktop has had no activity for {format_minutes(int(idle_s // 60))}.",
            lines=[REMINDER_LINE],
            cursor=repr(last),
        )

    def mark_reminded(self, agent_id: str, cursor: str) -> None:
        """Record that the stretch starting at ``cursor`` was reminded.

        Raises:
            ValueError: ``cursor`` is not one ``due`` produced.
        """
        stretch = float(cursor)
        with self._lock:
            self._reminded_stretch[agent_id] = stretch

    def skip(self, agent_id: str) -> None:
        """Count the current stretch as reminded (the agent is on vacation)."""
        with self._lock:
            last = self._last_activity.get(agent_id)
            if last is not None:
                self._reminded_stretch[agent_id] = last

    def forget(self, agent_id: str) -> None:
        """Drop the agent's stretch (its desktop stopped, was reset or removed)."""
        with self._lock:
            self._last_activity.pop(agent_id, None)
            self._reminded_stretch.pop(agent_id, None)


def format_minutes(minutes: int) -> str:
    """``45 minutes``, ``1 hour``, ``2 hours``, ``2 hours 15 minutes``."""
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if rest or not hours:
        parts.append(f"{rest} minute{'s' if rest != 1 else ''}")
    return " ".join(parts)
