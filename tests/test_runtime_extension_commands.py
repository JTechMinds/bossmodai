"""The extension runtime commands: filed by the app (RuntimeServices), run by the worker (RuntimeController)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import db
from core import config
from core.runtime import worker
from core.runtime.events import NullRuntimeEventSink, runtime_events
from core.runtime.services import RuntimeServices


def setup_function() -> None:
    runtime_events.set_sink(NullRuntimeEventSink())
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    runtime_events.set_sink(NullRuntimeEventSink())
    db.close_connection()


class _RecordingSink(NullRuntimeEventSink):
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    async def broadcast_extension_live(self, extension_id: str | None, agent_id: str | None) -> None:
        self.calls.append(("live", extension_id, agent_id))


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """One ordered log of hook calls and live nudges, with the hooks replaced."""
    log: list[tuple] = []
    runtime_events.set_sink(_RecordingSink(log))
    monkeypatch.setattr(worker.extension_wake_watch, "invalidate", lambda: log.append(("invalidate",)))
    monkeypatch.setattr(
        worker, "notify_agent_config_changed",
        lambda ext_id, agent_id: log.append(("notify", ext_id, agent_id)) or frozenset({ext_id}),
    )
    monkeypatch.setattr(worker, "apply_extension_states", lambda: log.append(("apply",)) or frozenset({"browser"}))
    monkeypatch.setattr(
        worker, "reconcile_agents", lambda: log.append(("reconcile",)) or frozenset({"desk", "browser"}),
    )
    return log


async def _drain(lane: worker.ExtensionLane) -> None:
    """Wait until every job queued before this call has run (the lane is sequential)."""
    done = asyncio.Event()

    async def _mark() -> None:
        done.set()

    lane.submit(_mark)
    await asyncio.wait_for(done.wait(), timeout=5.0)


@pytest.fixture()
async def runtime_worker():
    """A RuntimeWorker whose extension lane runs, stopped after the test."""
    instance = worker.RuntimeWorker()
    instance._controller.extension_lane.start()
    yield instance
    await instance._controller.extension_lane.stop()


# ─── worker dispatch: the handlers queue the hooks and return ───


@pytest.mark.asyncio
async def test_extension_config_changed_invalidates_at_once_then_queues_notify_and_nudge(
    calls: list[tuple], runtime_worker,
) -> None:
    await runtime_worker._execute("extension_config_changed", {"extension_id": "desk", "agent_id": "a1"})
    assert calls == [("invalidate",)], "the command returned before the hook ran"
    await _drain(runtime_worker._controller.extension_lane)
    assert calls == [("invalidate",), ("notify", "desk", "a1"), ("live", "desk", None)]


@pytest.mark.asyncio
async def test_extension_config_changed_without_its_payload_fails_loudly(calls: list[tuple], runtime_worker) -> None:
    # The command loop records the failure on the command row; nothing is guessed.
    with pytest.raises(KeyError):
        await runtime_worker._execute("extension_config_changed", {})


@pytest.mark.asyncio
async def test_extensions_reconcile_queues_apply_then_reconcile_then_one_nudge_each(
    calls: list[tuple], runtime_worker,
) -> None:
    await runtime_worker._execute("extensions_reconcile", {})
    assert calls == [], "the command returned before the hooks ran"
    await _drain(runtime_worker._controller.extension_lane)
    assert calls == [("apply",), ("reconcile",), ("live", "browser", None), ("live", "desk", None)]


@pytest.mark.asyncio
async def test_a_hook_command_before_the_lane_runs_fails_loudly(calls: list[tuple]) -> None:
    with pytest.raises(RuntimeError, match="extension lane is not running"):
        await worker.RuntimeWorker()._execute("extensions_reconcile", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("paused", [True, False])
async def test_boot_no_longer_reconciles(calls: list[tuple], monkeypatch: pytest.MonkeyPatch, paused: bool) -> None:
    monkeypatch.setattr(worker.background_processes, "sweep", lambda **kwargs: calls.append(("sweep",)))
    monkeypatch.setattr(worker.RuntimeController, "_start_services", lambda self: calls.append(("services",)))

    await worker.RuntimeController().boot(paused=paused)

    assert calls == ([("sweep",)] if paused else [("sweep",), ("services",)])


# ─── the extension lane ───


@pytest.mark.asyncio
async def test_lane_jobs_run_in_filing_order_and_a_failing_job_does_not_stop_the_next(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lane = worker.ExtensionLane()
    lane.start()
    ran: list[str] = []

    async def slow() -> None:
        await asyncio.sleep(0.05)
        ran.append("slow")

    async def broken() -> None:
        ran.append("broken")
        raise ValueError("host bug")

    async def quick() -> None:
        ran.append("quick")

    try:
        with caplog.at_level(logging.ERROR, logger=worker.__name__):
            for job in (slow, broken, quick):
                lane.submit(job)
            await _drain(lane)
    finally:
        await lane.stop()
    assert ran == ["slow", "broken", "quick"]
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "broken" in record.getMessage() and record.exc_info is not None


@pytest.mark.asyncio
async def test_stopping_the_lane_drops_queued_jobs_and_refuses_new_ones() -> None:
    lane = worker.ExtensionLane()
    lane.start()
    started = asyncio.Event()
    ran: list[str] = []

    async def blocking() -> None:
        started.set()
        await asyncio.sleep(30)

    async def queued() -> None:
        ran.append("queued")

    lane.submit(blocking)
    lane.submit(queued)
    await asyncio.wait_for(started.wait(), timeout=5.0)
    await asyncio.wait_for(lane.stop(), timeout=5.0)
    assert ran == []
    with pytest.raises(RuntimeError, match="not running"):
        lane.submit(queued)
    await lane.stop()  # a stopped lane stops again without error


@pytest.mark.asyncio
async def test_shutdown_stops_the_lane_before_the_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    controller = worker.RuntimeController()
    controller.extension_lane.start()
    real_stop = controller.extension_lane.stop

    async def _stop_services(self) -> None:
        order.append("services")

    async def _stop_lane() -> None:
        order.append("lane")
        await real_stop()

    monkeypatch.setattr(worker.RuntimeController, "_stop_services", _stop_services)
    monkeypatch.setattr(controller.extension_lane, "stop", _stop_lane)
    monkeypatch.setattr(worker, "shutdown_loaded_extensions", lambda: order.append("extensions"))

    await controller.shutdown()

    assert order == ["services", "lane", "extensions"]
    with pytest.raises(RuntimeError, match="not running"):
        controller.extension_lane.submit(controller.reconcile_after_boot)


# ─── the worker process loop (RuntimeWorker.run) ───


@pytest.fixture()
def quiet_worker(monkeypatch: pytest.MonkeyPatch):
    """A RuntimeWorker run in this process: services, extensions and stdout replaced by recorders."""
    monkeypatch.setenv("BOSSMOD_RUNTIME_WORKER", "1")
    monkeypatch.delenv("BOSSMOD_APP_PID", raising=False)
    db.set_setting("runtime_command_fallback_poll_seconds", "30", "advanced")
    config.reload()
    log: list[tuple] = []

    async def _no_services(self) -> None:
        log.append(("stop-services",))

    monkeypatch.setattr(worker.RuntimeController, "_start_services", lambda self: log.append(("start-services",)))
    monkeypatch.setattr(worker.RuntimeController, "_stop_services", _no_services)
    monkeypatch.setattr(worker, "shutdown_loaded_extensions", lambda: log.append(("shutdown-extensions",)))
    instance = worker.RuntimeWorker()

    async def _send(payload: dict) -> None:
        if payload.get("type") == "event":
            log.append(("event", payload["payload"]["kind"], payload["payload"]["data"].get("extension_id")))
        else:
            log.append(("message", payload["type"]))

    monkeypatch.setattr(instance._transport, "send_message", _send)
    return instance, log


async def _wait_until(predicate, timeout: float = 10.0) -> None:
    started = time.monotonic()
    while not predicate():
        assert time.monotonic() - started < timeout, "condition not met in time"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("paused", [True, False])
async def test_the_boot_reconcile_runs_in_the_lane_after_ready_paused_or_not(
    quiet_worker, monkeypatch: pytest.MonkeyPatch, paused: bool,
) -> None:
    instance, log = quiet_worker
    db.set_setting("runtime_control_state", "paused" if paused else "running", "advanced")
    config.reload()
    monkeypatch.setattr(worker, "reconcile_agents", lambda: log.append(("reconcile",)) or frozenset({"desk"}))

    run = asyncio.create_task(instance.run())
    try:
        await _wait_until(lambda: ("event", "extension_live", "desk") in log)
    finally:
        instance._stopping.set()
        assert await asyncio.wait_for(run, timeout=10.0) == 0

    ready = log.index(("message", "ready"))
    assert log.index(("reconcile",)) > ready
    assert log.index(("event", "extension_live", "desk")) > log.index(("reconcile",))
    assert (("start-services",) in log) is not paused
    # On the way out: services, then (after the lane, pinned above) the extensions.
    assert log[-2:] == [("stop-services",), ("shutdown-extensions",)]


@pytest.mark.asyncio
async def test_a_slow_settings_hook_does_not_hold_up_the_command_loop(quiet_worker, monkeypatch) -> None:
    from core.runtime.services import notify_runtime_command_queued

    instance, log = quiet_worker
    db.set_setting("runtime_control_state", "paused", "advanced")
    config.reload()
    entered, release = threading.Event(), threading.Event()

    def slow_hook(ext_id: str, agent_id: str) -> frozenset[str]:
        entered.set()
        release.wait(timeout=30)
        return frozenset()

    monkeypatch.setattr(worker, "reconcile_agents", lambda: frozenset())
    monkeypatch.setattr(worker, "notify_agent_config_changed", slow_hook)
    monkeypatch.setattr(worker.schedule_watch, "reload", lambda: log.append(("reload",)))

    run = asyncio.create_task(instance.run())
    try:
        await _wait_until(lambda: ("message", "ready") in log)
        changed = db.create_runtime_command("extension_config_changed", {"extension_id": "desk", "agent_id": "a1"})
        later = db.create_runtime_command("reload_schedules")
        notify_runtime_command_queued()
        await _wait_until(lambda: entered.is_set() and db.get_runtime_command(later.id).status == "completed")
        # Both commands are done while the hook is still blocked in its thread.
        assert not release.is_set()
        assert db.get_runtime_command(changed.id).status == "completed"
        assert ("reload",) in log
    finally:
        release.set()
        instance._stopping.set()
        assert await asyncio.wait_for(run, timeout=10.0) == 0


# ─── app side: filing the commands ───


def _running_services(monkeypatch: pytest.MonkeyPatch) -> tuple[RuntimeServices, list[str]]:
    """A RuntimeServices that believes a worker runs, with the doorbell recorded."""
    services = RuntimeServices()
    services._process = SimpleNamespace(returncode=None)
    rings: list[str] = []
    monkeypatch.setattr(services, "_ring_worker", lambda: rings.append("ring"))
    return services, rings


def _open_commands() -> list[dict]:
    return db.query("SELECT command_type, payload FROM runtime_commands WHERE status = 'queued' ORDER BY created_at")


def test_a_config_change_is_filed_with_its_pair_and_rings(monkeypatch: pytest.MonkeyPatch) -> None:
    services, rings = _running_services(monkeypatch)
    services.extension_config_changed("desk", "a1")
    services.extension_config_changed("desk", "a1")  # never de-duplicated
    rows = _open_commands()
    assert [row["command_type"] for row in rows] == ["extension_config_changed"] * 2
    assert [json.loads(row["payload"]) for row in rows] == [{"extension_id": "desk", "agent_id": "a1"}] * 2
    assert rings == ["ring", "ring"]


def test_a_reconcile_is_filed_and_rings(monkeypatch: pytest.MonkeyPatch) -> None:
    services, rings = _running_services(monkeypatch)
    services.extensions_reconcile()
    assert [(row["command_type"], json.loads(row["payload"])) for row in _open_commands()] == [
        ("extensions_reconcile", {}),
    ]
    assert rings == ["ring"]


def test_no_worker_means_no_reconcile_command() -> None:
    RuntimeServices().extensions_reconcile()
    assert not db.has_open_runtime_command(["extensions_reconcile"])


@pytest.mark.asyncio
async def test_a_reconcile_reaches_a_real_worker_and_completes() -> None:
    # A 30 s fallback: completing well inside it shows the doorbell rang and
    # the worker knows the command type.
    db.set_setting("runtime_command_fallback_poll_seconds", "30", "advanced")
    services = RuntimeServices()
    await services.start()
    try:
        services.extensions_reconcile()
        [row] = db.query("SELECT id FROM runtime_commands WHERE command_type = 'extensions_reconcile'")
        started = time.monotonic()
        while (command := db.get_runtime_command(row["id"])).status != "completed":
            assert command.status != "failed", command.failure_reason
            assert time.monotonic() - started < 10.0, f"command still {command.status}"
            await asyncio.sleep(0.01)
    finally:
        await services.stop()
