"""core/tabs.js — the quiet tab group the Office and the Agents dialog share.

Extracted on the second occurrence: the Office header's Map | Org, and the
Agents dialog's Add agent | Marketplace. The behaviour is proved against the
real module in tests/js_tabs_harness.cjs; what stays here is that both
surfaces actually build through it and that the look moved with it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
HARNESS = Path(__file__).resolve().parent / "js_tabs_harness.cjs"

MODULES = [JS / "core" / "dom.js", JS / "core" / "tabs.js"]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_tabs_harness() -> None:
    """Click, arrows, Home/End, the roving tab stop, and every refusal.

    `onSelect` is the operator's choice alone: building the group, `select()`
    and a click on the tab already up must all stay silent, because the
    caller's show-the-pane work runs on it.
    """
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "theGroupIsANamedTablist": True,
        "everyTabIsAButtonThatNamesItsPanel": True,
        "oneSelectedAndOneTabStop": True,
        "aClickSelectsAndReports": True,
        "clickingTheSelectedTabIsSilent": True,
        "theArrowsWrapAndMoveTheKeyboard": True,
        "anArrowStepsOneTab": True,
        "homeAndEndGoToTheEnds": True,
        "otherKeysAreLeftAlone": True,
        "selectMovesTheSelectionSilently": True,
        "focusLandsOnTheSelectedTab": True,
        "theConstructorRefusesWhatItCannotBuild": True,
        "selectRefusesAnUnknownTab": True,
        "aTabMayLeadWithAnIcon": True,
    }


def test_both_surfaces_build_their_tabs_through_the_one_builder() -> None:
    """Two hand-built copies of a roving tabindex are two keyboards that drift."""
    office = _read(JS / "places" / "office" / "office-place.js")
    assert "BossModTabs.create({" in office
    assert "idPrefix: 'office-tab'," in office
    assert "onSelect: showPane," in office
    for gone in ("function buildTabs(", "function onTabKeydown(", "tabButtons", "role: 'tab'"):
        assert gone not in office, gone
    agents = _read(JS / "context" / "agents-dialog.js")
    assert "BossModTabs.create({" in agents
    assert "idPrefix: 'agents-tab'," in agents
    assert "role: 'tab'" not in agents


def test_the_look_moved_with_the_builder() -> None:
    """The rules are controls.css's now; only a row's placement stays with it."""
    controls = _read(CSS / "controls.css")
    for selector in (".tabs {", ".tab {", ".tab:hover {", '.tab[aria-selected="true"] {'):
        assert selector in controls, selector
    group = controls.split(".tabs {", 1)[1].split("}", 1)[0]
    assert "margin-left" not in group, "where a group sits is its row's business"
    places = _read(CSS / "places.css")
    assert ".office-tab" not in places
    assert ".office-header .tabs { margin-left: auto; }" in places
    # A tab that leads with an icon lays the two out in a row, the glyph at the
    # search field's 14px.
    tab = controls.split(".tab {", 1)[1].split("}", 1)[0]
    assert "display: inline-flex;" in tab and "gap: 6px;" in tab
    assert ".tab svg { width: 14px; height: 14px; flex: none; }" in controls
