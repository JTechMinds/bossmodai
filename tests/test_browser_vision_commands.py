"""Browser Vision ``bv`` commands against a fake browser host."""

from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image

import db
from core import config
from core.bm_cli.types import CliExecutionContext, ParsedCliCommand
from core.extensions.contract import ExtensionContext
from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_ENTRY = get_discovery().get("browser-vision")
_PACKAGE = import_package(_ENTRY)
_host_module = importlib.import_module(f"{_PACKAGE.__name__}.browser_host")
_commands_module = importlib.import_module(f"{_PACKAGE.__name__}.commands")
ActionOutcome = _host_module.ActionOutcome
Capture = _host_module.Capture
DownloadResult = _host_module.DownloadResult


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


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (250, 250, 250)).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeHost:
    """Records every call; screenshots are blank pages of the current size."""

    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.calls: list[tuple] = []
        self.size = (1280, 800)
        self.url = "about:blank"
        self.next_outcome = ActionOutcome()

    def _outcome(self):
        outcome, self.next_outcome = self.next_outcome, ActionOutcome()
        return outcome

    def has_session(self, agent_id):
        return agent_id in self.sessions

    def open_session(self, agent_id, viewport, downloads_dir):
        self.calls.append(("open_session", viewport.name, downloads_dir))
        self.sessions.add(agent_id)

    def goto(self, agent_id, url):
        self.calls.append(("goto", url))
        self.url = url
        return self._outcome()

    def click(self, agent_id, x, y):
        self.calls.append(("click", x, y))
        return self._outcome()

    def type_text(self, agent_id, text, *, enter):
        self.calls.append(("type", text, enter))
        return self._outcome()

    def press(self, agent_id, key):
        self.calls.append(("press", key))
        return self._outcome()

    def scroll(self, agent_id, dy):
        self.calls.append(("scroll", dy))
        return self._outcome()

    def back(self, agent_id):
        self.calls.append(("back",))
        return self._outcome()

    def set_viewport(self, agent_id, viewport):
        self.calls.append(("set_viewport", viewport))
        if viewport.width is not None:
            self.size = (viewport.width, viewport.height)
        else:
            self.size = (390, 664)

    def capture(self, agent_id):
        width, height = self.size
        return Capture(png=_png(width, height), url=self.url, title="Fixture", width=width, height=height)

    def close(self, agent_id):
        self.calls.append(("close",))
        existed = agent_id in self.sessions
        self.sessions.discard(agent_id)
        return existed

    def shutdown(self):
        self.calls.append(("shutdown",))


@pytest.fixture()
def env(tmp_path: Path):
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "ready.json").write_text(json.dumps({"browser": "test"}), encoding="utf-8")
    host = FakeHost()
    vision = {"value": ("vision-model", True)}
    extension = _PACKAGE.BrowserVisionExtension(
        ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path / "data"),
        host=host,
        install_dir=install_dir,
        vision_model=lambda agent: vision["value"],
        downloads_dir=lambda agent: tmp_path / "downloads",
    )
    agent = db.create_agent("Iris", role="Researcher")
    ctx = CliExecutionContext(agent=agent, state=db.get_agent_state(agent.id), cwd="/me")

    def run(raw: str, body: str | None = None):
        import shlex

        tokens = shlex.split(raw)
        return extension.handle(ctx, ParsedCliCommand(raw=raw, name=tokens[0], args=tuple(tokens[1:])), body)

    return {"run": run, "host": host, "install": install_dir, "vision": vision, "tmp": tmp_path}


def _error(result) -> str:
    assert not result.ok, result.prompt_content
    return result.data["error"]


def test_setup_must_be_ready(env) -> None:
    (env["install"] / "ready.json").unlink()
    assert _error(env["run"]("bv open example.com")).startswith("SETUP_REQUIRED:")
    assert env["host"].calls == []


