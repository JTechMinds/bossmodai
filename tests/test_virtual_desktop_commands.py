"""Virtual Desktop ``vm`` commands against a fake desktop host (no Docker)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import db
from core import config
from core.extensions.contract import ExtensionContext
from tests._virtual_desktop_fakes import ENTRY, PACKAGE, FakeHost, cli_runner, module, types

commands_module = module("commands")
commands_io = module("commands_io")


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


class _Clock:
    """Monotonic time that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture()
def env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ready.json").write_text(json.dumps({"image_version": "1", "docker": "29.8.1"}), encoding="utf-8")
    host = FakeHost()
    clock = _Clock()
    configs: dict[str, dict[str, str]] = {}
    vision = {"value": ("vision-model", True)}
    extension = PACKAGE.VirtualDesktopExtension(
        ExtensionContext(manifest=ENTRY.manifest, data_dir=data_dir, read_agent_config=configs.get),
        host=host,
        vision_model=lambda agent: vision["value"],
        clock=clock,
        sleep=clock.sleep,
    )
    agent = db.create_agent("Desky", role="Tester")
    configs[agent.id] = {}
    return {
        "run": cli_runner(extension, agent), "host": host, "clock": clock, "configs": configs, "vision": vision,
        "extension": extension, "agent": agent, "data": data_dir,
    }


def _error(result) -> str:
    assert not result.ok, result.prompt_content
    return result.data["error"]


def _lines(result) -> list[str]:
    return [line.strip() for line in result.prompt_content.splitlines()]


def _xdotool(host: FakeHost) -> list[list[str]]:
    return [call[1] for call in host.calls if call[0] == "xdotool"]


# ── preconditions and dispatch ───────────────────────────────────────────


def test_setup_must_be_ready(env) -> None:
    (env["data"] / "ready.json").unlink()
    assert _error(env["run"]("vm view")).startswith("SETUP_REQUIRED: the desktop is not set up (missing)")
    assert env["host"].calls == []


def test_an_out_of_date_install_is_not_ready(env) -> None:
    (env["data"] / "ready.json").write_text(json.dumps({"image_version": "0"}), encoding="utf-8")
    assert _error(env["run"]("vm view")).startswith("SETUP_REQUIRED:")


def test_the_model_must_see_images(env) -> None:
    env["vision"]["value"] = ("text-model", False)
    assert _error(env["run"]("vm view")) == "MODEL_CANNOT_SEE_IMAGES: text-model"
    env["vision"]["value"] = (None, False)
    assert _error(env["run"]("vm view")) == "MODEL_CANNOT_SEE_IMAGES: no model configured"
    assert env["host"].calls == []


def test_an_agent_without_settings_is_not_enabled(env) -> None:
    env["configs"].clear()
    assert _error(env["run"]("vm view")) == commands_module.NOT_ENABLED
    assert "Configure agents" in commands_module.NOT_ENABLED
    assert env["host"].calls == []


@pytest.mark.parametrize("raw", ["vm", "vm fly", "vm VIEWX"])
def test_an_unknown_subcommand_answers_usage(env, raw: str) -> None:
    assert _error(env["run"](raw)).startswith("USAGE: vm view|point|point1k|click")
    assert env["host"].calls == []


def test_the_manifest_declares_the_non_idempotent_subcommands_and_the_setup_contract() -> None:
    manifest = ENTRY.manifest
    assert manifest.command.name == "vm"
    assert manifest.command.no_retry == ("reset", "run", "launch")
    assert manifest.setup.ready_requires == {"image_version": manifest.defaults["image_version"]}
    assert manifest.live_view is not None and manifest.live_view.label == "Desktop view"
    assert manifest.wake is not None and manifest.wake.interval_seconds == 60 and manifest.wake.desk_status is False
    assert all(not field.required and field.kind == "number" for field in manifest.agent_config.fields)


# ── screenshots and the result text ──────────────────────────────────────


