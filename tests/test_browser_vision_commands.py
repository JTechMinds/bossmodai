"""Browser Vision ``bv`` commands against a fake browser host."""

from __future__ import annotations

import importlib
import io
import json
import os
from datetime import datetime, timedelta
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
_marks_module = importlib.import_module(f"{_PACKAGE.__name__}.marks")
ActionOutcome = _host_module.ActionOutcome
Capture = _host_module.Capture
DownloadResult = _host_module.DownloadResult
SessionInfo = _host_module.SessionInfo
Paced = _host_module.Paced
WaitOutcome = _host_module.WaitOutcome
Mark = _marks_module.Mark

# The fake page's controls: a button, a text field and a dropdown.
_MARKS = (
    Mark(n=1, kind="button", name="Sign in", rect=(20.0, 20.0, 220.0, 80.0), point=(120.0, 50.0), state=None),
    Mark(n=2, kind="textbox", name="Email", rect=(20.0, 110.0, 260.0, 140.0), point=(140.0, 125.0), state="empty"),
    Mark(n=3, kind="select", name="Apple", rect=(20.0, 170.0, 180.0, 200.0), point=(100.0, 185.0), state=None),
)


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
        # Open sessions: agent id → session id.
        self.open: dict[str, str] = {}
        self.opened = 0
        self.window = "desktop"
        self.calls: list[tuple] = []
        self.size = (1280, 800)
        self.url = "about:blank"
        self.next_outcome = ActionOutcome()
        # Set to make the next click land on a new page.
        self.click_navigates_to: str | None = None
        # Set to make the next goto fail with this Playwright message.
        self.goto_error: str | None = None
        self.marks = _MARKS
        # What describe() reports for any point (click feedback).
        self.hit = {"kind": "button", "name": "Sign in"}
        # What the next wait_for_change() reports, and where it leaves the page.
        self.next_wait = WaitOutcome(changed=False, elapsed_s=30.0, settled=False)
        self.wait_navigates_to: str | None = None
        # How long the next pace() says it waited (R33).
        self.next_pace_s = 0.0
        # Bot-check signals the next captures carry (R34).
        self.nav_status: int | None = None
        self.frame_urls: tuple[str, ...] = ()
        self.text = ""

    def _outcome(self):
        outcome, self.next_outcome = self.next_outcome, ActionOutcome()
        return outcome

    def has_session(self, agent_id):
        return agent_id in self.open

    def sessions(self):
        return {
            agent_id: SessionInfo(session_id=session_id, opened_at="2026-09-28T12:00:00+00:00",
                                  url=self.url, window=self.window)
            for agent_id, session_id in self.open.items()
        }

    def pace(self, key):
        self.calls.append(("pace", key))
        waited, self.next_pace_s = self.next_pace_s, 0.0
        return Paced(site=key, waited_s=waited)

    def open_session(self, agent_id, viewport, downloads_dir):
        self.calls.append(("open_session", viewport.name, downloads_dir))
        if agent_id not in self.open:
            self.opened += 1
            self.open[agent_id] = f"session{self.opened}"
            self.window = viewport.name

    def goto(self, agent_id, url):
        self.calls.append(("goto", url))
        if self.goto_error is not None:
            message, self.goto_error = self.goto_error, None
            raise _host_module.BrowserActionError(message)
        self.url = url
        return self._outcome()

    def click(self, agent_id, x, y):
        self.calls.append(("click", x, y))
        if self.click_navigates_to is not None:
            self.url, self.click_navigates_to = self.click_navigates_to, None
        return self._outcome()

    def hover(self, agent_id, x, y):
        self.calls.append(("hover", x, y))
        return self._outcome()

    def describe(self, agent_id, x, y):
        self.calls.append(("describe", x, y))
        return self.hit

    def select(self, agent_id, mark_n, x, y, label):
        self.calls.append(("select", mark_n, x, y, label))
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

    def wait_for_change(self, agent_id, timeout_s):
        self.calls.append(("wait", timeout_s))
        if self.wait_navigates_to is not None:
            self.url, self.wait_navigates_to = self.wait_navigates_to, None
        return self.next_wait

    def set_viewport(self, agent_id, viewport):
        self.calls.append(("set_viewport", viewport))
        self.window = viewport.name
        if viewport.width is not None:
            self.size = (viewport.width, viewport.height)
        else:
            self.size = (390, 664)

    def capture(self, agent_id):
        width, height = self.size
        return Capture(png=_png(width, height), url=self.url, title="Fixture", width=width, height=height,
                       session=self.sessions()[agent_id], marks=self.marks, nav_status=self.nav_status,
                       frame_urls=self.frame_urls, text=self.text)

    def close(self, agent_id):
        self.calls.append(("close",))
        return self.open.pop(agent_id, None) is not None

    def shutdown(self):
        self.calls.append(("shutdown",))
        self.open.clear()


@pytest.fixture()
def env(tmp_path: Path):
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "ready.json").write_text(json.dumps({"browser": "test", "browser_kind": "chromium"}), encoding="utf-8")
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

    return {"run": run, "host": host, "install": install_dir, "vision": vision, "tmp": tmp_path,
            "extension": extension, "agent": agent}


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
    assert "view: full page" in text
    assert "marks: 3" in text
    assert '[@1] button "Sign in"' in text
    assert '[@2] textbox "Email" (empty)' in text
    assert '[@3] select "Apple"' in text