def test_the_model_must_see_images(env) -> None:
    env["vision"]["value"] = ("text-model", False)
    assert _error(env["run"]("bv open example.com")) == "MODEL_CANNOT_SEE_IMAGES: text-model"
    env["vision"]["value"] = (None, False)
    assert _error(env["run"]("bv open example.com")) == "MODEL_CANNOT_SEE_IMAGES: no model configured"


def test_open_adds_https_to_a_bare_host_and_returns_a_screenshot(env) -> None:
    result = env["run"]("bv open example.com")
    assert result.ok, result.prompt_content
    assert ("goto", "https://example.com") in env["host"].calls
    assert len(result.image_paths) == 1 and Path(result.image_paths[0]).is_file()
    text = result.prompt_content
    assert "url: https://example.com" in text
    assert "window: desktop 1280x800" in text
    assert "grid: on, density 40 → 32 cols × 20 rows, cells 0–639, colour auto, opacity 0.55" in text


def test_open_accepts_localhost_and_rejects_non_urls(env) -> None:
    assert env["run"]("bv open http://localhost:8000/x").ok
    assert _error(env["run"]("bv open http://")).startswith("INVALID_URL:")
    assert _error(env["run"]("bv open")).startswith("USAGE: bv open <url>")


def test_unknown_subcommand_gives_usage(env) -> None:
    assert _error(env["run"]("bv fly")).startswith("USAGE: bv open|view|")
    assert _error(env["run"]("bv")).startswith("USAGE:")


def test_click_before_any_screenshot_is_no_view(env) -> None:
    assert _error(env["run"]("bv click 3")) == 'NO_VIEW: run "bv view" first'


def test_a_cell_off_the_grid_is_out_of_range_and_nothing_is_clicked(env) -> None:
    env["run"]("bv open example.com")
    message = _error(env["run"]("bv click 640"))
    assert message.startswith("CELL_OUT_OF_RANGE: cell 640")
    assert "0–639" in message
    assert not any(call[0] == "click" for call in env["host"].calls)


def test_the_active_grid_follows_focus_then_click_then_full_view(env) -> None:
    env["run"]("bv open example.com")
    focused = env["run"]("bv view --density 10 --focus 33-66")
    assert focused.ok, focused.prompt_content
    assert "grid: on, density 10 → 128 cols × 80 rows, cells 0–10239" in focused.prompt_content
    assert "focus: cells 33–66 (zoom ×2.4)" in focused.prompt_content
    # Cell (5, 10) at density 10 is number 10*128+5; its centre is (55, 105).
    clicked = env["run"](f"bv click {10 * 128 + 5}")
    assert clicked.ok, clicked.prompt_content
    assert ("click", 55.0, 105.0) in env["host"].calls
    assert "clicked cell 1285 at (55, 105)" in clicked.prompt_content
    # The click returned a full view at the base density, now the active grid.
    assert "density 40 → 32 cols × 20 rows" in clicked.prompt_content
    assert _error(env["run"]("bv click 1285")).startswith("CELL_OUT_OF_RANGE")


def test_density_on_a_plain_view_is_sticky_but_not_with_focus(env) -> None:
    env["run"]("bv open example.com")
    assert "density 20 →" in env["run"]("bv view --density 20").prompt_content
    assert "density 20 →" in env["run"]("bv scroll down").prompt_content
    env["run"]("bv view --density 5 --focus 0-1")
    assert "density 20 →" in env["run"]("bv back").prompt_content


def test_grid_settings_are_sticky_and_parse_errors_name_the_form(env) -> None:
    env["run"]("bv open example.com")
    styled = env["run"]("bv view --grid-color #00ff00 --grid-opacity 0.3")
    assert "colour #00ff00, opacity 0.3" in styled.prompt_content
    assert "colour #00ff00, opacity 0.3" in env["run"]("bv key Tab").prompt_content
    assert "auto or #rrggbb" in _error(env["run"]("bv view --grid-color green"))
    assert "from 0 to 1" in _error(env["run"]("bv view --grid-opacity 2"))
    assert "positive whole number" in _error(env["run"]("bv view --density 0"))
    assert "--grid on|off" in _error(env["run"]("bv view --grid maybe"))
    off = env["run"]("bv view --grid off")
    assert "grid: off (density 40" in off.prompt_content


