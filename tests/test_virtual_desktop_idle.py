"""Virtual Desktop power-down reminder: one wake per idle stretch, never a forced stop (fake clock, no Docker)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import db
from core import config
from core.extensions.contract import ExtensionContext
from tests._virtual_desktop_fakes import ENTRY, PACKAGE, FakeHost, cli_runner, module

idle = module("idle")


class _Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now

    def minutes(self, count: float) -> None:
        self.now += count * 60


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


@pytest.fixture()
def env(tmp_path: Path):
    (tmp_path / "ready.json").write_text(json.dumps({"image_version": "1"}), encoding="utf-8")
    clock = _Clock()
    host = FakeHost()
    configs: dict[str, dict[str, str]] = {}
    extension = PACKAGE.VirtualDesktopExtension(
        ExtensionContext(manifest=ENTRY.manifest, data_dir=tmp_path, read_agent_config=configs.get),
        host=host, vision_model=lambda agent: ("vision-model", True), clock=clock, sleep=lambda seconds: None,
    )
    agent = db.create_agent("Idler", role="Tester")
    configs[agent.id] = {"power_down_reminder_minutes": "120"}
    return {"extension": extension, "clock": clock, "host": host, "configs": configs, "agent": agent,
            "run": cli_runner(extension, agent)}


def test_no_reminder_for_a_stopped_desktop_before_the_threshold_or_at_zero_minutes(env) -> None:
    extension, clock, agent = env["extension"], env["clock"], env["agent"]
    clock.minutes(500)
    assert extension.poll_wake(agent.id) is None
    env["run"]("vm view")
    clock.minutes(119)
    assert extension.poll_wake(agent.id) is None
    env["configs"][agent.id] = {"power_down_reminder_minutes": "0"}
    clock.minutes(1000)
    assert extension.poll_wake(agent.id) is None


def test_exactly_one_reminder_per_idle_stretch(env) -> None:
    extension, clock, agent = env["extension"], env["clock"], env["agent"]
    env["run"]("vm view")
    clock.minutes(120)
    batch = extension.poll_wake(agent.id)
    assert batch is not None
    assert batch.agent_id == agent.id
    assert batch.title == "Your desktop has had no activity for 2 hours."
    assert batch.lines == [
        "It is still running and using memory. If you are done with it for now, save anything open and run vm stop; "
        "your files and programs are kept."
    ]
    # Not committed yet (the trigger was not persisted): still due.
    assert extension.poll_wake(agent.id) == batch
    extension.commit_wake(batch)
    clock.minutes(600)
    assert extension.poll_wake(agent.id) is None


def test_the_next_vm_command_starts_a_new_stretch(env) -> None:
    extension, clock, agent = env["extension"], env["clock"], env["agent"]
    env["run"]("vm view")
    clock.minutes(125)
    extension.commit_wake(extension.poll_wake(agent.id))
    env["run"]("vm status")
    clock.minutes(119)
    assert extension.poll_wake(agent.id) is None
    clock.minutes(16)
    again = extension.poll_wake(agent.id)
    assert again is not None and again.title == "Your desktop has had no activity for 2 hours 15 minutes."


def test_vacation_counts_the_current_stretch_as_reminded(env) -> None:
    extension, clock, agent = env["extension"], env["clock"], env["agent"]
    env["run"]("vm view")
    clock.minutes(60)
    extension.skip_wake(agent.id)
    clock.minutes(600)
    assert extension.poll_wake(agent.id) is None


def test_the_reminder_minutes_are_read_live_and_a_corrupt_value_is_raised(env) -> None:
    extension, clock, agent = env["extension"], env["clock"], env["agent"]
    env["run"]("vm view")
    clock.minutes(45)
    assert extension.poll_wake(agent.id) is None
    env["configs"][agent.id] = {"power_down_reminder_minutes": "45"}
    assert extension.poll_wake(agent.id).title == "Your desktop has had no activity for 45 minutes."
    env["configs"][agent.id] = {"power_down_reminder_minutes": "soon"}
    with pytest.raises(ValueError):
        extension.poll_wake(agent.id)


def test_the_reminder_never_stops_the_desktop(env) -> None:
    extension, clock, agent = env["extension"], env["clock"], env["agent"]
    env["run"]("vm view")
    clock.minutes(10_000)
    extension.commit_wake(extension.poll_wake(agent.id))
    assert "stop" not in env["host"].names()
    assert env["host"].running_here() == frozenset({agent.id})


def test_vm_stop_says_so_once_and_deletes_the_screenshots(env) -> None:
    shot = Path(env["run"]("vm view").image_paths[0])
    stopped = env["run"]("vm stop")
    assert stopped.data["status_lines"] == ["Powered down the desktop"]
    assert not shot.exists()
    again = env["run"]("vm stop")
    assert again.data["status_lines"] == []
    assert "desktop already off" in again.prompt_content
    env["clock"].minutes(500)
    assert env["extension"].poll_wake(env["agent"].id) is None


@pytest.mark.parametrize(("minutes", "text"), [
    (0, "0 minutes"), (1, "1 minute"), (45, "45 minutes"), (60, "1 hour"), (61, "1 hour 1 minute"),
    (120, "2 hours"), (135, "2 hours 15 minutes"),
])
def test_format_minutes(minutes: int, text: str) -> None:
    assert idle.format_minutes(minutes) == text


def test_the_tracker_alone() -> None:
    clock = _Clock()
    tracker = idle.IdleTracker(clock)
    assert tracker.due("a", 1) is None
    tracker.touch("a")
    clock.minutes(1)
    batch = tracker.due("a", 1)
    tracker.mark_reminded("a", batch.cursor)
    assert tracker.due("a", 1) is None
    tracker.forget("a")
    assert tracker.due("a", 1) is None and tracker.idle_minutes("a") is None
    with pytest.raises(ValueError):
        tracker.mark_reminded("a", "not-a-stamp")