def test_each_screenshot_has_a_sidecar_describing_it(env) -> None:
    env["run"]("bv open example.com")
    result = env["run"]("bv zoom 5")
    sidecar = json.loads(Path(result.image_paths[0]).with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["command"] == "bv zoom 5"
    assert sidecar["url"] == "https://example.com"
    assert sidecar["title"] == "Fixture"
    assert sidecar["window"] == "desktop 1280x800"
    assert sidecar["view"] == "view: zoom 5 — region 427×267 px at (427, 267)"
    assert sidecar["image"] == "1568x980"
    assert sidecar["marks"] == 3
    assert sidecar["taken_at"].endswith("+00:00")
    assert set(sidecar) == {"command", "url", "title", "window", "view", "image", "marks", "taken_at"}


def test_open_accepts_localhost_and_rejects_non_urls(env) -> None:
    assert env["run"]("bv open http://localhost:8000/x").ok
    assert _error(env["run"]('bv open ""')).startswith("INVALID_URL: empty URL")
    assert _error(env["run"]("bv open :::")).startswith("INVALID_URL:")
    assert _error(env["run"]("bv open")).startswith("USAGE: bv open <url>")


@pytest.mark.parametrize(("typed", "opened"), [
    # An explicit scheme passes through unchanged (D8): the browser decides.
    ("file:///tmp/x.html", "file:///tmp/x.html"),
    ("about:blank", "about:blank"),
    ("data:text/html,hi", "data:text/html,hi"),
    ("http://", "http://"),
    # No scheme: https:// is added.
    ("example.com", "https://example.com"),
    # host:port looks like "scheme:" to urlsplit but is a host with a port.
    ("localhost:3000/app", "https://localhost:3000/app"),
    ("localhost:3000", "https://localhost:3000"),
])
def test_open_passes_any_scheme_and_adds_https_only_without_one(env, typed, opened) -> None:
    assert env["run"](f"bv open {typed}").ok
    assert [call for call in env["host"].calls if call[0] == "goto"][-1] == ("goto", opened)


def test_unknown_subcommand_gives_usage(env) -> None:
    assert _error(env["run"]("bv fly")).startswith("USAGE: bv open|view|zoom|point|point1k|click|type|select|")
    assert _error(env["run"]("bv")).startswith("USAGE:")


def test_click_before_any_page_is_no_page(env) -> None:
    assert _error(env["run"]("bv click @1")) == 'NO_PAGE: run "bv open <url>" first'
    assert _error(env["run"]("bv click 3")) == 'NO_PAGE: run "bv open <url>" first'


def test_an_unknown_mark_names_the_valid_range_and_nothing_is_clicked(env) -> None:
    env["run"]("bv open example.com")
    message = _error(env["run"]("bv click @4"))
    assert message == "MARK_NOT_FOUND: @4 is not on the latest screenshot; valid marks: @1–@3"
    env["host"].marks = ()
    env["run"]("bv view")
    assert _error(env["run"]("bv click @1")).endswith("valid marks: none (no marks on the latest screenshot)")
    assert not any(call[0] == "click" for call in env["host"].calls)


def test_a_bad_keypad_digit_names_the_form(env) -> None:
    env["run"]("bv open example.com")
    for bad in ("0", "10", "x"):
        assert "one digit 1–9" in _error(env["run"](f"bv click {bad}")), bad
        assert "one digit 1–9" in _error(env["run"](f"bv zoom {bad}")), bad


def test_a_mark_click_hits_its_point_and_reports_what_it_hit(env) -> None:
    env["run"]("bv open example.com")
    clicked = env["run"]("bv click @1")
    assert clicked.ok, clicked.prompt_content
    calls = env["host"].calls
    assert ("describe", 120.0, 50.0) in calls and ("click", 120.0, 50.0) in calls
    # Feedback is read before the click lands.
    assert calls.index(("describe", 120.0, 50.0)) < calls.index(("click", 120.0, 50.0))
    assert 'clicked button "Sign in" at (120, 50)' in clicked.prompt_content
    env["host"].hit = {"tag": "CANVAS"}
    assert "clicked canvas (not a control) at (640, 400)" in env["run"]("bv click 5").prompt_content


def test_the_zoom_stack_narrows_the_keypad_and_actions_reset_it(env) -> None:
    env["run"]("bv open example.com")
    zoomed = env["run"]("bv zoom 5")
    assert "view: zoom 5 — region 427×267 px at (427, 267)" in zoomed.prompt_content
    assert "image 1568x980 (scale ×3.675)" in zoomed.prompt_content
    # A region click uses the CURRENT view: region 9 of zoom 5.
    clicked = env["run"]("bv click 9")
    assert ("click", pytest.approx(2560 / 3 - 1280 / 18), pytest.approx(1600 / 3 - 800 / 18)) in [
        call for call in env["host"].calls if call[0] == "click"
    ]
    assert "view: full page" in clicked.prompt_content  # an action resets the zoom
    chained = env["run"]("bv zoom 5 3")
    assert "view: zoom 5 › 3 — region 142×89 px at (711, 267)" in chained.prompt_content
    # view keeps the zoom; out pops one level; reset clears it.
    assert "view: zoom 5 › 3" in env["run"]("bv view").prompt_content
    assert "view: zoom 5 —" in env["run"]("bv zoom out").prompt_content
    assert "view: full page" in env["run"]("bv zoom reset").prompt_content
    assert _error(env["run"]("bv zoom out")) == "ZOOM_OUT: already showing the full page"


def test_zoom_stops_below_one_pixel_and_leaves_the_view_unchanged(env) -> None:
    env["run"]("bv open example.com")
    env["run"]("bv zoom 5 5 5 5 5 5")
    message = _error(env["run"]("bv zoom 5"))
    assert message.startswith("ZOOM_LIMIT: region 5")
    assert "view: zoom 5 › 5 › 5 › 5 › 5 › 5 —" in env["run"]("bv view").prompt_content
    # A chain that would pass the limit changes nothing either.
    env["run"]("bv zoom reset")
    assert _error(env["run"]("bv zoom 5 5 5 5 5 5 5")).startswith("ZOOM_LIMIT")
    assert "view: full page" in env["run"]("bv view").prompt_content


def test_marks_setting_is_sticky(env) -> None:
    env["run"]("bv open example.com")
    assert "marks: 3 (hidden on the image)" in env["run"]("bv view --marks off").prompt_content
    assert "marks: 3 (hidden on the image)" in env["run"]("bv key Tab").prompt_content
    assert "marks: 3\n" in env["run"]("bv view --marks on").prompt_content + "\n"
    assert "--marks on|off" in _error(env["run"]("bv view --marks maybe"))


def test_grid_settings_are_sticky_and_parse_errors_name_the_form(env) -> None:
    env["run"]("bv open example.com")
    env["run"]("bv view --grid on --grid-color #00ff00 --grid-opacity 0.3")
    env["run"]("bv key Tab")
    assert "grid: on, colour #00ff00, opacity 0.3" in env["run"]("bv status").prompt_content
    assert "auto or #rrggbb" in _error(env["run"]("bv view --grid-color green"))
    assert "from 0 to 1" in _error(env["run"]("bv view --grid-opacity 2"))
    assert "--grid on|off" in _error(env["run"]("bv view --grid maybe"))
    env["run"]("bv view --grid off")
    assert "grid: off, colour #00ff00" in env["run"]("bv status").prompt_content
    # The removed density/focus language is simply unknown now.
    assert "unknown argument '--density'" in _error(env["run"]("bv view --density 40"))
    assert "unknown argument '--focus'" in _error(env["run"]("bv view --focus 1-2"))


def test_type_into_a_mark_clicks_it_first(env) -> None:
    env["run"]("bv open example.com")
    typed = env["run"]("bv type @2 --enter", body="me@example.com")
    assert typed.ok, typed.prompt_content
    calls = env["host"].calls
    assert calls.index(("click", 140.0, 125.0)) < calls.index(("type", "me@example.com", True))
    assert 'clicked button "Sign in" at (140, 125)' in typed.prompt_content
    assert "typed 14 characters" in typed.prompt_content
    assert _error(env["run"]("bv type 2", body="x")).startswith("USAGE: bv type [@<n>]")


def test_select_chooses_an_option_on_a_select_mark_only(env) -> None:
    env["run"]("bv open example.com")
    picked = env["run"]("bv select @3 Blood orange")
    assert picked.ok, picked.prompt_content
    assert ("select", 3, 100.0, 185.0, "Blood orange") in env["host"].calls
    assert 'selected "Blood orange" in @3' in picked.prompt_content
    assert _error(env["run"]("bv select @1 Yes")) == "NOT_A_SELECT: @1 is a button; use bv click or bv type for it"
    assert _error(env["run"]("bv select @3")).startswith("USAGE: bv select @<n> <option text>")


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


# ─── inline type text (R25) ───


def test_type_takes_inline_text_after_the_mark_and_enter(env) -> None:
    env["run"]("bv open example.com")
    typed = env["run"]("bv type @1 hello world --enter")
    assert typed.ok, typed.prompt_content
    calls = env["host"].calls
    assert calls.index(("click", 120.0, 50.0)) < calls.index(("type", "hello world", True))
    assert "typed 11 characters" in typed.prompt_content


def test_enter_may_come_before_the_mark(env) -> None:
    env["run"]("bv open example.com")
    assert env["run"]("bv type --enter @2 hi").ok
    calls = env["host"].calls
    assert calls.index(("click", 140.0, 125.0)) < calls.index(("type", "hi", True))


def test_inline_text_without_a_mark_types_into_the_focus(env) -> None:
    env["run"]("bv open example.com")
    assert env["run"]("bv type 787 SW 13th Ave").ok
    assert ("type", "787 SW 13th Ave", False) in env["host"].calls
    assert not any(call[0] == "click" for call in env["host"].calls)


def test_different_text_in_the_body_and_the_command_is_a_usage_error(env) -> None:
    # R32 accepts identical text in both places, so the error case differs.
    env["run"]("bv open example.com")
    message = _error(env["run"]("bv type @1 hello", body="goodbye"))
    assert message == (
        "USAGE: bv type [@<n>] [--enter] [<text …>] — give the text either in the body or after the command, not both"
    )
    assert _error(env["run"]("bv type @1 --enter")).startswith("USAGE: bv type needs the text")
    assert not any(call[0] in {"click", "type"} for call in env["host"].calls)


def test_the_same_text_in_the_body_and_the_command_is_typed_once(env) -> None:
    """R32: equal after collapsing whitespace, so it is one text, typed as the body gives it."""
    env["run"]("bv open example.com")
    typed = env["run"]("bv type @1 --enter 123 Main St,  Miami FL", body="123 Main St, Miami  FL\n")
    assert typed.ok, typed.prompt_content
    assert [call for call in env["host"].calls if call[0] == "type"] == [("type", "123 Main St, Miami  FL\n", True)]


def test_body_only_typing_is_unchanged(env) -> None:
    env["run"]("bv open example.com")
    assert env["run"]("bv type @2 --enter", body="me@example.com  two spaces").ok
    assert ("type", "me@example.com  two spaces", True) in env["host"].calls


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

    wide = env["run"]("bv window widescreen")
    assert "viewport: 1920x1080" in wide.prompt_content
    assert "image 1568x882 (scale ×0.8167)" in wide.prompt_content
    # Clicks stay in CSS px: a mark's point is the same whatever the image size.
    env["run"]("bv click @1")
    assert [call for call in env["host"].calls if call[0] == "click"][-1] == ("click", 120.0, 50.0)


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
    assert _error(env["run"]("bv click @1")) == 'NO_PAGE: run "bv open <url>" first'
    assert _error(env["run"]("bv view")) == 'NO_PAGE: run "bv open <url>" first'


def test_downloads_go_to_the_agents_own_me_folder() -> None:
    from core.bm_cli.filesystem import agent_artifact_dir

    agent = db.create_agent("Iris", role="Researcher")
    assert _commands_module.agent_downloads_dir(agent) == agent_artifact_dir(agent.storage_key) / "downloads"


def test_a_manifest_with_label_font_min_above_max_is_rejected() -> None:
    from pydantic import ValidationError

    defaults = dict(_ENTRY.manifest.defaults)
    assert _commands_module.BrowserVisionDefaults.model_validate(defaults).label_font_max_px == 14
    defaults.update(label_font_min_px=16, label_font_max_px=14)
    with pytest.raises(ValidationError, match="label_font_min_px must not exceed label_font_max_px"):
        _commands_module.BrowserVisionDefaults.model_validate(defaults)



# ─── operator status lines (R13) ───


def _lines(result) -> list[str]:
    return result.data["status_lines"]


def test_open_says_browsing_the_short_url(env) -> None:
    assert _lines(env["run"]("bv open https://example.com/a/")) == ["Browsing example.com/a"]


def test_a_click_on_the_same_page_says_nothing_and_a_navigating_one_says_browsing(env) -> None:
    env["run"]("bv open example.com")
    assert _lines(env["run"]("bv click 3")) == []
    env["host"].click_navigates_to = "https://example.com/next?q=1"
    assert _lines(env["run"]("bv click 3")) == ["Browsing example.com/next?q=1"]
    # Plain view, scroll and window say nothing.
    for command in ("bv view", "bv scroll down", "bv window tablet"):
        assert _lines(env["run"](command)) == [], command


def test_a_download_says_where_it_went(env) -> None:
    env["run"]("bv open example.com")
    env["host"].next_outcome = ActionOutcome(downloads=(DownloadResult(file_name="x.pdf", size=3, error=None),))
    assert _lines(env["run"]("bv click 1")) == ["Downloaded x.pdf to /me/downloads"]


def test_a_navigation_and_a_download_in_one_action_give_two_lines(env) -> None:
    env["run"]("bv open example.com")
    env["host"].click_navigates_to = "https://example.com/files"
    env["host"].next_outcome = ActionOutcome(downloads=(DownloadResult(file_name="x.pdf", size=3, error=None),))
    assert _lines(env["run"]("bv click 1")) == ["Browsing example.com/files", "Downloaded x.pdf to /me/downloads"]


def test_a_failed_open_says_it_could_not_open(env) -> None:
    env["host"].goto_error = "net::ERR_NAME_NOT_RESOLVED at https://nope.invalid/\nCall log: ..."
    result = env["run"]("bv open nope.invalid")
    assert not result.ok
    assert _lines(result) == ["Couldn't open nope.invalid — net::ERR_NAME_NOT_RESOLVED at https://nope.invalid/"]


def test_close_says_closed_only_when_a_session_was_open(env) -> None:
    env["run"]("bv open example.com")
    assert _lines(env["run"]("bv close")) == ["Closed the browser"]
    assert _lines(env["run"]("bv close")) == []


def test_long_urls_and_reasons_are_truncated_to_80_characters(env) -> None:
    long_path = "a" * 200
    line = _lines(env["run"](f"bv open https://example.com/{long_path}"))[0]
    assert line == "Browsing " + ("example.com/" + long_path)[:79] + "…"
    env["host"].goto_error = "E" * 200
    failed = _lines(env["run"]("bv open example.org"))[0]
    assert failed == "Couldn't open example.org — " + "E" * 79 + "…"


def test_short_url_keeps_a_scheme_without_slashes() -> None:
    assert _commands_module.short_url("about:blank") == "about:blank"
    assert _commands_module.short_url("file:///tmp/x.html") == "/tmp/x.html"
    assert _commands_module.short_url("http://localhost:3000/") == "localhost:3000"


# ─── direct pointing: bv point / point1k, bv click at the pointer (R21) ───


def _cursor_at(result, x: int, y: int) -> bool:
    """Whether the result's image has the cursor's right arm at image px (x, y)."""
    image = Image.open(Path(result.image_paths[0])).convert("RGB")
    if not (0 <= x < image.width - 8 and 0 <= y < image.height):
        return False
    return sum(image.getpixel((x + 8, y))) < 100 and sum(image.getpixel((x, y))) > 600


def _hovers(env) -> list[tuple]:
    return [call for call in env["host"].calls if call[0] == "hover"]


def test_point_moves_the_mouse_and_echoes_both_units(env) -> None:
    env["run"]("bv open example.com")
    env["host"].hit = {"kind": "option", "name": "Alpha Road"}
    pointed = env["run"]("bv point 412 488")
    assert pointed.ok, pointed.prompt_content
    assert _hovers(env)[-1] == ("hover", 412.0, 488.0)
    assert 'pointer: (412, 488) px = (322, 610)‰ — hovering option "Alpha Road"' in pointed.prompt_content
    assert "view: full page" in pointed.prompt_content
    assert _cursor_at(pointed, 412, 488)
    assert not any(call[0] == "click" for call in env["host"].calls)


def test_point1k_is_a_0_to_1000_scale_of_the_image(env) -> None:
    env["run"]("bv open example.com")
    pointed = env["run"]("bv point1k 500 250")
    assert _hovers(env)[-1] == ("hover", 640.0, 200.0)
    assert "pointer: (640, 200) px = (500, 250)‰" in pointed.prompt_content
    env["run"]("bv point1k 1000 1000")
    assert _hovers(env)[-1] == ("hover", 1280.0, 800.0)
    env["run"]("bv point1k 0 0")
    assert _hovers(env)[-1] == ("hover", 0.0, 0.0)


def test_point_is_in_pixels_of_the_latest_image_shrunk_or_zoomed(env) -> None:
    env["run"]("bv open example.com")
    env["run"]("bv window widescreen")  # image 1568x882 of a 1920x1080 page
    env["run"]("bv point 784 441")
    assert _hovers(env)[-1] == ("hover", pytest.approx(960.0), pytest.approx(540.0))
    env["run"]("bv window desktop")
    zoomed = env["run"]("bv zoom 5")  # image 1568x980 of region 427×267 at (427, 267)
    assert "image 1568x980 (scale ×3.675)" in zoomed.prompt_content
    env["run"]("bv point 784 490")  # the image centre is the region centre
    assert _hovers(env)[-1] == ("hover", pytest.approx(640.0), pytest.approx(400.0))
    pointed = env["run"]("bv point 900 600")  # away from the keypad "5" tag
    assert _hovers(env)[-1] == ("hover", pytest.approx(1280 / 3 + 900 * 1280 / 3 / 1568),
                                pytest.approx(800 / 3 + 600 * 1280 / 3 / 1568))
    assert "view: zoom 5 —" in pointed.prompt_content  # point keeps the zoom
    assert _cursor_at(pointed, 900, 600)


def test_points_outside_the_image_name_the_range_for_the_command(env) -> None:
    assert _error(env["run"]("bv point 1 1")) == 'NO_PAGE: run "bv open <url>" first'
    env["run"]("bv open example.com")
    for bad in ("1280 0", "0 800", "-1 5", "x 5", "nan 5", "inf 5"):
        message = _error(env["run"](f"bv point {bad}"))
        assert message.startswith("POINT_OUT_OF_VIEW: x must be 0–1279, y 0–799"), (bad, message)
    for bad in ("1001 0", "0 -1", "abc 1"):
        message = _error(env["run"](f"bv point1k {bad}"))
        assert message.startswith("POINT_OUT_OF_VIEW: x must be 0–1000, y 0–1000"), (bad, message)
    assert _error(env["run"]("bv point 5")) == "USAGE: bv point <x> <y>"
    assert _error(env["run"]("bv point1k 1 2 3")) == "USAGE: bv point1k <x> <y>"
    assert _hovers(env) == []


def test_click_without_an_argument_clicks_at_the_pointer(env) -> None:
    env["run"]("bv open example.com")
    assert _error(env["run"]("bv click")) == 'NO_POINTER: run "bv point x y" first'
    env["run"]("bv point 1000 720")
    clicked = env["run"]("bv click")
    assert clicked.ok, clicked.prompt_content
    assert [call for call in env["host"].calls if call[0] == "click"] == [("click", 1000.0, 720.0)]
    assert 'clicked button "Sign in" at (1000, 720)' in clicked.prompt_content
    assert _cursor_at(clicked, 1000, 720)  # same page: the pointer stays


def test_a_mark_or_region_click_moves_the_pointer_to_where_it_clicked(env) -> None:
    env["run"]("bv open example.com")
    clicked = env["run"]("bv click @1")
    assert _cursor_at(clicked, 120, 50)
    env["run"]("bv click")
    assert [call for call in env["host"].calls if call[0] == "click"][-1] == ("click", 120.0, 50.0)
    env["run"]("bv click 9")
    env["run"]("bv click")
    assert [call for call in env["host"].calls if call[0] == "click"][-1] == (
        "click", pytest.approx(2560 / 3 + 1280 / 6), pytest.approx(1600 / 3 + 800 / 6),
    )


@pytest.mark.parametrize(("command", "body"), [
    ("bv view", None),
    ("bv zoom 5", None),
    ("bv key Tab", None),
    ("bv type --enter", "hello"),
    ("bv select @3 Banana", None),
])
def test_the_pointer_is_kept_while_the_page_stays(env, command, body) -> None:
    env["run"]("bv open example.com")
    env["run"]("bv point 1000 720")
    result = env["run"](command, body=body)
    assert result.ok, result.prompt_content
    if command != "bv zoom 5":  # the zoomed region does not contain the pointer
        assert _cursor_at(result, 1000, 720)
    env["run"]("bv click")
    assert [call for call in env["host"].calls if call[0] == "click"][-1] == ("click", 1000.0, 720.0)


@pytest.mark.parametrize("commands", [
    ["bv open example.org"],
    ["bv back"],
    ["bv scroll down"],
    ["bv window tablet"],
    ["bv zoom 5", "bv point 10 10", "bv zoom out"],
    ["bv zoom 5", "bv point 10 10", "bv zoom reset"],
])
def test_the_pointer_is_cleared_when_the_page_moves_under_it(env, commands) -> None:
    env["run"]("bv open example.com")
    env["run"]("bv point 1000 720")
    for command in commands:
        result = env["run"](command)
        assert result.ok, result.prompt_content
    assert not _cursor_at(result, 1000, 720)
    assert _error(env["run"]("bv click")) == 'NO_POINTER: run "bv point x y" first'


def test_a_click_that_navigates_clears_the_pointer(env) -> None:
    env["run"]("bv open example.com")
    env["run"]("bv point 1000 720")
    env["host"].click_navigates_to = "https://example.com/next"
    moved = env["run"]("bv click")
    assert moved.ok and "url: https://example.com/next" in moved.prompt_content
    assert not _cursor_at(moved, 1000, 720)
    assert _error(env["run"]("bv click")) == 'NO_POINTER: run "bv point x y" first'


def test_every_screenshot_states_its_image_size(env) -> None:
    lines = env["run"]("bv open example.com").prompt_content.splitlines()
    assert "image 1280x800" in [line.strip() for line in lines]


# ─── one-line summaries for superseded results (R22) ───


def test_each_screenshot_result_has_a_one_line_summary(env) -> None:
    opened = env["run"]("bv open https://example.com/a/")
    assert opened.summary == "bv open https://example.com/a/ → example.com/a"
    clicked = env["run"]("bv click @1")
    assert clicked.summary == 'bv click @1 → example.com/a; clicked button "Sign in"'
    typed = env["run"]("bv type @2", body="me@example.com")
    assert typed.summary == 'bv type @2 → example.com/a; clicked button "Sign in"'
    env["host"].next_outcome = ActionOutcome(downloads=(
        DownloadResult(file_name="report.pdf", size=12, error=None),
        DownloadResult(file_name=None, size=None, error="big.zip: did not finish within 120s"),
    ))
    downloaded = env["run"]("bv key Enter")
    assert downloaded.summary == "bv key Enter → example.com/a; downloaded report.pdf"
    for result in (opened, clicked, typed, downloaded):
        assert "\n" not in result.summary and "[@" not in result.summary
    # Results without a screenshot have nothing to collapse.
    assert env["run"]("bv status").summary is None
    assert env["run"]("bv click @9").summary is None


# ─── bv wait (R24) ───


def _waits(env) -> list[tuple]:
    return [call for call in env["host"].calls if call[0] == "wait"]


def test_wait_reports_a_change_that_settled_and_screenshots_the_full_page(env) -> None:
    env["run"]("bv open example.com")
    env["run"]("bv zoom 5")
    env["host"].next_wait = WaitOutcome(changed=True, elapsed_s=7.23, settled=True)
    waited = env["run"]("bv wait 10")
    assert waited.ok, waited.prompt_content
    assert _waits(env) == [("wait", 10.0)]
    assert "wait: page changed after 7.2s (settled)" in waited.prompt_content
    assert "view: full page" in waited.prompt_content  # the zoom resets, as for any action
    assert len(waited.image_paths) == 1
    assert waited.summary == "bv wait 10 → example.com"


def test_wait_reports_a_page_still_changing_and_no_change(env) -> None:
    env["run"]("bv open example.com")
    env["host"].next_wait = WaitOutcome(changed=True, elapsed_s=30.0, settled=False)
    assert "wait: page changed after 30.0s (still changing)" in env["run"]("bv wait").prompt_content
    env["host"].next_wait = WaitOutcome(changed=False, elapsed_s=30.02, settled=False)
    assert "wait: no change after 30.0s" in env["run"]("bv wait").prompt_content


def test_wait_defaults_to_the_manifest_value(env) -> None:
    env["run"]("bv open example.com")
    env["run"]("bv wait")
    assert _ENTRY.manifest.defaults["wait_default_s"] == 30
    assert _waits(env) == [("wait", 30.0)]
    env["run"]("bv wait 0.5")
    assert _waits(env)[-1] == ("wait", 0.5)


def test_wait_says_browsing_only_when_the_url_changed(env) -> None:
    env["run"]("bv open example.com")
    assert _lines(env["run"]("bv wait 1")) == []
    env["host"].wait_navigates_to = "https://example.com/report"
    env["host"].next_wait = WaitOutcome(changed=True, elapsed_s=2.5, settled=True)
    assert _lines(env["run"]("bv wait 5")) == ["Browsing example.com/report"]


def test_wait_lists_downloads_the_page_started(env) -> None:
    env["run"]("bv open example.com")
    env["host"].next_wait = WaitOutcome(
        changed=True, elapsed_s=3.0, settled=True,
        downloads=(DownloadResult(file_name="report.pdf", size=12, error=None),),
    )
    waited = env["run"]("bv wait")
    assert "downloaded: /me/downloads/report.pdf (12 bytes)" in waited.prompt_content
    assert _lines(waited) == ["Downloaded report.pdf to /me/downloads"]


def test_a_bad_wait_argument_names_the_form(env) -> None:
    env["run"]("bv open example.com")
    for bad in ("0", "-3", "soon", "nan", "inf"):
        message = _error(env["run"](f"bv wait {bad}"))
        assert message == f"INVALID_ARGUMENT: bv wait [<seconds>]: seconds must be a positive number, got {bad!r}", bad
    assert _error(env["run"]("bv wait 1 2")) == "USAGE: bv wait [<seconds>]"
    assert _waits(env) == []


def test_wait_needs_a_page(env) -> None:
    assert _error(env["run"]("bv wait")) == 'NO_PAGE: run "bv open <url>" first'


# ─── keypad off by default, overlay status line (R27) ───


def _dark(image: Image.Image, box: tuple[int, int, int, int]) -> int:
    """How many pixels in ``box`` are dark on the fake page's near-white background."""
    crop = image.crop(box)
    return sum(1 for x in range(crop.width) for y in range(crop.height) if sum(crop.getpixel((x, y))) < 600)


def _keypad_drawn(result, *, width: int, height: int) -> tuple[bool, bool]:
    """``(lines, digits)``: whether the keypad's lines and its centre digit are on the image.

    Lines are probed on the left vertical line in the bottom third; the digit
    ``5`` sits in the centre region. The fake page's marks are all in the top
    left, away from both probes.
    """
    image = Image.open(Path(result.image_paths[0])).convert("RGB")
    assert image.size == (width, height)
    x = round(width / 3)
    lines = _dark(image, (x - 2, round(height * 0.8), x + 3, round(height * 0.8) + 20)) > 0
    cx, cy = width // 2, height // 2
    digits = _dark(image, (cx - 30, cy - 30, cx + 30, cy + 30)) > 0
    return lines, digits


def test_the_default_full_view_has_no_keypad(env) -> None:
    assert _ENTRY.manifest.defaults["grid_default"] is False
    assert _ENTRY.manifest.defaults["marks_default"] is True
    opened = env["run"]("bv open example.com")
    assert _keypad_drawn(opened, width=1280, height=800) == (False, False)
    assert "overlays: marks on · 3×3 grid off (bv view --grid on to add it)" in opened.prompt_content.splitlines()


def test_a_zoomed_view_draws_the_keypad_with_the_grid_off(env) -> None:
    env["run"]("bv open example.com")
    zoomed = env["run"]("bv zoom 5")
    assert _keypad_drawn(zoomed, width=1568, height=980) == (True, True)
    assert "overlays: marks on · 3×3 grid shown while zoomed (bv zoom reset for the full page)" in (
        zoomed.prompt_content.splitlines()
    )
    assert "grid: off" in env["run"]("bv status").prompt_content  # the sticky setting is untouched


def test_the_sticky_grid_on_draws_the_full_view_keypad(env) -> None:
    env["run"]("bv open example.com")
    shown = env["run"]("bv view --grid on")
    assert _keypad_drawn(shown, width=1280, height=800) == (True, True)
    assert "overlays: marks on · 3×3 grid on (bv view --grid off to hide it)" in shown.prompt_content.splitlines()
    after = env["run"]("bv key Tab")
    assert _keypad_drawn(after, width=1280, height=800) == (True, True)


def test_marks_off_is_stated_in_the_overlay_line(env) -> None:
    env["run"]("bv open example.com")
    hidden = env["run"]("bv view --marks off")
    assert (
        "overlays: marks off (bv view --marks on to show them) · 3×3 grid off (bv view --grid on to add it)"
        in hidden.prompt_content.splitlines()
    )


def test_a_region_click_works_at_full_page_with_the_grid_off_and_says_so(env) -> None:
    env["run"]("bv open example.com")
    clicked = env["run"]("bv click 5")
    assert clicked.ok, clicked.prompt_content
    assert [call for call in env["host"].calls if call[0] == "click"] == [("click", 640.0, 400.0)]
    assert (
        "keypad region 5 of the full page (the 3×3 grid is hidden; its regions still apply)"
        in clicked.prompt_content.splitlines()
    )
    # With the grid drawn, or zoomed, the note is not needed.
    env["run"]("bv view --grid on")
    assert "grid is hidden" not in env["run"]("bv click 5").prompt_content
    env["run"]("bv view --grid off")
    env["run"]("bv zoom 5")
    assert "grid is hidden" not in env["run"]("bv click 5").prompt_content


# ─── R28–R30: screenshots, the monitor and the prompt follow the live session ───


def _shots_dir(env) -> Path:
    return env["tmp"] / "data" / "shots" / env["agent"].id


def _marker_path(env) -> Path:
    return env["tmp"] / "data" / "sessions" / f"{env['agent'].id}.json"


def test_screenshots_are_stored_under_the_session_id(env) -> None:
    result = env["run"]("bv open example.com")
    assert Path(result.image_paths[0]).parent == _shots_dir(env) / "session1"


def test_close_deletes_the_sessions_screenshots_and_marker(env) -> None:
    opened = env["run"]("bv open example.com")
    assert Path(opened.image_paths[0]).is_file() and _marker_path(env).is_file()
    env["run"]("bv close")
    assert not Path(opened.image_paths[0]).exists()
    assert not _shots_dir(env).exists()
    assert not _marker_path(env).exists()


def test_a_window_swap_keeps_the_session_folder(env) -> None:
    opened = env["run"]("bv open example.com")
    swapped = env["run"]("bv window phone")
    assert Path(opened.image_paths[0]).is_file()
    assert Path(swapped.image_paths[0]).parent == Path(opened.image_paths[0]).parent


def test_shutdown_deletes_every_session_this_process_has_open(env) -> None:
    opened = env["run"]("bv open example.com")
    env["extension"].shutdown()
    assert ("shutdown",) in env["host"].calls
    assert not Path(opened.image_paths[0]).exists() and not _marker_path(env).exists()


def _leftovers(data_dir: Path) -> tuple[Path, Path]:
    shot = data_dir / "shots" / "agent-old" / "dead-session" / "20260928T100000000000Z.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"old")
    marker = data_dir / "sessions" / "agent-old.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    return shot, marker


@pytest.mark.parametrize("worker", [True, False])
def test_only_the_runtime_worker_clears_what_an_earlier_worker_left(tmp_path: Path, worker: bool) -> None:
    shot, marker = _leftovers(tmp_path / "data")
    _PACKAGE.BrowserVisionExtension(
        ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path / "data", runtime_worker=worker),
        host=FakeHost(),
        install_dir=tmp_path,
    )
    assert shot.exists() is not worker and marker.exists() is not worker
    assert (tmp_path / "data" / "shots").is_dir()  # the shots root itself stays


