"""Phase 4 (refresh efficiency): hot-path indexes and history retention.

Index use is checked on the SQL the real helpers send: every statement is
recorded at ``SQLiteCompatConnection.execute`` (the one path all statements
take) and the matching one is run again under ``EXPLAIN QUERY PLAN``. So a
helper whose query drifts away from its index fails here, not in production.

Retention: finished triggers, activity-log rows and diagnostics older than
their settings are pruned by the task watchdog at most once an hour; queued
or leased triggers and triggers another table still names are never pruned.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

import db
from core import config
from core.config import ConfigError
from core.tasking.resolution import OPEN_TASK_STATUSES
from db.connection import (
    _HOT_PATH_INDEXES,
    SQLiteCompatConnection,
    _apply_migrations,
    _normalize_statement,
    get_connection,
)
from db.crud import execute, query, query_one

_SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


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


# ---------------------------------------------------------------------------
# EXPLAIN QUERY PLAN on the statements the helpers really send
# ---------------------------------------------------------------------------

def _plan_of(call: Callable[[], Any], needle: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run ``call``, then EXPLAIN the first statement it sent containing ``needle``."""
    recorded: list[tuple[str, Any]] = []
    original = SQLiteCompatConnection.execute

    def recording(self: SQLiteCompatConnection, sql: str, params: Any = None) -> Any:
        recorded.append((sql, params))
        return original(self, sql, params)

    monkeypatch.setattr(SQLiteCompatConnection, "execute", recording)
    call()
    monkeypatch.setattr(SQLiteCompatConnection, "execute", original)
    matches = [(sql, params) for sql, params in recorded if needle in sql]
    assert matches, f"no statement containing {needle!r}: {[sql[:60] for sql, _ in recorded]}"
    sql, params = matches[0]
    explained_sql, explained_params = _normalize_statement("EXPLAIN QUERY PLAN " + sql, params)
    rows = get_connection().execute(explained_sql, explained_params or []).fetchall()
    return [str(row[3]) for row in rows]


def _uses(plan: list[str], table: str, index: str) -> bool:
    """True when ``table`` is read through ``index`` (SEARCH, or an ordered SCAN)."""
    return any(
        re.match(rf"(SEARCH|SCAN) {table}\b.*\bINDEX {index}\b", line) for line in plan
    )


def test_world_state_reads_active_activities_through_the_index(monkeypatch: pytest.MonkeyPatch) -> None:
    import db.world as world

    plan = _plan_of(world.get_world_state, "LEFT JOIN activities act", monkeypatch)
    assert _uses(plan, "act", "idx_activities_agent_status"), plan


