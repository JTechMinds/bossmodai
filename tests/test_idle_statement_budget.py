"""Idle cost of the runtime worker, counted in SQLite statements.

A real worker (``core.runtime.worker.RuntimeWorker``) runs in a child
process against its own fresh database: 15 agents and 5 dormant threads,
nothing queued. Every statement any thread of that process sends through
``SQLiteCompatConnection.execute`` is counted over a fixed window after a
warm-up, so the number is what an idle desktop pays per second.

The child process is the isolation: the count must not include other
tests' data or other tests' threads, and the worker's own stdin doorbell
needs a real pipe (pytest's stdin is not one).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from core import config
from db.connection import SQLiteCompatConnection, get_connection

ROOT = Path(__file__).resolve().parent.parent

AGENTS = 15
CHANNELS = 5
# Long enough that init_db, service start-up and the first scan of every
# loop are over before counting starts.
WARMUP_SECONDS = 5.0
WINDOW_SECONDS = 10.0
# Was ~64/s before the Phase 1 work; measured 2.4/s after. What is left at
# idle, per 10 s window: the 5 s heartbeat (2-3), the 5 s channel idle scan
# (2-3 scans of one settings read, one thread list and one latest-line read
# per dormant thread: 14-21), and one or two reads per 5 s from the command
# fallback poll, the extension wake tick and the meeting EXISTS (6-12). That
# is 2.2-3.6/s depending on how the timers fall in the window; 5 leaves room
# for a 30 s or 60 s loop (dispatcher fallback, schedule clock) landing in
# it without letting any per-tick N+1 back in (one per agent would add 3/s).
MAX_STATEMENTS_PER_SECOND = 5.0


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


def _statements_during(call) -> list[str]:
    """Run ``call`` and return every SQL statement it sent through the connection layer."""
    sent: list[str] = []
    original = SQLiteCompatConnection.execute

    def counting(self, sql, params=None):
        sent.append(sql)
        return original(self, sql, params)

    SQLiteCompatConnection.execute = counting
    try:
        call()
    finally:
        SQLiteCompatConnection.execute = original
    return sent


_HARNESS = r"""
import asyncio
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import db
from db.connection import SQLiteCompatConnection

agents_n, channels_n = int(sys.argv[1]), int(sys.argv[2])
warmup, window = float(sys.argv[3]), float(sys.argv[4])

db.init_db()
agents = [db.create_agent(f"Agent {index}", role="Engineer") for index in range(agents_n)]
dormant_at = datetime.now(timezone.utc) - timedelta(days=2)
for index in range(channels_n):
    members = [agent.id for agent in agents[index * 3:index * 3 + 3]] or [agents[0].id]
    channel = db.create_channel(name=f"Thread {index}", member_agent_ids=members)
    db.create_channel_message(
        channel_id=channel.id, author_type="human", author_name="Operator",
        content="Status?", source_channel="channel",
    )
    db.execute("UPDATE channel_messages SET created_at = $1 WHERE channel_id = $2", [dormant_at, channel.id])
db.close_connection()

lock = threading.Lock()
count = 0
original = SQLiteCompatConnection.execute


def counting(self, sql, params=None):
    global count
    with lock:
        count += 1
    return original(self, sql, params)


SQLiteCompatConnection.execute = counting

from core.runtime.worker import RuntimeWorker


async def main():
    run = asyncio.create_task(RuntimeWorker().run())
    await asyncio.sleep(warmup)
    with lock:
        start = count
    started = time.monotonic()
    await asyncio.sleep(window)
    with lock:
        measured = count - start
    elapsed = time.monotonic() - started
    os.kill(os.getpid(), signal.SIGTERM)
    code = await run
    print("BUDGET " + json.dumps({"statements": measured, "seconds": elapsed, "exit": code}), flush=True)


