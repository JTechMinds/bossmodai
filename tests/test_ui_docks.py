"""Company views dock into named slots as tabs, not floating windows."""

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

SLOT_IDS = ("left", "center", "right")
PANE_IDS = ("focus", "map", "activity", "files", "tasks", "metrics", "org")
COMPANY_PANES = ("files", "tasks", "metrics", "org")


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _app_js() -> str:
    return (JS / "app.js").read_text(encoding="utf-8")


def test_named_slots_exist() -> None:
    html = _html()
    for slot_id in SLOT_IDS:
        match = re.search(rf'<div id="slot-{slot_id}"([^>]*)>', html)
        assert match, slot_id
        attrs = match.group(1)
        assert "dock-slot" in attrs
        assert f'data-slot="{slot_id}"' in attrs
        assert f'data-slot-tabs="{slot_id}"' in html
        assert f'data-slot-body="{slot_id}"' in html


def test_dockable_panes_are_not_floating_windows() -> None:
    html = _html()
    assert 'id="dock-layer"' not in html
    assert "dock-window" not in html
    for pane_id in PANE_IDS:
        assert f'data-pane="{pane_id}"' in html
    assert 'id="panel-left"' in html
    assert 'id="panel-map"' in html
    assert 'id="panel-activity"' in html
    assert 'id="company-dashboard"' not in html
    assert 'id="company-dashboard-content"' not in html
    for pane_id in COMPANY_PANES:
        assert f'id="dock-{pane_id}-body"' in html


def test_map_is_a_slot_pane_not_under_overlay_chrome() -> None:
    html = _html()
    map_chunk = html.split('id="panel-map"', 1)[1].split('id="slot-right"', 1)[0]
    assert 'data-pane="map"' in html.split('id="panel-map"', 1)[0][-200:] + html.split('id="panel-map"', 1)[1][:200]
    assert "dock-layer" not in map_chunk
    assert "dock-window" not in map_chunk
    assert 'id="canvas-container"' in map_chunk


def test_dock_css_is_slot_shell_not_absolute_windows() -> None:
    css = _css()
    assert ".dock-slot {" in css
    slot_rule = css.split(".dock-slot {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column" in slot_rule
    assert "position: absolute" not in slot_rule
    pane_rule = css.split("\n.dock-pane {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in pane_rule
    assert "inset: 0" in pane_rule
    assert ".dock-layer" not in css
    assert ".dock-window" not in css
    hidden = css.split(".dock-pane.hidden", 1)[1].split("}", 1)[0]
    assert "display: none !important" in hidden


def test_app_uses_slot_shell_and_keeps_map_mounted() -> None:
    source = _app_js()
    assert "DockManager.init(" in source
    assert "DockManager.open(" in source
    assert "DockManager.closeAll(" in source
    assert "DockManager.activate('map')" in source
    assert "Split(['#slot-left', '#slot-center', '#slot-right']" in source
    switch = source.split("function switchCenterMode(mode) {", 1)[1].split(
        "function switchCompanyTab(", 1
    )[0]
    assert "showCenterPane(" not in source
    assert "prefs.docks" in source
    assert "DockManager.closeAll()" in switch


def test_index_loads_dock_manager_before_app() -> None:
    html = _html()
    sources = re.findall(r"static_url\('([^']+)'\)", html)
    assert "js/dock-manager.js" in sources
    assert sources.index("js/company-dashboard.js") < sources.index("js/dock-manager.js")
    assert sources.index("js/dock-manager.js") < sources.index("js/app.js")


def test_dock_manager_harness_assigns_and_maximizes_to_tabs() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "dock-manager.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["slotIds"] == ["left", "center", "right"]
    assert payload["paneIds"] == [
        "focus",
        "map",
        "activity",
        "files",
        "tasks",
        "metrics",
        "org",
    ]
    assert payload["defaultLeft"] == {"panes": ["focus"], "active": "focus"}
    assert payload["defaultCenter"] == {"panes": ["map"], "active": "map"}
    assert payload["defaultRight"] == {"panes": ["activity"], "active": "activity"}
    assert payload["activityMovedLeft"] is True
    assert payload["mapMovedRight"] is True
    assert payload["filesOpenedCenter"] is True
    assert payload["maximizeIsTabs"] is True
    assert payload["filesClosed"] is True
    assert payload["companyClosed"] is True
    assert payload["coreStaysOpen"] is True
    assert payload["junkIgnored"] is True
    assert payload["migratedToCenterTabs"] is True
    assert payload["emptyDefaults"] is True
