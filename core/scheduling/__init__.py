"""BossMod AI — per-agent recurring schedules.

The pure half is re-exported here: the recurrence math and the worker's
timetable. The I/O half is imported from its own module so importing this
package never pulls in the database or the dispatcher:

- ``core.scheduling.service``: the app-side create/update/delete and the
  API view (``to_view``).
- ``core.scheduling.runner``: one occurrence, synchronously, against the DB.
- ``core.scheduling.watch``: the runtime worker's clock loop
  (``schedule_watch``).
"""

from core.scheduling.recurrence import describe, matches_day, next_occurrence
from core.scheduling.timetable import Timetable

__all__ = ["Timetable", "describe", "matches_day", "next_occurrence"]