def test_the_loader_passes_the_worker_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.extensions import loader

    seen: list[bool] = []

    class _Instance:
        def live_view(self):
            return []

    class _Module:
        @staticmethod
        def create(ctx):
            seen.append(ctx.runtime_worker)
            return _Instance()

    monkeypatch.setattr(loader, "import_package", lambda entry: _Module)
    for flag, expected in (("1", True), (None, False)):
        monkeypatch.setattr(loader, "_loaded", {})
        monkeypatch.setattr(loader, "_contract_failures", {})
        if flag is None:
            monkeypatch.delenv("BOSSMOD_RUNTIME_WORKER", raising=False)
        else:
            monkeypatch.setenv("BOSSMOD_RUNTIME_WORKER", flag)
        loader.load_extension(_ENTRY)
        assert seen[-1] is expected


def test_the_marker_follows_the_session(env) -> None:
    env["run"]("bv open example.com")
    marker = json.loads(_marker_path(env).read_text(encoding="utf-8"))
    assert marker == {
        "session_id": "session1", "pid": os.getpid(),
        "opened_at": "2026-09-28T12:00:00+00:00", "url": "https://example.com",
    }
    env["host"].click_navigates_to = "https://example.com/next"
    env["run"]("bv click @1")
    assert json.loads(_marker_path(env).read_text(encoding="utf-8"))["url"] == "https://example.com/next"


