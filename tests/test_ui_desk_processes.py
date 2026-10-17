"""The desk's Processes section (context/desk-processes.js), through its node harness."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CONTEXT = JS / "context"
HARNESS = Path(__file__).resolve().parent / "js_desk_processes_harness.cjs"

# The order the harness evaluates its modules in (its NAMES list).
MODULES = [
    JS / "api-client.js",
    JS / "core" / "dom.js",
    JS / "core" / "format.js",
    JS / "core" / "gates.js",
    JS / "core" / "bus.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "modal-trail.js",
    JS / "core" / "overlay-actions.js",
    JS / "core" / "overlays.js",
    CONTEXT / "desk-processes.js",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desk_processes_renders_its_states_and_acts() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), *(str(path) for path in MODULES)],
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "loadingShows": True,
        "rowsRender": True,
        "rowsAreLabelled": True,
        "stopPosts": True,
        "logOpensWithTheTail": True,
        "activityRepaintsOnlyThisAgent": True,
        "destroyClosesTheLog": True,
        "emptySays": True,
        "errorSays": True,
        "retryRecovers": True,
        "refusedStopIsSaid": True,
        "refusesMissingDeps": True,
    }


def test_the_desk_mounts_the_section_under_schedules_and_loads_it() -> None:
    panel = _read(CONTEXT / "desk-panel.js")
    assert "BossModDeskProcesses.createDeskProcesses({ api, bus, agentId })" in panel
    assert panel.index("section('Schedules'") < panel.index("section('Processes', null, processes.element)")
    assert panel.index("section('Processes'") < panel.index("section('Files'")
    assert "processes.destroy();" in panel
    index = _read(ROOT / "ui" / "templates" / "index.html")
    assert index.index("js/context/desk-schedules.js") < index.index("js/context/desk-processes.js")
    assert index.index("js/context/desk-processes.js") < index.index("js/context/desk-panel.js")


def test_desk_processes_builds_nodes_and_uses_no_inline_style() -> None:
    source = _read(CONTEXT / "desk-processes.js")
    assert "innerHTML" not in source
    assert "style=" not in source and "style:" not in source and ".style." not in source
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", source) is None, "colours come from the theme tokens"
    assert len(source.splitlines()) < 400


def test_a_waiting_checkin_reads_as_a_question_not_an_error() -> None:
    css = _read(ROOT / "ui" / "static" / "css" / "context.css")
    assert '.desk-process-tone[data-tone="ask"] { font-size: 11px; color: var(--accent); }' in css
    assert '.desk-process-tone[data-tone="alert"]' not in css
    assert "--accent:" in _read(ROOT / "ui" / "static" / "css" / "tokens.css")