def test_view_starts_the_desktop_and_describes_the_screenshot(env) -> None:
    result = env["run"]("vm view")
    assert result.ok, result.prompt_content
    lines = _lines(result)
    assert "screen: 1280x800" in lines
    assert "image 1280x800" in lines
    assert 'pointer: (640, 400) px = (500, 501)‰ — over "Desktop"' in lines
    assert "desktop started (6.1s)" in lines
    assert not any(line.startswith("window:") for line in lines)
    assert len(result.image_paths) == 1 and Path(result.image_paths[0]).is_file()
    assert result.summary == "vm view"
    assert env["host"].names()[:2] == ["ensure_running", "capture"]


def test_a_running_desktop_adds_no_start_note_and_the_window_is_named(env) -> None:
    env["run"]("vm view")
    env["host"].active_window = "Example Domain — Mozilla Firefox"
    result = env["run"]("vm view")
    lines = _lines(result)
    assert "window: Example Domain — Mozilla Firefox" in lines
    assert not any(line.startswith("desktop started") for line in lines)
    assert result.summary == "vm view → Example Domain — Mozilla Firefox"


def test_an_image_update_says_what_was_kept(env) -> None:
    env["host"].recreate_next = True
    lines = _lines(env["run"]("vm view"))
    assert "desktop started (6.1s)" in lines
    assert commands_io.UPDATED_NOTE in lines


def test_the_sidecar_holds_exactly_what_the_result_lines_say(env) -> None:
    env["configs"][env["agent"].id] = {"screen_width": "1920", "screen_height": "1080"}
    env["host"].active_window = "Terminal - agent@desktop: ~"
    result = env["run"]("vm view")
    lines = _lines(result)
    sidecar = json.loads(Path(result.image_paths[0]).with_suffix(".json").read_text(encoding="utf-8"))

    def after(prefix: str) -> str:
        found = [line[len(prefix):] for line in lines if line.startswith(prefix)]
        assert len(found) == 1, (prefix, lines)
        return found[0]

    assert sidecar["screen"] == after("screen: ") == "1920x1080"
    assert sidecar["image"] == after("image ") == "1568x882 (scale ×0.8167)"
    assert sidecar["pointer"] == after("pointer: ") == '(523, 327) px = (334, 371)‰ — over "Desktop"'
    assert sidecar["window"] == after("window: ") == "Terminal - agent@desktop: ~"
    assert sidecar["command"] == "vm view"
    assert sidecar["pid"] == os.getpid()
    assert set(sidecar) == {"command", "window", "screen", "image", "pointer", "taken_at", "pid"}


# ── pointing, clicking, keys ─────────────────────────────────────────────


def test_point_needs_a_screenshot_of_the_running_desktop(env) -> None:
    assert _error(env["run"]("vm point 10 10")).startswith('INVALID_ARGUMENT: there is no screenshot of the desktop as it is now; run "vm view" first')
    assert _xdotool(env["host"]) == []


def test_point_moves_the_real_pointer_in_screen_pixels(env) -> None:
    env["configs"][env["agent"].id] = {"screen_width": "1920", "screen_height": "1080"}
    env["run"]("vm view")
    result = env["run"]("vm point 784 441")
    assert result.ok, result.prompt_content
    assert _xdotool(env["host"]) == [["mousemove", "960", "540"]]
    assert env["clock"].sleeps[-1] == pytest.approx(0.6)


def test_point1k_reaches_the_bottom_right_pixel(env) -> None:
    env["run"]("vm view")
    env["run"]("vm point1k 1000 1000")
    assert _xdotool(env["host"]) == [["mousemove", "1279", "799"]]


def test_a_point_outside_the_image_is_refused_not_clamped(env) -> None:
    env["run"]("vm view")
    assert _error(env["run"]("vm point 1280 10")).startswith("INVALID_ARGUMENT: x must be 0–1279, y 0–799")
    assert _xdotool(env["host"]) == []