def test_live_view_lists_a_live_session_with_a_screenshot(env) -> None:
    opened = env["run"]("bv open example.com")
    items = env["extension"].live_view()
    assert [(item.agent_id, item.image_path) for item in items] == [(env["agent"].id, Path(opened.image_paths[0]))]


def test_live_view_skips_an_agent_without_a_marker(env) -> None:
    env["run"]("bv open example.com")
    _marker_path(env).unlink()
    assert env["extension"].live_view() == []


def test_live_view_skips_a_dead_process_and_warns_once(env, caplog) -> None:
    import subprocess
    import sys

    env["run"]("bv open example.com")
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    marker = json.loads(_marker_path(env).read_text(encoding="utf-8"))
    _marker_path(env).write_text(json.dumps({**marker, "pid": finished.pid}), encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert env["extension"].live_view() == []
        assert env["extension"].live_view() == []

    stale = [r for r in caplog.records if "stale session marker" in r.getMessage()]
    assert len(stale) == 1 and f"process {finished.pid} is gone" in stale[0].getMessage()
    assert _marker_path(env).is_file()  # never deleted by the reader


def test_live_view_skips_a_marker_whose_session_has_no_screenshot(env) -> None:
    env["host"].goto_error = "net::ERR_NAME_NOT_RESOLVED"
    assert not env["run"]("bv open nowhere.invalid").ok
    assert _marker_path(env).is_file()  # the session is open, with no page shown
    assert env["extension"].live_view() == []


def test_the_prompt_state_follows_the_session(env) -> None:
    agent = env["agent"]
    assert env["extension"].prompt_state(agent) == "Your browser right now: no page open."
    env["run"]("bv open example.com/path")
    assert env["extension"].prompt_state(agent) == "Your browser right now: open at example.com/path (window desktop)."
    env["run"]("bv window phone")
    assert env["extension"].prompt_state(agent) == "Your browser right now: open at example.com/path (window phone)."
    env["run"]("bv close")
    assert env["extension"].prompt_state(agent) == "Your browser right now: no page open."


def test_asking_the_prompt_state_does_not_start_the_browser(tmp_path: Path) -> None:
    import threading

    extension = _PACKAGE.BrowserVisionExtension(
        ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path / "data"),
        install_dir=tmp_path,
    )
    agent = db.create_agent("Iris", role="Researcher")
    assert extension.prompt_state(agent) == "Your browser right now: no page open."
    assert extension._host._thread is None and extension._host._loop is None
    assert not any(thread.name == "browser-vision" for thread in threading.enumerate())


