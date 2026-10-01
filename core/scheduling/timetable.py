"""BossMod AI — the runtime worker's in-memory timetable of schedule runs (pure).

A sorted list of ``(next_at, schedule_id)``, one entry per enabled
schedule. The database stores only the rule and the last outcome; when a
schedule runs next is always derived here from the rule and the current
time, so an occurrence that came due while the app was closed or paused
is simply never in the list.

A sorted list with ``bisect.insort`` is the simplest correct structure at a
handful of schedules per agent; a heap would need lazy-deletion bookkeeping
for ``drop`` and replace, for no gain.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from datetime import datetime

from core.models.schedule import RecurrenceRule
from core.scheduling.recurrence import next_occurrence


class Timetable:
    """The next run of each loaded schedule, soonest first. Not thread-safe."""

    def __init__(self) -> None:
        self._entries: list[tuple[datetime, str]] = []

    def load(self, entries: Iterable[tuple[str, RecurrenceRule]], *, now: datetime) -> None:
        """Replace every entry with each schedule's next run strictly after ``now``.

        Args:
            entries: ``(schedule_id, rule)`` pairs; ids must be unique.
            now: An aware instant.

        Raises:
            ValueError: A schedule id appears twice, or ``next_occurrence``
                refuses (a naive ``now``).
        """
        rebuilt: list[tuple[datetime, str]] = []
        seen: set[str] = set()
        for schedule_id, rule in entries:
            if schedule_id in seen:
                raise ValueError(f"schedule {schedule_id} is listed twice")
            seen.add(schedule_id)
            rebuilt.append((next_occurrence(rule, after=now), schedule_id))
        rebuilt.sort()
        self._entries = rebuilt

    def next_due(self) -> datetime | None:
        """The soonest ``next_at``, or ``None`` when nothing is loaded."""
        return self._entries[0][0] if self._entries else None

    def pop_due(self, now: datetime) -> list[tuple[str, datetime]]:
        """Remove and return every entry due at or before ``now``, soonest first.

        Returns:
            ``(schedule_id, due_at)`` pairs; several schedules due at the
            same instant are all returned.
        """
        cut = 0
        while cut < len(self._entries) and self._entries[cut][0] <= now:
            cut += 1
        due = self._entries[:cut]
        self._entries = self._entries[cut:]
        return [(schedule_id, due_at) for due_at, schedule_id in due]

    def schedule(self, schedule_id: str, rule: RecurrenceRule, *, after: datetime) -> None:
        """Insert ``schedule_id`` at its next run after ``after``, replacing any entry it had.

        Raises:
            ValueError: ``next_occurrence`` refuses (a naive ``after``).
        """
        next_at = next_occurrence(rule, after=after)
        self.drop(schedule_id)
        bisect.insort(self._entries, (next_at, schedule_id))

    def drop(self, schedule_id: str) -> None:
        """Remove ``schedule_id``'s entry; nothing happens when it has none."""
        self._entries = [entry for entry in self._entries if entry[1] != schedule_id]

    def __len__(self) -> int:
        return len(self._entries)