@pytest.mark.parametrize(("raw", "expected"), [
    ("vm click", ["click", "1"]),
    ("vm click right", ["click", "3"]),
    ("vm click middle", ["click", "2"]),
    ("vm click double", ["click", "--repeat", "2", "1"]),
    ("vm click 100 200", ["mousemove", "100", "200", "click", "1"]),
    ("vm click 100 200 right", ["mousemove", "100", "200", "click", "3"]),
])
def test_click_presses_the_button_where_the_mouse_is_or_moves_first(env, raw: str, expected: list[str]) -> None:
    env["run"]("vm view")
    result = env["run"](raw)
    assert result.ok, result.prompt_content
    assert _xdotool(env["host"]) == [expected]
    assert any(line.startswith("clicked ") for line in _lines(result))


def test_click_refuses_an_unknown_button(env) -> None:
    env["run"]("vm view")
    assert _error(env["run"]("vm click 400")) == "INVALID_ARGUMENT: the button must be left, right, middle or double; got '400'"


def test_drag_presses_moves_and_releases(env) -> None:
    env["run"]("vm view")
    env["run"]("vm drag 300 200")
    assert _xdotool(env["host"]) == [["mousedown", "1", "mousemove", "300", "200", "mouseup", "1"]]


def test_type_from_args_or_body_and_enter(env) -> None:
    env["run"]("vm type hello world")
    env["run"]("vm type --enter", body="from the body")
    assert _xdotool(env["host"]) == [
        ["type", "--clearmodifiers", "--delay", "12", "--", "hello world"],
        ["type", "--clearmodifiers", "--delay", "12", "--", "from the body"],
        ["key", "--clearmodifiers", "Return"],
    ]


def test_type_refuses_different_text_in_both_places_and_no_text(env) -> None:
    assert _error(env["run"]("vm type one", body="two")).startswith("USAGE: vm type [--enter] <text> — give the text either")
    assert _error(env["run"]("vm type --enter")).startswith("USAGE: vm type needs the text")


def test_key_and_scroll(env) -> None:
    env["run"]("vm key ctrl+l")
    env["run"]("vm scroll down")
    env["run"]("vm scroll left 2")
    assert _xdotool(env["host"]) == [
        ["key", "--clearmodifiers", "ctrl+l"],
        ["click", "--repeat", "5", "5"],
        ["click", "--repeat", "2", "6"],
    ]
    assert _error(env["run"]("vm scroll down 0")).startswith("INVALID_ARGUMENT: vm scroll's n must be a whole number")
    assert _error(env["run"]("vm scroll sideways")).startswith("USAGE: vm scroll up|down|left|right [n]")


# ── wait, launch, status, power ─────────────────────────────────────────


def test_wait_reports_a_change_that_settles(env) -> None:
    env["run"]("vm view")
    env["host"].shades = [250, 250, 10, 10, 10, 10]
    result = env["run"]("vm wait 30")
    assert "wait: screen changed after 2.0s (settled)" in _lines(result)


def test_wait_reports_no_change_after_its_limit(env) -> None:
    env["run"]("vm view")
    result = env["run"]("vm wait 3")
    assert "wait: no change after 3.0s" in _lines(result)
    assert _error(env["run"]("vm wait -1")).startswith("INVALID_ARGUMENT: vm wait's limit must be a positive number")


def test_launch_starts_the_program_then_screenshots_after_the_settle(env) -> None:
    result = env["run"]("vm launch firefox-esr https://example.com")
    assert ("launch", "firefox-esr https://example.com") in env["host"].calls
    assert "launched: firefox-esr https://example.com" in _lines(result)
    assert result.data["status_lines"] == ["Opened firefox-esr on the desktop"]
    assert env["clock"].sleeps == [2.5]
    assert result.image_paths