# ─── polite pacing per site (R33) ───


def _paced(host) -> list[str]:
    return [call[1] for call in host.calls if call[0] == "pace"]


def test_only_actions_that_can_reach_the_network_are_paced(env) -> None:
    run, host = env["run"], env["host"]
    assert run("bv open https://www.example.com/start").ok
    assert _paced(host) == ["example.com"]
    host.calls.clear()
    for command in ("bv view", "bv zoom 5", "bv zoom reset", "bv point 10 10", "bv point1k 5 5",
                    "bv scroll down", "bv wait 1", "bv status", "bv type @2 no submit", "bv window phone"):
        assert run(command).ok, command
    assert _paced(host) == []
    for command, body in (("bv click @1", None), ("bv click", None), ("bv click 5", None),
                          ("bv type @2 --enter", "hi"), ("bv key Enter", None), ("bv select @3 Banana", None),
                          ("bv back", None)):
        assert run(command, body).ok, command
    assert _paced(host) == ["example.com"] * 7
    assert run("bv close").ok
    assert _paced(host) == ["example.com"] * 7


def test_a_noticeable_wait_is_stated_in_the_result(env) -> None:
    run, host = env["run"], env["host"]
    host.next_pace_s = 2.84
    opened = run("bv open redfin.com")
    assert "paced 2.8s for redfin.com (polite browsing)" in opened.prompt_content
    host.next_pace_s = 0.05
    assert "paced" not in run("bv click @1").prompt_content


