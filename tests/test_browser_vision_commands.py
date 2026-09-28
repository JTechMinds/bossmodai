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
_marks_module = importlib.import_module(f"{_PACKAGE.__name__}.marks")
ActionOutcome = _host_module.ActionOutcome
Capture = _host_module.Capture
DownloadResult = _host_module.DownloadResult
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
        self.sessions: set[str] = set()
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

    def set_viewport(self, agent_id, viewport):
        self.calls.append(("set_viewport", viewport))
        if viewport.width is not None:
            self.size = (viewport.width, viewport.height)
        else:
            self.size = (390, 664)

    def capture(self, agent_id):
        width, height = self.size
        return Capture(png=_png(width, height), url=self.url, title="Fixture", width=width, height=height,
                       marks=self.marks)

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
    assert "view: full page" in text
    assert "marks: 3" in text
    assert '[1] button "Sign in"' in text
    assert '[2] textbox "Email" (empty)' in text
    assert '[3] select "Apple"' in text


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
    assert _error(env["run"]("bv fly")).startswith("USAGE: bv open|view|zoom|click|type|select|")
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
    env["run"]("bv view --grid-color #00ff00 --grid-opacity 0.3")
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
