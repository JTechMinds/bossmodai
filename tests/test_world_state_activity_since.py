"""The world snapshot carries when the active turn began.

Phase 4's single approved engine touch (spec 12, carried items): one additive
SELECT column in `db.get_world_state()`. The presence row trades
"Jim is thinking..." for "Jim is working - 12m" once the turn passes a minute,
and this column is the only thing that can tell it which. Without the test the
column is one careless SELECT edit away from disappearing, taking the duration
with it and leaving the UI silently frozen on the older copy.
"""

from __future__ import annotations

import os
from pathlib import Path

import db
from core import config


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _row_for(agent_id: str) -> dict:
    rows = [row for row in db.get_world_state() if row["id"] == agent_id]
    assert len(rows) == 1, f"expected exactly one world row for {agent_id}"
    return rows[0]


def test_world_state_reports_when_the_active_turn_began() -> None:
    working = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    resting = db.create_agent("Bo", role="Ops", desk_x=3, desk_y=1)

    activity = db.create_runtime_activity(working.id, "work", status="active")

    busy = _row_for(working.id)
    assert busy["currentActivityKind"] == "work"
    # The column is the activity's own created_at, not a derived or defaulted
    # timestamp: a stand-in would make every turn look like it just started.
    assert busy["currentActivitySince"] is not None
    assert str(busy["currentActivitySince"]) == str(activity.created_at)

    # An agent with no active activity has no start time. `None` is the honest
    # answer and the one the presence row reads as "say 'is thinking...'".
    idle = _row_for(resting.id)
    assert idle["currentActivityKind"] is None
    assert idle["currentActivitySince"] is None

    # Ending the turn takes the start time with it — a finished activity must
    # not keep reporting a running duration.
    db.update_activity(activity.id, status="completed")
    assert _row_for(working.id)["currentActivitySince"] is None