def test_a_page_without_a_site_is_not_paced(env) -> None:
    assert env["run"]("bv open about:blank").ok
    assert env["run"]("bv click @1").ok
    assert _paced(env["host"]) == []


# ─── bot checks and cooldowns (R34) ───


def _cooldowns(env) -> dict:
    return json.loads((env["tmp"] / "data" / "cooldowns.json").read_text(encoding="utf-8"))


def test_a_bot_check_says_blocked_and_cools_down_the_requested_site(env) -> None:
    run, host = env["run"], env["host"]
    host.nav_status = 429
    original_goto = host.goto

    def goto_wall(agent_id, url):
        outcome = original_goto(agent_id, url)
        host.url = "https://ratelimited.redfin.com/?rl-reason=blocked"
        return outcome

    host.goto = goto_wall
    blocked = run("bv open https://www.redfin.com/FL/Miami/sold-homes")
    assert blocked.ok, blocked.prompt_content
    assert len(blocked.image_paths) == 1
    lines = blocked.prompt_content.splitlines()
    # The BLOCKED line leads the browser section.
    assert lines[lines.index("BROWSER:") + 1] == (
        "BLOCKED: redfin.com is showing a bot check (HTTP 429). Stop browsing this site and tell the "
        "operator; do not retry or switch tools to get around it."
    )
    assert blocked.data["status_lines"] == ["Blocked by redfin.com's bot check"]
    assert list(_cooldowns(env)) == ["redfin.com"]