def test_batched_active_activities_use_the_index(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan_of(lambda: db.get_active_activities(["a1", "a2"]), "FROM activities", monkeypatch)
    assert _uses(plan, "activities", "idx_activities_agent_status"), plan
    assert "SCAN activities" not in plan, plan


def test_channel_latest_message_reads_use_the_index(monkeypatch: pytest.MonkeyPatch) -> None:
    import db.channels as channels

    one = _plan_of(lambda: channels.get_latest_channel_message("c1"), "FROM channel_messages", monkeypatch)
    many = _plan_of(
        lambda: channels.get_latest_channel_messages(["c1", "c2"]), "PARTITION BY channel_id", monkeypatch,
    )
    rail = _plan_of(channels.list_channels, "GROUP BY channel_id", monkeypatch)
    for plan in (one, many, rail):
        assert _uses(plan, "channel_messages", "idx_channel_messages_channel_created"), plan
    # The rail's newest-per-channel is read off the index alone, no sort for the GROUP BY.
    assert "USE TEMP B-TREE FOR GROUP BY" not in rail, rail


@pytest.mark.parametrize(
    ("filters", "index"),
    [
        ({}, "idx_tasks_status"),
        ({"assigned_to": "agent-1"}, "idx_tasks_assigned_status"),
        ({"owner_id": "agent-1"}, "idx_tasks_owner_status"),
    ],
)
def test_board_and_watchdog_status_queries_use_the_index(
    monkeypatch: pytest.MonkeyPatch, filters: dict[str, str], index: str,
) -> None:
    plan = _plan_of(lambda: db.list_tasks_by_statuses(OPEN_TASK_STATUSES, **filters), "FROM tasks t", monkeypatch)
    assert _uses(plan, "t", index), plan


def test_newest_task_events_use_the_index(monkeypatch: pytest.MonkeyPatch) -> None:
    import db.task_events as task_events

    plan = _plan_of(
        lambda: task_events.list_recent_task_events(["t1", "t2"], limit_per_task=1), "FROM task_events", monkeypatch,
    )
    assert _uses(plan, "task_events", "idx_task_events_task_created"), plan


def test_dispatcher_queued_scan_uses_the_index(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan_of(db.list_queued_triggers, "FROM agent_triggers", monkeypatch)
    assert _uses(plan, "agent_triggers", "idx_agent_triggers_status_created"), plan
    assert "SCAN agent_triggers" not in plan, plan


def test_feed_branches_read_newest_first_through_their_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    from db.unified_feed import get_unified_feed

    plan = _plan_of(get_unified_feed, "UNION ALL", monkeypatch)
    assert _uses(plan, "activity_log", "idx_activity_log_created"), plan
    assert _uses(plan, "n", "idx_notifications_created"), plan
    recent = _plan_of(db.get_recent_activity_log_entries, "FROM activity_log", monkeypatch)
    assert recent == ["SCAN activity_log USING INDEX idx_activity_log_created"], recent


def test_prunes_find_their_rows_through_the_index(monkeypatch: pytest.MonkeyPatch) -> None:
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    triggers = _plan_of(lambda: db.prune_finished_triggers(cutoff), "DELETE FROM agent_triggers", monkeypatch)
    assert any(
        line.startswith("SEARCH agent_triggers USING INDEX idx_agent_triggers_status_created (status=? AND created_at<?)")
        for line in triggers
    ), triggers
    log = _plan_of(lambda: db.prune_activity_log(cutoff), "DELETE FROM activity_log", monkeypatch)
    assert log == ["SEARCH activity_log USING INDEX idx_activity_log_created (created_at<?)"], log


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def _index_names() -> set[str]:
    return {
        row["name"]
        for row in query("SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'")
    }


def test_the_migration_adds_the_indexes_to_an_older_database_and_is_idempotent() -> None:
    names = {name for name, _table, _columns in _HOT_PATH_INDEXES}
    con = get_connection()
    # A database from before Phase 4: none of these indexes.
    for name in names:
        con.execute(f"DROP INDEX {name}")
    before = _index_names()
    assert not names & before

    _apply_migrations(con)
    after_first = {
        row["name"]: row["sql"]
        for row in query("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'")
    }
    assert set(after_first) == before | names

    _apply_migrations(con)
    after_second = {
        row["name"]: row["sql"]
        for row in query("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'")
    }
    assert after_second == after_first


def test_schema_and_migration_declare_the_same_indexes() -> None:
    """schema.sql (new databases) and the migration (existing ones) must not drift.

    idx_tasks_owner_status is the one the schema leaves out: tasks.owner_id
    is a migration-added column, so the schema script cannot index it on a
    database older than that column.
    """
    declared = {
        match.group(1): (match.group(2), " ".join(match.group(3).split()))
        for match in re.finditer(
            r"CREATE INDEX IF NOT EXISTS (\w+)\s+ON (\w+) \(([^)]*)\)", _SCHEMA.read_text(encoding="utf-8"),
        )
    }
    migrated = {name: (table, columns) for name, table, columns in _HOT_PATH_INDEXES}
    assert "idx_tasks_owner_status" not in declared
    for name, spec in migrated.items():
        if name != "idx_tasks_owner_status":
            assert declared.get(name) == spec, name


# ---------------------------------------------------------------------------
# Trigger retention
# ---------------------------------------------------------------------------

def _sql_ts(moment: datetime) -> str:
    """``moment`` in SQLite's ``current_timestamp`` text, the way created_at defaults."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _trigger(
    agent_id: str, status: str, created: datetime, *, finished: datetime | None = None,
) -> str:
    row = query_one(
        """
        INSERT INTO agent_triggers (
            agent_id, trigger_type, source_channel, payload, status, created_at, completed_at, failed_at
        )
        VALUES ($1, 'chat_message', 'chat', '{}', $2, $3, $4, $5)
        RETURNING id
        """,
        [
            agent_id,
            status,
            _sql_ts(created),
            finished if status == "completed" else None,
            finished if status == "failed" else None,
        ],
    )
    assert row is not None
    return str(row["id"])


def _trigger_ids() -> set[str]:
    return {row["id"] for row in query("SELECT id FROM agent_triggers")}


def test_finished_triggers_past_retention_are_pruned_and_nothing_else() -> None:
    agent = db.create_agent("Pruned", role="Eng", desk_x=1, desk_y=1)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = now - timedelta(days=7)
    old = now - timedelta(days=10)
    recent = now - timedelta(days=1)

    old_completed = _trigger(agent.id, "completed", old, finished=old)
    old_failed = _trigger(agent.id, "failed", old, finished=old)
    kept = {
        "recent completed": _trigger(agent.id, "completed", recent, finished=recent),
        "old queued": _trigger(agent.id, "queued", old),
        "old claimed (leased)": _trigger(agent.id, "claimed", old),
        # Created long ago, but it only just finished.
        "old but recently finished": _trigger(agent.id, "completed", old, finished=recent),
        "named by a task event": _trigger(agent.id, "completed", old, finished=old),
        "named by an approval": _trigger(agent.id, "failed", old, finished=old),
    }
    task = db.create_task("Referenced", assigned_to=agent.id)
    db.create_task_event(
        task_id=task.id, author_type="agent", author_name="Pruned", event_type="comment",
        content="done", source_trigger_id=kept["named by a task event"],
    )
    db.create_cli_approval_request(
        agent_id=agent.id, command="ls", trigger_id=kept["named by an approval"],
    )

    removed = db.prune_finished_triggers(cutoff)

    assert removed == 2
    remaining = _trigger_ids()
    assert old_completed not in remaining and old_failed not in remaining
    for label, trigger_id in kept.items():
        assert trigger_id in remaining, label


def test_trigger_prune_boundary_is_the_cutoff_second() -> None:
    agent = db.create_agent("Edge", role="Eng", desk_x=1, desk_y=1)
    cutoff = datetime(2026, 6, 1, 12, 0, 0, 500000, tzinfo=timezone.utc)
    second_before = _trigger(agent.id, "completed", cutoff - timedelta(seconds=1), finished=cutoff - timedelta(seconds=1))
    # created_at holds whole seconds; a row in the cutoff's own second is not older than it.
    same_second = _trigger(agent.id, "completed", cutoff, finished=cutoff - timedelta(seconds=1))

    assert db.prune_finished_triggers(cutoff) == 1
    assert _trigger_ids() == {same_second}
    assert second_before not in _trigger_ids()


def test_prunes_refuse_a_naive_cutoff() -> None:
    naive = datetime(2026, 1, 1)
    for prune in (db.prune_finished_triggers, db.prune_activity_log, db.prune_diagnostics):
        with pytest.raises(ValueError):
            prune(naive)


# ---------------------------------------------------------------------------
# Activity-log retention
# ---------------------------------------------------------------------------

def _log(detail: str, created: datetime) -> None:
    execute(
        "INSERT INTO activity_log (event, detail, created_at) VALUES ('agent_moved', $1, $2)",
        [detail, _sql_ts(created)],
    )


def test_activity_log_rows_past_retention_are_pruned() -> None:
    cutoff = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _log("old", cutoff - timedelta(days=3))
    _log("second before", cutoff - timedelta(seconds=1))
    _log("at cutoff", cutoff)
    _log("new", cutoff + timedelta(days=3))

    assert db.prune_activity_log(cutoff) == 2
    assert sorted(row["detail"] for row in query("SELECT detail FROM activity_log")) == ["at cutoff", "new"]


# ---------------------------------------------------------------------------
# Diagnostics retention
# ---------------------------------------------------------------------------

def _diagnostic(label: str, created: datetime) -> str:
    row = db.create_diagnostic(
        agent_id="agent-x", agent_name="X", trigger_type="chat_message", trigger_data="{}",
        context=label, steps=[{"step_index": 0, "raw_response": label}],
    )
    execute("UPDATE diagnostics SET created_at = $1 WHERE id = $2", [created, row["id"]])
    return str(row["id"])


def test_diagnostics_past_retention_are_pruned_with_their_steps() -> None:
    cutoff = datetime(2026, 6, 1, 12, 0, 0, 500000, tzinfo=timezone.utc)
    old = _diagnostic("old", cutoff - timedelta(days=2))
    just_before = _diagnostic("just before", cutoff - timedelta(microseconds=1))
    at_cutoff = _diagnostic("at cutoff", cutoff)
    just_after = _diagnostic("just after", cutoff + timedelta(microseconds=1))
    # Stored the way create_diagnostic stores it: ISO text with +00:00.
    stored = query_one("SELECT CAST(created_at AS TEXT) AS raw FROM diagnostics WHERE id = $1", [at_cutoff])
    assert stored is not None and stored["raw"] == "2026-06-01 12:00:00.500000+00:00"

    assert db.prune_diagnostics(cutoff) == 2

    left = {row["id"] for row in query("SELECT id FROM diagnostics")}
    assert left == {at_cutoff, just_after}
    step_owners = {row["diagnostic_id"] for row in query("SELECT diagnostic_id FROM diagnostic_steps")}
    assert step_owners == {at_cutoff, just_after}
    assert old not in left and just_before not in left


def test_the_diagnostics_row_limit_is_a_required_setting() -> None:
    execute("DELETE FROM settings WHERE key = 'diagnostics_retention_limit'")
    config.reload()
    with pytest.raises(ConfigError):
        db.create_diagnostic(agent_id="a", agent_name="A", trigger_type="chat_message", trigger_data="{}")


def test_the_diagnostics_row_limit_still_purges_oldest_first() -> None:
    db.set_setting("diagnostics_retention_limit", "2", "advanced")
    config.reload()
    first = db.create_diagnostic(agent_id="a", agent_name="A", trigger_type="t", trigger_data="{}")
    db.create_diagnostic(agent_id="a", agent_name="A", trigger_type="t", trigger_data="{}")
    db.create_diagnostic(agent_id="a", agent_name="A", trigger_type="t", trigger_data="{}")
    ids = {row["id"] for row in query("SELECT id FROM diagnostics")}
    assert len(ids) == 2 and first["id"] not in ids


# ---------------------------------------------------------------------------
# The history-prune hook on the task watchdog
# ---------------------------------------------------------------------------

def test_retention_settings_are_seeded() -> None:
    for key, value in (
        ("trigger_retention_days", "7"),
        ("activity_log_retention_days", "30"),
        ("diagnostics_retention_days", "7"),
        ("diagnostics_retention_limit", "5000"),
        ("history_prune_interval_minutes", "60"),
    ):
        row = query_one("SELECT value, category FROM settings WHERE key = $1", [key])
        assert row == {"value": value, "category": "advanced"}, key
    assert query_one("SELECT 1 AS hit FROM settings WHERE key = 'diagnostics_retention_max_mb'") is None


def _counting_prunes(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[datetime]]:
    import core.agent_loop.watchdog as watchdog_module

    calls: dict[str, list[datetime]] = {"triggers": [], "log": [], "diagnostics": []}
    monkeypatch.setattr(watchdog_module.db, "prune_finished_triggers", lambda cutoff: calls["triggers"].append(cutoff) or 3)
    monkeypatch.setattr(watchdog_module.db, "prune_activity_log", lambda cutoff: calls["log"].append(cutoff) or 4)
    monkeypatch.setattr(watchdog_module.db, "prune_diagnostics", lambda cutoff: calls["diagnostics"].append(cutoff) or 5)
    return calls


def test_the_watchdog_prunes_on_its_first_tick_then_once_per_interval(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import core.agent_loop.watchdog as watchdog_module

    clock = [1000.0]
    monkeypatch.setattr(watchdog_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    calls = _counting_prunes(monkeypatch)
    watchdog = watchdog_module.TaskWatchdog()

    with caplog.at_level(logging.INFO, logger=watchdog_module.__name__):
        before = datetime.now(timezone.utc)
        watchdog._prune_history_if_due()
    assert [len(v) for v in calls.values()] == [1, 1, 1]
    assert calls["triggers"][0] <= before - timedelta(days=7) + timedelta(seconds=5)
    assert calls["triggers"][0] >= before - timedelta(days=7) - timedelta(seconds=5)
    assert abs(calls["log"][0] - (before - timedelta(days=30))) < timedelta(seconds=5)
    assert abs(calls["diagnostics"][0] - (before - timedelta(days=7))) < timedelta(seconds=5)
    assert any(
        "3 finished trigger(s)" in record.getMessage()
        and "4 activity-log row(s)" in record.getMessage()
        and "5 diagnostic(s)" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO
    )

    clock[0] += 3599.0
    watchdog._prune_history_if_due()
    assert [len(v) for v in calls.values()] == [1, 1, 1]

    clock[0] += 1.0
    watchdog._prune_history_if_due()
    assert [len(v) for v in calls.values()] == [2, 2, 2]

    # The interval is the history_prune_interval_minutes setting, read each tick.
    db.set_setting("history_prune_interval_minutes", "5", "advanced")
    config.reload()
    clock[0] += 299.0
    watchdog._prune_history_if_due()
    assert [len(v) for v in calls.values()] == [2, 2, 2]
    clock[0] += 1.0
    watchdog._prune_history_if_due()
    assert [len(v) for v in calls.values()] == [3, 3, 3]


def test_a_non_positive_retention_raises_before_anything_is_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.agent_loop.watchdog as watchdog_module

    calls = _counting_prunes(monkeypatch)
    db.set_setting("diagnostics_retention_days", "0", "advanced")
    config.reload()
    with pytest.raises(ConfigError):
        watchdog_module.TaskWatchdog()._prune_history_if_due()
    assert calls == {"triggers": [], "log": [], "diagnostics": []}


def test_the_first_loop_tick_prunes_even_when_the_task_scan_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.agent_loop.watchdog as watchdog_module

    pruned: list[bool] = []
    watchdog = watchdog_module.TaskWatchdog()

    async def failing_scan() -> None:
        raise RuntimeError("scan broke")

    monkeypatch.setattr(watchdog, "_check_tasks", failing_scan)
    monkeypatch.setattr(watchdog, "_prune_history_if_due", lambda: pruned.append(True))

    async def run_one_tick() -> None:
        watchdog._running = True
        task = asyncio.create_task(watchdog._loop())
        for _ in range(100):
            if pruned:
                break
            await asyncio.sleep(0.01)
        watchdog._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one_tick())
    assert pruned == [True]


def test_only_the_two_checked_columns_store_trigger_ids() -> None:
    """Guard for the reference audit behind prune_finished_triggers.

    No foreign key points at agent_triggers or activity_log, and the only
    trigger-id columns are the two the prune checks. A new one must be added
    to the prune's NOT IN lists, and this test says so.
    """
    referencing = query(
        """
        SELECT m.name AS tbl, f."table" AS target
        FROM sqlite_master m, pragma_foreign_key_list(m.name) f
        WHERE m.type = 'table' AND f."table" IN ('agent_triggers', 'activity_log')
        """
    )
    assert referencing == []
    tables_with_trigger_id = {
        row["tbl"]
        for row in query(
            """
            SELECT m.name AS tbl
            FROM sqlite_master m, pragma_table_info(m.name) p
            WHERE m.type = 'table' AND p.name IN ('trigger_id', 'source_trigger_id')
            """
        )
    }
    assert tables_with_trigger_id == {"task_events", "cli_approval_requests"}


def test_task_lists_keep_insertion_order_for_same_second_tasks() -> None:
    """created_at is whole seconds; ties stay in insertion order whatever index is used."""
    agent = db.create_agent("Ties", role="Eng", desk_x=1, desk_y=1)
    made = [db.create_task(f"task {index}", assigned_to=agent.id, owner_id=agent.id) for index in range(6)]
    for task, status in zip(made, ("active", "pending", "active", "waiting", "pending", "active")):
        execute("UPDATE tasks SET status = $1, created_at = '2026-06-01 12:00:00' WHERE id = $2", [status, task.id])
    expected = [task.id for task in made]
    assert [t.id for t in db.list_tasks_by_statuses(OPEN_TASK_STATUSES, assigned_to=agent.id)] == expected
    assert [t.id for t in db.list_tasks_by_statuses(OPEN_TASK_STATUSES, owner_id=agent.id)] == expected
    assert [t.id for t in db.list_tasks_by_statuses(OPEN_TASK_STATUSES)] == expected
    assert [t.id for t in db.list_tasks(assigned_to=agent.id)] == expected
    actives = [task.id for task, status in zip(made, ("active", "pending", "active", "waiting", "pending", "active")) if status == "active"]
    assert [t.id for t in db.list_tasks(assigned_to=agent.id, status="active")] == actives


def test_diagnostics_age_prune_uses_the_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    steps = _plan_of(lambda: db.prune_diagnostics(cutoff), "DELETE FROM diagnostic_steps", monkeypatch)
    assert _uses(steps, "diagnostic_steps", "idx_diagnostic_steps_diagnostic"), steps
    assert _uses(steps, "diagnostics", "idx_diagnostics_created"), steps
    entries = _plan_of(lambda: db.prune_diagnostics(cutoff), "DELETE FROM diagnostics WHERE created_at", monkeypatch)
    assert any(
        line.startswith("SEARCH diagnostics USING") and "idx_diagnostics_created (created_at<?)" in line
        for line in entries
    ), entries


@pytest.mark.parametrize(
    ("needle", "table", "index"),
    [
        ("SELECT id FROM diagnostics ORDER BY created_at ASC", "diagnostics", "idx_diagnostics_created"),
        ("DELETE FROM diagnostic_steps WHERE diagnostic_id IN", "diagnostic_steps", "idx_diagnostic_steps_diagnostic"),
        ("DELETE FROM diagnostics WHERE id IN (", "diagnostics", "idx_diagnostics_created"),
    ],
)
def test_the_row_limit_purge_uses_the_indexes(
    monkeypatch: pytest.MonkeyPatch, needle: str, table: str, index: str,
) -> None:
    db.set_setting("diagnostics_retention_limit", "1", "advanced")
    config.reload()
    db.create_diagnostic(agent_id="a", agent_name="A", trigger_type="t", trigger_data="{}")

    def over_the_limit() -> None:
        db.create_diagnostic(
            agent_id="a", agent_name="A", trigger_type="t", trigger_data="{}",
            steps=[{"step_index": 0, "raw_response": "r"}],
        )

    plan = _plan_of(over_the_limit, needle, monkeypatch)
    assert _uses(plan, table, index), plan
    assert f"SCAN {table}" not in plan, plan


def test_recent_tasks_order_ties_newest_insert_first_and_pages_consistently() -> None:
    """last_activity and created_at can both tie; rowid settles it, newest first."""
    agent = db.create_agent("Recent", role="Eng", desk_x=1, desk_y=1)
    made = [db.create_task(f"recent {index}", assigned_to=agent.id) for index in range(8)]
    for task in made:
        execute(
            "UPDATE tasks SET last_activity = '2026-06-01 12:00:00', created_at = '2026-06-01 11:00:00' WHERE id = $1",
            [task.id],
        )
    newest_first = [task.id for task in reversed(made)]
    for filters in ({}, {"assigned_to": agent.id}):
        assert [t.id for t in db.list_recent_tasks(limit=50, **filters)] == newest_first, filters
        # A smaller limit is a prefix of the same order: no reshuffle at the edge.
        assert [t.id for t in db.list_recent_tasks(limit=3, **filters)] == newest_first[:3], filters


@pytest.mark.parametrize("value", ["0", "-1", "soon"])
def test_a_bad_prune_interval_raises_before_anything_is_deleted(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    import core.agent_loop.watchdog as watchdog_module

    calls = _counting_prunes(monkeypatch)
    db.set_setting("history_prune_interval_minutes", value, "advanced")
    config.reload()
    with pytest.raises(ConfigError):
        watchdog_module.TaskWatchdog()._prune_history_if_due()
    assert calls == {"triggers": [], "log": [], "diagnostics": []}
