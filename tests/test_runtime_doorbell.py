"""The runtime worker's stdin doorbell (core/runtime/services.py, worker.py).

The ``runtime_commands`` table stays the command queue; the app writes one
newline to the worker's stdin after filing a row so the worker reads the
queue now instead of at its fallback poll. These tests run a real worker
process against the test database.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest

import db
from core import config
from core.runtime.services import RuntimeServices
from db.connection import _ensure_runtime_command_types, get_connection

FALLBACK = "runtime_command_fallback_poll_seconds"


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


async def _wait_for_status(command_id: str, statuses: set[str], timeout: float) -> float:
    """Poll the command row until its status is in ``statuses``; return the seconds waited."""
    started = time.monotonic()
    while True:
        command = db.get_runtime_command(command_id)
        assert command is not None
        if command.status in statuses:
            return time.monotonic() - started
        assert command.status != "failed", command.failure_reason
        assert time.monotonic() - started < timeout, f"command still {command.status} after {timeout}s"
        await asyncio.sleep(0.01)


async def _warm_up(services: RuntimeServices) -> None:
    """Wait until the worker's command loop has run one command end to end."""
    command = db.create_runtime_command("wake_dispatcher")
    services._ring_worker()
    await _wait_for_status(command.id, {"completed"}, timeout=10.0)


def test_the_fallback_poll_is_a_seeded_setting() -> None:
    assert config.get(FALLBACK) == "5"


@pytest.mark.asyncio
async def test_a_command_reaches_the_worker_through_the_doorbell() -> None:
    # A 30 s fallback: the claim has to come well inside it to prove the
    # doorbell. 3 s, not raw speed: under a loaded parallel run the worker
    # can take a few hundred ms to be scheduled, and that is not the question.
    db.set_setting(FALLBACK, "30", "advanced")
    services = RuntimeServices()
    await services.start()
    try:
        await _warm_up(services)
        command = db.create_runtime_command("pause_runtime")
        started = time.monotonic()
        services._ring_worker()
        await _wait_for_status(command.id, {"claimed", "completed"}, timeout=10.0)
        elapsed = time.monotonic() - started
        assert elapsed < 3.0, f"pause took {elapsed:.3f}s to reach the worker (fallback is 30 s)"
        await _wait_for_status(command.id, {"completed"}, timeout=10.0)
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_a_failed_ring_is_logged_and_the_fallback_poll_still_runs_the_command(caplog) -> None:
    db.set_setting(FALLBACK, "1", "advanced")
    services = RuntimeServices()
    await services.start()
    try:
        await _warm_up(services)
        process = services._process
        assert process is not None and process.stdin is not None
        # The app's end of the pipe is gone: every ring from here on fails.
        process.stdin.close()
        command = db.create_runtime_command("wake_dispatcher")
        with caplog.at_level(logging.WARNING, logger="core.runtime.services"):
            services._ring_worker()
        assert any(
            record.levelno == logging.WARNING and "doorbell is closed" in record.getMessage()
            for record in caplog.records
        )
        waited = await _wait_for_status(command.id, {"completed"}, timeout=10.0)
        # Within one fallback interval (1 s), plus room for the command itself.
        assert waited < 2.0, f"fallback took {waited:.2f}s"
    finally:
        await services.stop()


@pytest.mark.asyncio
async def test_an_extension_config_change_reaches_the_worker() -> None:
    db.set_setting(FALLBACK, "30", "advanced")
    services = RuntimeServices()
    await services.start()
    try:
        await _warm_up(services)
        services.extension_config_changed()
        [row] = db.query("SELECT id FROM runtime_commands WHERE command_type = 'extension_config_changed'")
        # Completed, not failed: the worker knows the type (it invalidates
        # the wake service's cached configs).
        await _wait_for_status(row["id"], {"completed"}, timeout=5.0)
    finally:
        await services.stop()


def test_no_worker_means_no_extension_config_command() -> None:
    RuntimeServices().extension_config_changed()
    assert not db.has_open_runtime_command(["extension_config_changed"])


def test_an_older_command_table_is_rebuilt_to_accept_the_new_type() -> None:
    con = get_connection()
    con.execute("DROP TABLE runtime_commands")
    con.execute(
        """
        CREATE TABLE runtime_commands (
            id             VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
            command_type   VARCHAR NOT NULL
                              CHECK (command_type IN (
                                  'wake_dispatcher', 'pause_runtime', 'resume_runtime',
                                  'reset_agent_runtime', 'shutdown_runtime', 'reload_schedules'
                              )),
            payload        TEXT NOT NULL,
            status         VARCHAR NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued', 'claimed', 'completed', 'failed')),
            failure_reason TEXT,
            claimed_at     TIMESTAMP,
            completed_at   TIMESTAMP,
            failed_at      TIMESTAMP,
            created_at     TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    kept = db.create_runtime_command("reload_schedules")
    _ensure_runtime_command_types(con)
    assert db.get_runtime_command(kept.id) is not None
    added = db.create_runtime_command("extension_config_changed")
    assert db.get_runtime_command(added.id).command_type == "extension_config_changed"


@pytest.mark.asyncio
async def test_a_command_queued_inside_the_worker_rings_its_own_doorbell(monkeypatch) -> None:
    """The agent CLI's schedule reload runs in the worker; it must not wait out the fallback."""
    from core.runtime.worker import RuntimeWorker
    from core.scheduling.service import request_reload

    monkeypatch.setenv("BOSSMOD_RUNTIME_WORKER", "1")
    monkeypatch.delenv("BOSSMOD_APP_PID", raising=False)
    # Paused: no services start, so the command loop is the only thing running.
    db.set_setting("runtime_control_state", "paused", "advanced")
    db.set_setting(FALLBACK, "30", "advanced")
    config.reload()
    worker = RuntimeWorker()
    run = asyncio.create_task(worker.run())
    try:
        deadline = time.monotonic() + 10.0
        while (db.get_runtime_worker_state() is None
               or db.get_runtime_worker_state().lifecycle_state != "running"):
            assert time.monotonic() < deadline, "worker did not start"
            await asyncio.sleep(0.01)
        # One loop turn so the command loop is parked on the doorbell.
        await asyncio.sleep(0.05)
        started = time.monotonic()
        request_reload()
        [row] = db.query("SELECT id FROM runtime_commands WHERE command_type = 'reload_schedules'")
        await _wait_for_status(row["id"], {"claimed", "completed"}, timeout=10.0)
        elapsed = time.monotonic() - started
        # Well inside the 30 s fallback proves the local doorbell; raw speed
        # under a loaded parallel run is not the question.
        assert elapsed < 3.0, f"in-worker reload took {elapsed:.3f}s to be picked up (fallback is 30 s)"
    finally:
        shutdown = db.create_runtime_command("shutdown_runtime")
        from core.runtime.services import notify_runtime_command_queued

        notify_runtime_command_queued()
        assert await asyncio.wait_for(run, timeout=10.0) == 0
        assert db.get_runtime_command(shutdown.id) is not None