def test_a_site_on_cooldown_is_refused_without_loading_even_by_subdomain(env) -> None:
    run, host = env["run"], env["host"]
    host.text = "Are you a\nrobot?"
    assert "BLOCKED: redfin.com" in run("bv open redfin.com").prompt_content
    entry = _cooldowns(env)["redfin.com"]
    at = datetime.fromisoformat(entry["blocked_at"]).astimezone().strftime("%H:%M")
    until = datetime.fromisoformat(entry["until"]).astimezone().strftime("%H:%M")
    assert datetime.fromisoformat(entry["until"]) - datetime.fromisoformat(entry["blocked_at"]) == timedelta(minutes=30)
    host.calls.clear()
    for url in ("https://www.redfin.com/", "ratelimited.redfin.com"):
        refused = run(f"bv open {url}")
        assert _error(refused) == (
            f"SITE_COOLDOWN: redfin.com showed a bot check at {at}; it can be tried again after {until} (local time)"
        )
        assert "status_lines" not in refused.data
    # The open page is still redfin.com, so its paced actions are refused too.
    assert _error(run("bv click @1")).startswith("SITE_COOLDOWN: redfin.com")
    assert [call[0] for call in host.calls if call[0] != "describe"] == []
    # Another site is untouched.
    host.text = ""
    assert run("bv open example.com").ok