def test_status_does_not_start_a_stopped_desktop(env) -> None:
    result = env["run"]("vm status")
    assert "ensure_running" not in env["host"].names()
    lines = _lines(result)
    assert "desktop: off; any vm command except status and stop starts it" in lines
    assert "screen: 1280x800 (from your settings; applies when it starts)" in lines
    assert "memory limit: 2048 MB" in lines and "CPU limit: 2" in lines and "image version: 1" in lines
    assert not result.image_paths


def test_status_of_a_running_desktop_reports_what_it_runs_with(env) -> None:
    env["run"]("vm view")
    env["clock"].now += 14 * 60
    lines = _lines(env["run"]("vm status"))
    assert "desktop: running" in lines
    assert "screen: 1280x800" in lines
    assert "last used: 14 minutes ago" in lines


def test_stop_powers_down_once_and_deletes_the_screenshots(env) -> None:
    shot = Path(env["run"]("vm view").image_paths[0])
    result = env["run"]("vm stop")
    assert result.data["status_lines"] == ["Powered down the desktop"]
    assert any(line.startswith("desktop powered down: your files and programs are kept") for line in _lines(result))
    assert not shot.exists()
    again = env["run"]("vm stop")
    assert "desktop already off" in _lines(again)
    assert again.data["status_lines"] == []
    assert "ensure_running" not in env["host"].names()[2:]


def test_restart_reboots_a_running_desktop(env) -> None:
    env["run"]("vm view")
    result = env["run"]("vm restart")
    assert "restart" in env["host"].names()
    assert "desktop restarted (3.2s): files and programs kept" in _lines(result)


def test_restart_of_a_stopped_desktop_is_its_start(env) -> None:
    result = env["run"]("vm restart")
    assert "restart" not in env["host"].names()
    assert "desktop started (6.1s)" in _lines(result)


def test_reset_wipes_without_starting_the_old_desktop_first(env) -> None:
    old = Path(env["run"]("vm view").image_paths[0])
    env["host"].calls.clear()
    result = env["run"]("vm reset")
    assert env["host"].names()[0] == "reset"
    assert "ensure_running" not in env["host"].names()
    assert result.data["status_lines"] == ["Reset the desktop"]
    assert any(line.startswith("desktop reset to a fresh install (7.0s)") for line in _lines(result))
    assert not old.exists()
    assert len(list(old.parent.glob("*.png"))) == 1


# ── errors ───────────────────────────────────────────────────────────────


def test_docker_and_desktop_failures_are_coded(env) -> None:
    env["host"].fail["ensure_running"] = types.DockerUnavailable(types.UNAVAILABLE)
    assert _error(env["run"]("vm view")) == f"DOCKER_UNAVAILABLE: {types.UNAVAILABLE}"
    env["host"].fail["capture"] = types.DesktopActionError("bm-capture failed (exit 1): no display")
    assert _error(env["run"]("vm view")) == "DESKTOP_ERROR: bm-capture failed (exit 1): no display"


def test_every_command_past_the_preconditions_starts_an_idle_stretch(env) -> None:
    extension = env["extension"]
    env["host"].fail["ensure_running"] = types.DockerUnavailable(types.UNAVAILABLE)
    env["run"]("vm view")
    assert extension._idle.idle_minutes(env["agent"].id) == 0


def test_a_corrupt_settings_row_is_raised_not_hidden(env) -> None:
    env["configs"][env["agent"].id] = {"cpus": "two"}
    with pytest.raises(ValueError, match="cpus is not a whole number"):
        env["run"]("vm view")


def test_the_prompt_state_comes_from_memory(env) -> None:
    extension, agent = env["extension"], env["agent"]
    assert extension.prompt_state(agent) == "Your desktop right now: off; any vm command except status and stop starts it."
    env["run"]("vm view")
    env["clock"].now += 125 * 60
    assert extension.prompt_state(agent) == "Your desktop right now: running (1280x800), last used 2 hours 5 minutes ago."