def test_keys_and_combos_pass_through_verbatim(env) -> None:
    env["run"]("bv open example.com")
    for key in ("Enter", "Control+Shift+T", "F5", "NotARealKey"):
        assert env["run"](f"bv key {key}").ok
    assert [call[1] for call in env["host"].calls if call[0] == "press"] == [
        "Enter", "Control+Shift+T", "F5", "NotARealKey",
    ]


def test_type_takes_the_body_and_enter_flag(env) -> None:
    env["run"]("bv open example.com")
    assert _error(env["run"]("bv type")).startswith("USAGE: bv type needs the text")
    assert env["run"]("bv type --enter", body='say "hi" & bye').ok
    assert ("type", 'say "hi" & bye', True) in env["host"].calls


def test_scroll_moves_most_of_a_screen(env) -> None:
    env["run"]("bv open example.com")
    env["run"]("bv scroll down")
    env["run"]("bv scroll up")
    assert [call[1] for call in env["host"].calls if call[0] == "scroll"] == [640.0, -640.0]
    assert _error(env["run"]("bv scroll left")).startswith("USAGE: bv scroll up|down")


def test_window_presets_and_custom_sizes(env) -> None:
    before = env["run"]("bv window tablet")
    assert before.ok and "applies when you run bv open" in before.prompt_content
    env["run"]("bv open example.com")
    assert ("open_session", "tablet", env["tmp"] / "downloads") in env["host"].calls

    custom = env["run"]("bv window 1024x768")
    assert custom.ok, custom.prompt_content
    viewport = [call[1] for call in env["host"].calls if call[0] == "set_viewport"][-1]
    assert (viewport.name, viewport.device, viewport.width, viewport.height) == ("1024x768", None, 1024, 768)
    assert "viewport: 1024x768" in custom.prompt_content
    assert "form contents and scroll position were reset" in custom.prompt_content

    phone = env["run"]("bv window phone")
    viewport = [call[1] for call in env["host"].calls if call[0] == "set_viewport"][-1]
    assert viewport.device == "iPhone 14"
    assert "window: phone 390x664" in phone.prompt_content
    assert "phone|tablet|desktop|widescreen" in _error(env["run"]("bv window huge"))


def test_downloads_are_listed_in_the_result_and_status(env) -> None:
    env["run"]("bv open example.com")
    env["host"].next_outcome = ActionOutcome(downloads=(
        DownloadResult(file_name="report.pdf", size=12, error=None),
        DownloadResult(file_name=None, size=None, error="big.zip: did not finish within 120s"),
    ))
    result = env["run"]("bv click 5")
    assert "downloaded: /me/downloads/report.pdf (12 bytes)" in result.prompt_content
    assert "download failed: big.zip: did not finish within 120s" in result.prompt_content
    status = env["run"]("bv status")
    assert status.ok and status.image_paths == ()
    assert "downloaded: /me/downloads/report.pdf (12 bytes)" in status.prompt_content


def test_close_ends_the_session_and_forgets_the_view(env) -> None:
    env["run"]("bv open example.com")
    closed = env["run"]("bv close")
    assert closed.ok and "browser session closed" in closed.prompt_content
    assert _error(env["run"]("bv click 1")) == 'NO_VIEW: run "bv view" first'
    assert _error(env["run"]("bv view")) == 'NO_PAGE: run "bv open <url>" first'


def test_downloads_go_to_the_agents_own_me_folder() -> None:
    from core.bm_cli.filesystem import agent_artifact_dir

    agent = db.create_agent("Iris", role="Researcher")
    assert _commands_module.agent_downloads_dir(agent) == agent_artifact_dir(agent.storage_key) / "downloads"