def test_the_cooldown_survives_a_new_extension_instance(env, tmp_path: Path) -> None:
    env["host"].frame_urls = ("https://www.google.com/recaptcha/api2/anchor?k=x",)
    assert "BLOCKED: example.com is showing a bot check (challenge frame from google.com/recaptcha)" in (
        env["run"]("bv open example.com").prompt_content
    )
    host = FakeHost()
    fresh = _PACKAGE.BrowserVisionExtension(
        ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path / "data"),
        host=host,
        install_dir=env["install"],
        vision_model=lambda agent: ("vision-model", True),
        downloads_dir=lambda agent: tmp_path / "downloads",
    )
    ctx = CliExecutionContext(agent=env["agent"], state=db.get_agent_state(env["agent"].id), cwd="/me")
    refused = fresh.handle(ctx, ParsedCliCommand(raw="bv open example.com", name="bv", args=("open", "example.com")), None)
    assert refused.data["error"].startswith("SITE_COOLDOWN: example.com showed a bot check at ")
    assert host.calls == []


def test_a_normal_page_is_not_a_bot_check(env) -> None:
    env["host"].nav_status = 200
    env["host"].text = "Robot vacuum review: the best robot for pet hair"
    opened = env["run"]("bv open example.com")
    assert "BLOCKED" not in opened.prompt_content
    assert opened.data["status_lines"] == ["Browsing example.com"]
    assert not (env["tmp"] / "data" / "cooldowns.json").exists()


def test_an_unreadable_cooldown_file_is_an_explicit_error(env) -> None:
    (env["tmp"] / "data").mkdir(parents=True, exist_ok=True)
    (env["tmp"] / "data" / "cooldowns.json").write_text("{not json", encoding="utf-8")
    assert _error(env["run"]("bv open example.com")).startswith("COOLDOWN_FILE_UNREADABLE: ")
    assert env["host"].calls == []
