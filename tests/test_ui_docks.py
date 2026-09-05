"""Company views are dockable windows, not a squeezed middle column."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "ui" / "templates" / "index.html"
CSS = ROOT / "ui" / "static" / "css" / "style.css"
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_dock_manager_harness.cjs"

DOCK_IDS = ("files", "tasks", "metrics", "org")


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _app_js() -> str:
    return (JS / "app.js").read_text(encoding="utf-8")


def test_workspace_is_not_a_flex_row_of_center_panes() -> None:
    html = _html()
    match = re.search(r'<div id="panel-map"([^>]*)>', html)
    assert match, "panel-map missing"
    attrs = match.group(1)
    assert "flex items-center" not in attrs
    assert "dock-workspace" in attrs
    assert 'id="dock-layer"' in html
    assert 'id="company-dashboard"' not in html
    assert 'id="company-dashboard-content"' not in html


def test_company_views_are_individual_docks() -> None:
    html = _html()
    for dock_id in DOCK_IDS:
        match = re.search(rf'<div id="dock-{dock_id}"([^>]*)>', html)
        assert match, dock_id
        attrs = match.group(1)
        assert "dock-window" in attrs
        assert "hidden" in attrs
        assert f'id="dock-{dock_id}-body"' in html


def test_activity_sidebar_stays_outside_docks() -> None:
    html = _html()
    assert 'id="panel-activity"' in html
    activity = html.split('id="panel-activity"', 1)[1]
    assert "dock-window" not in activity[:400]
    assert 'id="dock-layer"' in html.split('id="panel-map"', 1)[1].split('id="panel-activity"', 1)[0]


def test_dock_css_floats_over_map_not_as_column() -> None:
    css = _css()
    assert ".dock-window" in css
    window_rule = css.split("\n.dock-window {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in window_rule
    assert ".dock-window.hidden" in css
    hidden = css.split(".dock-window.hidden", 1)[1].split("}", 1)[0]
    assert "display: none !important" in hidden
    layer = css.split(".dock-layer {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in layer
    assert "pointer-events: none" in layer


def test_app_opens_docks_and_keeps_map_mounted() -> None:
    source = _app_js()
    assert "DockManager.init(" in source
    assert "DockManager.open(" in source
    assert "DockManager.closeAll(" in source
    switch = source.split("function switchCenterMode(mode) {", 1)[1].split(
        "function switchCompanyTab(", 1
    )[0]
    assert "canvasContainer.classList.remove('hidden')" in switch
    assert "showCenterPane(" not in source
    assert "prefs.docks" in source


def test_index_loads_dock_manager_before_app() -> None:
    html = _html()
    sources = re.findall(r"static_url\('([^']+)'\)", html)
    assert "js/dock-manager.js" in sources
    assert sources.index("js/company-dashboard.js") < sources.index("js/dock-manager.js")
    assert sources.index("js/dock-manager.js") < sources.index("js/app.js")


def test_dock_manager_harness_clamps_and_normalizes() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "dock-manager.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "dockIds": ["files", "tasks", "metrics", "org"],
        "clampedInBounds": True,
        "clampedMinSize": True,
        "filesOpen": True,
        "tasksClosed": True,
        "junkIgnored": True,
        "hasOpen": True,
    }
