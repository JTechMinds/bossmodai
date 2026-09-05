"""Center slot is exclusive: map OR one company view, never a squeezed column."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "ui" / "templates" / "index.html"
CSS = ROOT / "ui" / "static" / "css" / "style.css"
APP_JS = ROOT / "ui" / "static" / "js" / "app.js"

CENTER_PANE_IDS = (
    "canvas-container",
    "company-dashboard",
    "diagnostic-detail-panel",
)


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_panel_map_is_a_single_slot_not_a_flex_row() -> None:
    html = _html()
    match = re.search(r'<div id="panel-map"([^>]*)>', html)
    assert match, "panel-map missing"
    attrs = match.group(1)
    assert "flex items-center" not in attrs
    assert "center-pane" not in attrs


def test_center_panes_share_exclusive_hidden_class() -> None:
    html = _html()
    for pane_id in CENTER_PANE_IDS:
        match = re.search(rf'<div id="{pane_id}"([^>]*)>', html)
        assert match, pane_id
        assert "center-pane" in match.group(1), pane_id


def test_hidden_center_pane_overrides_tailwind_flex() -> None:
    css = _css()
    assert ".center-pane.hidden" in css
    hidden_rule = css.split(".center-pane.hidden", 1)[1].split("}", 1)[0]
    assert "display: none !important" in hidden_rule
    assert "position: absolute" not in css.split("Exclusive center pane", 1)[-1][:800]


def test_switch_center_mode_shows_one_pane() -> None:
    source = _app_js()
    assert "function showCenterPane(" in source
    assert "function switchCenterMode(" in source

    show = source.split("function showCenterPane(", 1)[1].split("function switchCenterMode(", 1)[0]
    assert "classList.toggle('hidden', id !== visibleId)" in show

    switch = source.split("function switchCenterMode(mode) {", 1)[1].split(
        "function switchCompanyTab(", 1
    )[0]
    assert "showCenterPane('canvas-container')" in switch
    assert "showCenterPane('company-dashboard')" in switch
    assert "canvasContainer.classList.add('hidden')" not in switch
    assert "position: absolute" not in switch
    assert "overlay" not in switch.lower()
