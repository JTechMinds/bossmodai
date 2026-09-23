"""The toolbar's two shared controls: the search field and the menu-select.

Both replaced hand-built copies. The search field is one bordered box holding
a magnifier and a borderless input. The dropdown is the app's own anchored
menu (core/overlays.js createMenu) with `.menu-action` rows — the panel a
click on an agent's name in a chat opens — instead of a native <select>,
which WebKitGTK paints as a grey GTK button that no toolbar row could match.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
HARNESS = Path(__file__).resolve().parent / "js_toolbar_controls_harness.cjs"

MODULES = [
    JS / "core" / "dom.js", JS / "core" / "avatar.js",
    JS / "core" / "overlay-focus.js", JS / "core" / "overlays.js", JS / "core" / "menu.js",
    JS / "core" / "search-field.js", JS / "core" / "menu-select.js",
]

# Every toolbar that searches or filters, and which control each one uses.
SEARCH_CALLERS = (
    "places/tasks/tasks-toolbar.js", "places/tasks/tasks-archive.js", "places/log/log-filters.js",
)
SELECT_CALLERS = ("places/tasks/tasks-toolbar.js", "places/log/log-filters.js")


def _read(relative: str) -> str:
    return (JS / relative).read_text(encoding="utf-8")


def test_toolbar_controls_harness() -> None:
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "searchIsOneBox": True,
        "searchInputIsNamed": True,
        "searchReportsInput": True,
        "searchNeedsItsParts": True,
        "triggerNamesTheChoice": True,
        "opensTheSharedMenu": True,
        "opensOnTheChoice": True,
        "rowsCarryAvatars": True,
        "pickReportsAndCloses": True,
        "reopensOnTheChoice": True,
        "samePickIsSilent": True,
        "triggerToggles": True,
        "setOptionsMovesTheChoice": True,
        "unknownChoiceIsRefused": True,
        "badOptionsThrow": True,
    }


def test_toolbars_use_the_shared_controls() -> None:
    """No toolbar hand-builds a search input or a native <select> again."""
    for relative in SEARCH_CALLERS:
        source = _read(relative)
        assert "BossModSearchField.create(" in source, relative
        assert "type: 'search'" not in source, f"{relative} builds its own search input"
    for relative in SELECT_CALLERS:
        source = _read(relative)
        assert "BossModMenuSelect.create(" in source, relative
        assert "h('select'" not in source, f"{relative} builds a native <select>"
    # The classes the hand-built pair wore are gone from markup and styles.
    for relative in SEARCH_CALLERS:
        assert "place-search" not in _read(relative) and "place-select" not in _read(relative)
    places = (CSS / "places.css").read_text(encoding="utf-8")
    assert ".place-search" not in places and ".place-select" not in places


def test_the_controls_are_styled_once_and_escape_native_chrome() -> None:
    controls = (CSS / "controls.css").read_text(encoding="utf-8")
    box = controls.split(".search-field {", 1)[1].split("}", 1)[0]
    # The box carries the edge (the light resting field token) and the height the
    # dropdown trigger matches, so a toolbar's controls stand on one line.
    assert "border: 1px solid var(--line-field)" in box
    assert "min-height: 28px" in box
    # The box shows focus the way every text field does — light at rest, a
    # step to --line-control when focused, no ring (base.css) — so the glyph
    # sits inside the indicator.
    assert ".search-field:focus-within { border-color: var(--line-control); }" in controls
    field = controls.split(".search-field-input {", 1)[1].split("}", 1)[0]
    assert "appearance: none" in field and "border: 0" in field
    trigger = controls.split(".menu-select-trigger {", 1)[1].split("}", 1)[0]
    assert "min-height: 28px" in trigger
    assert ".menu-select { position: relative;" in controls, "the panel's positioned host"
    overlays = (CSS / "overlays.css").read_text(encoding="utf-8")
    panel = overlays.split('.menu[data-menu="select"] {', 1)[1].split("}", 1)[0]
    assert "overflow-y: auto" in panel, "a long roster scrolls inside the panel"
