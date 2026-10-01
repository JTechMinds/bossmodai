"""BossMod AI — the runtime worker's in-memory timetable of schedule runs (pure).

A sorted list of ``(next_at, schedule_id)``, one entry per enabled
schedule, plus the rule each entry was computed from. The database stores
only the rule and the last outcome; when a schedule runs next is derived
here from the rule and the clock.

``sync`` is how the timetable follows the database: an entry whose
schedule is still enabled with the same rule keeps its ``next_at`` (so a run
already due stays due and the next pass fires it, and an edit elsewhere
never replays or skips it); a new, re-enabled or rule-changed schedule is
computed from ``now`` (so nothing behind the clock fires, and a first sync
after a start never replays what came due while stopped); a schedule that
is gone or disabled is dropped.

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
        # The rule each entry's next_at was computed from.
        self._rules: dict[str, RecurrenceRule] = {}

    def sync(self, entries: Iterable[tuple[str, RecurrenceRule]], *, now: datetime) -> None:
        """Make the timetable hold exactly ``entries`` (see the module docstring).

        Args:
            entries: ``(schedule_id, rule)`` for every enabled schedule; ids
                must be unique.
            now: An aware instant; new or changed rules run strictly after it.

        Raises:
            ValueError: A schedule id appears twice, or ``next_occurrence``
                refuses (a naive ``now``). The timetable is unchanged then.
        """
        kept = {schedule_id: next_at for next_at, schedule_id in self._entries}
        rebuilt: list[tuple[datetime, str]] = []
        rules: dict[str, RecurrenceRule] = {}
        for schedule_id, rule in entries:
            if schedule_id in rules:
                raise ValueError(f"schedule {schedule_id} is listed twice")
            rules[schedule_id] = rule
            if schedule_id in kept and self._rules.get(schedule_id) == rule:
                rebuilt.append((kept[schedule_id], schedule_id))
            else:
                rebuilt.append((next_occurrence(rule, after=now), schedule_id))
        rebuilt.sort()
        self._entries = rebuilt
        self._rules = rules

    def next_due(self) -> datetime | None:
        """The soonest ``next_at``, or ``None`` when nothing is loaded."""
        return self._entries[0][0] if self._entries else None

    def pop_due(self, now: datetime) -> list[tuple[str, datetime]]:
        """Remove and return every entry due at or before ``now``, soonest first.

        Returns:
            ``(schedule_id, due_at)`` pairs; several schedules due at the
            same instant are all returned. A popped id is forgotten until
            ``schedule`` or ``sync`` adds it again.
        """
        cut = 0
        while cut < len(self._entries) and self._entries[cut][0] <= now:
            cut += 1
        due = self._entries[:cut]
        self._entries = self._entries[cut:]
        for _, schedule_id in due:
            self._rules.pop(schedule_id, None)
        return [(schedule_id, due_at) for due_at, schedule_id in due]

    def schedule(self, schedule_id: str, rule: RecurrenceRule, *, after: datetime) -> None:
        """Insert ``schedule_id`` at its next run after ``after``, replacing any entry it had.

        Raises:
            ValueError: ``next_occurrence`` refuses (a naive ``after``).
        """
        next_at = next_occurrence(rule, after=after)
        self.drop(schedule_id)
        bisect.insort(self._entries, (next_at, schedule_id))
        self._rules[schedule_id] = rule

    def drop(self, schedule_id: str) -> None:
        """Remove ``schedule_id``'s entry; nothing happens when it has none."""
        self._entries = [entry for entry in self._entries if entry[1] != schedule_id]
        self._rules.pop(schedule_id, None)

    def __len__(self) -> int:
        return len(self._entries)