asyncio.run(main())
"""


def _run_idle_worker(tmp_path: Path) -> dict:
    env = os.environ.copy()
    env["BOSSMOD_DB_PATH"] = str(tmp_path / "idle.sqlite3")
    env["BOSSMOD_COMPANY_ROOT"] = str(tmp_path / "data" / "company")
    env["BOSSMOD_ARTIFACTS_ROOT"] = str(tmp_path / "data" / "artifacts")
    env["BOSSMOD_RUNTIME_WORKER"] = "1"
    env.pop("BOSSMOD_APP_PID", None)
    out_path, err_path = tmp_path / "worker.out", tmp_path / "worker.err"
    with out_path.open("w") as out, err_path.open("w") as err:
        proc = subprocess.Popen(
            [sys.executable, "-c", _HARNESS, str(AGENTS), str(CHANNELS), str(WARMUP_SECONDS), str(WINDOW_SECONDS)],
            cwd=ROOT,
            env=env,
            # A pipe held open and never written: the doorbell reader blocks,
            # as it does under the app, instead of seeing end-of-file.
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
        )
        try:
            proc.wait(timeout=WARMUP_SECONDS + WINDOW_SECONDS + 60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            assert proc.stdin is not None
            proc.stdin.close()
    stdout, stderr = out_path.read_text(), err_path.read_text()
    assert "doorbell closed" not in stderr, "the doorbell pipe must stay open for the whole run"
    lines = [line for line in stdout.splitlines() if line.startswith("BUDGET ")]
    assert proc.returncode == 0 and lines, stderr[-4000:]
    return json.loads(lines[-1][len("BUDGET "):])


def test_an_idle_worker_stays_within_the_statement_budget(tmp_path: Path) -> None:
    """Fifteen agents and five dormant threads cost a handful of statements a second."""
    result = _run_idle_worker(tmp_path)
    assert result["exit"] == 0
    rate = result["statements"] / result["seconds"]
    assert rate <= MAX_STATEMENTS_PER_SECOND, (
        f"idle worker sent {result['statements']} statements in {result['seconds']:.1f}s "
        f"({rate:.2f}/s, budget {MAX_STATEMENTS_PER_SECOND}/s)"
    )


# ─── The pieces behind the budget ───


def test_a_query_sends_one_statement_with_no_liveness_probe() -> None:
    db.query_one("SELECT 1 AS warm")
    sent = _statements_during(lambda: db.query_one("SELECT 2 AS two"))
    assert sent == ["SELECT 2 AS two"]


def _unusable_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "unusable" in record.getMessage()
    ]


def test_a_closed_connection_fails_loudly_once_then_reconnects_through_crud(caplog) -> None:
    get_connection().close()
    with caplog.at_level(logging.WARNING, logger="db.connection"):
        with pytest.raises(sqlite3.ProgrammingError):
            db.query_one("SELECT 1 AS one")
        assert db.query_one("SELECT 1 AS one") == {"one": 1}
    assert len(_unusable_warnings(caplog)) == 1


def test_a_closed_connection_fails_loudly_once_then_reconnects_for_direct_users(caplog) -> None:
    """The callers that use get_connection() directly get the same handling."""
    get_connection().close()
    with caplog.at_level(logging.WARNING, logger="db.connection"):
        with pytest.raises(sqlite3.ProgrammingError):
            get_connection().execute("SELECT 1")
        assert get_connection().execute("SELECT 1").fetchone() == (1,)
        # A real direct user: claim_runtime_command opens its own cursor.
        command = db.create_runtime_command("wake_dispatcher")
        assert db.claim_runtime_command(command.id) is not None
    assert len(_unusable_warnings(caplog)) == 1


def test_an_ordinary_sql_error_keeps_the_connection() -> None:
    before = get_connection()
    with pytest.raises(sqlite3.OperationalError):
        db.query("SELECT * FROM no_such_table")
    assert get_connection() is before


def test_the_steady_heartbeat_is_one_update_on_the_running_row() -> None:
    db.mark_runtime_worker_running(pid=4242)
    sent = _statements_during(lambda: db.record_runtime_worker_heartbeat(pid=4242))
    assert len(sent) == 1 and sent[0].split()[0].upper() == "UPDATE", sent
    state = db.get_runtime_worker_state()
    assert state.lifecycle_state == "running" and state.pid == 4242


def test_a_heartbeat_from_another_process_does_not_touch_the_row() -> None:
    db.mark_runtime_worker_running(pid=4242)
    before = db.get_runtime_worker_state().last_heartbeat_at
    assert db.record_runtime_worker_heartbeat(pid=9999) is None
    assert db.get_runtime_worker_state().last_heartbeat_at == before


@pytest.mark.asyncio
async def test_the_simulation_sleeps_until_an_agent_has_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.world.simulation import WorldSimulation

    db.set_setting("tick_interval", "0.01", "simulation")
    config.reload()
    simulation = WorldSimulation()
    ticks: list[float] = []
    refreshes: list[bool] = []

    async def tick(elapsed: float) -> None:
        ticks.append(elapsed)

    monkeypatch.setattr(simulation, "_tick", tick)
    monkeypatch.setattr(config, "refresh_if_changed", lambda: refreshes.append(True) or False)
    simulation.start()
    try:
        await asyncio.sleep(0.1)
        assert ticks == [] and refreshes == []
        simulation.set_agent_path("walker", [(1, 1), (2, 1)])
        await asyncio.sleep(0.1)
        assert ticks and len(refreshes) == len(ticks)
        assert ticks[0] < 0.05, "time spent asleep is not walking time"
        simulation.clear_agent_path("walker")
        await asyncio.sleep(0.02)
        settled = len(ticks)
        await asyncio.sleep(0.1)
        assert len(ticks) == settled
    finally:
        await simulation.stop()


def test_the_watchdog_scans_every_thirty_seconds_by_default() -> None:
    from db.settings import get_seed_setting_default

    assert get_seed_setting_default("watchdog_check_interval_seconds") == ("30", "simulation")
    assert config.get("watchdog_check_interval_seconds") == "30"


def _stored(key: str) -> str:
    row = db.query_one("SELECT value FROM settings WHERE key = $1", [key])
    assert row is not None, key
    return str(row["value"])


def test_an_untouched_factory_watchdog_interval_moves_once() -> None:
    from db.settings import reconcile_factory_watchdog_interval

    db.execute("DELETE FROM settings WHERE key = $1", ["watchdog_check_interval_factory_reconciled"])
    db.set_setting("watchdog_check_interval_seconds", "5", "simulation")
    reconcile_factory_watchdog_interval()
    assert _stored("watchdog_check_interval_seconds") == "30"
    # After the pass a 5 is the operator's choice.
    db.set_setting("watchdog_check_interval_seconds", "5", "simulation")
    reconcile_factory_watchdog_interval()
    assert _stored("watchdog_check_interval_seconds") == "5"


def test_a_custom_watchdog_interval_is_kept() -> None:
    from db.settings import reconcile_factory_watchdog_interval

    db.execute("DELETE FROM settings WHERE key = $1", ["watchdog_check_interval_factory_reconciled"])
    db.set_setting("watchdog_check_interval_seconds", "12", "simulation")
    reconcile_factory_watchdog_interval()
    assert _stored("watchdog_check_interval_seconds") == "12"


def test_tasks_by_statuses_is_one_query_in_creation_order() -> None:
    made = {status: db.create_task(f"{status} task") for status in ("active", "waiting", "accepted", "complete")}
    for status, task in made.items():
        db.execute("UPDATE tasks SET status = $1 WHERE id = $2", [status, task.id])
    found: list = []
    sent = _statements_during(lambda: found.extend(db.list_tasks_by_statuses(["active", "accepted", "waiting"])))
    assert len(sent) == 1
    assert [task.id for task in found] == [made["active"].id, made["waiting"].id, made["accepted"].id]
    assert db.list_tasks_by_statuses([]) == []


def test_active_activities_match_the_single_agent_read() -> None:
    walker = db.create_agent("Walker", role="Engineer")
    worker = db.create_agent("Worker", role="Engineer")
    idle = db.create_agent("Idle", role="Engineer")
    older = db.create_runtime_activity(walker.id, "movement")
    newer = db.create_runtime_activity(walker.id, "work", title="Newer")
    db.execute(
        "UPDATE activities SET updated_at = $1 WHERE id = $2",
        [datetime.now(timezone.utc) - timedelta(minutes=5), older.id],
    )
    db.create_runtime_activity(worker.id, "work", title="Paused", status="paused")
    found: dict = {}
    ids = [walker.id, worker.id, idle.id, walker.id]
    sent = _statements_during(lambda: found.update(db.get_active_activities(ids)))
    assert len(sent) == 1
    assert set(found) == {walker.id}
    assert found[walker.id].id == newer.id == db.get_active_activity(walker.id).id
    assert db.get_active_activities([]) == {}


@pytest.mark.asyncio
async def test_the_meeting_watchdog_idles_on_one_query(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agent_loop import meeting_watchdog as module

    db.set_setting("meeting_watchdog_check_interval_seconds", "0.01", "simulation")
    config.reload()
    scans: list[bool] = []
    refreshes: list[bool] = []

    async def scan(self) -> None:
        scans.append(True)

    monkeypatch.setattr(module.MeetingWatchdog, "_check_meetings", scan)
    monkeypatch.setattr(config, "refresh_if_changed", lambda: refreshes.append(True) or False)
    watchdog = module.MeetingWatchdog()
    watchdog.start()
    try:
        await asyncio.sleep(0.1)
        assert scans == [] and refreshes == []
        host = db.create_agent("Host", role="Lead")
        session = db.create_meeting_session("meeting_room", title="Standup", created_by_agent_id=host.id)
        db.upsert_meeting_session_meta(
            session_id=session.id, host_agent_id=host.id, meeting_mode="room", phase="assembling",
        )
        await asyncio.sleep(0.1)
        assert scans and len(refreshes) == len(scans)
    finally:
        await watchdog.stop()


def test_the_heartbeat_interval_is_a_setting_and_staleness_is_three_intervals() -> None:
    assert config.get("runtime_heartbeat_seconds") == "5"
    assert db.heartbeat_interval_seconds() == 5.0
    assert db.heartbeat_stale_after_seconds() == 15.0


@pytest.mark.parametrize("bad", ["0", "-1", "soon"])
def test_an_unusable_heartbeat_setting_raises(bad: str) -> None:
    db.set_setting("runtime_heartbeat_seconds", bad, "advanced")
    config.reload()
    with pytest.raises(config.ConfigError):
        db.heartbeat_stale_after_seconds()


def _heartbeat_age(seconds: float) -> None:
    db.mark_runtime_worker_running(pid=4242)
    stamped = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    db.execute("UPDATE runtime_worker_state SET last_heartbeat_at = $1", [stamped])


@pytest.mark.parametrize("interval", ["1", "5"])
def test_a_heartbeat_one_interval_old_reads_healthy_and_past_three_reads_stale(interval: str) -> None:
    from core.runtime.services import RuntimeServices

    db.set_setting("runtime_heartbeat_seconds", interval, "advanced")
    config.reload()
    step = float(interval)
    _heartbeat_age(step)
    assert db.is_runtime_worker_live()
    assert RuntimeServices().status_payload()["worker"]["healthy"] is True
    _heartbeat_age(3 * step + 1)
    assert not db.is_runtime_worker_live()
    assert RuntimeServices().status_payload()["worker"]["healthy"] is False
