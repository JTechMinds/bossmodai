"""shell/floor-switcher.js — one trigger, every floor, a panel that hangs off its host."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_floor_switcher_harness.cjs"

MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "store.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    JS / "shell" / "floor-scope.js",
    JS / "shell" / "floor-api.js",
    JS / "shell" / "floor-edit.js",
    JS / "shell" / "floor-switcher.js",
]


def test_floor_switcher_behaviour() -> None:
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "triggerNamesTheCurrentFloor": True,
        "floorsLoadIntoTheStore": True,
        "panelHangsOffTheSwitcherHost": True,
        "everyFloorIsARowWithItsCount": True,
        "everyRowHasANamedEditButton": True,
        "pickingARowSetsTheCurrentFloor": True,
        "escInTheFieldReturnsToTheDoor": True,
        "newFloorCreatesAndMovesThere": True,
        "editFloorPrefillsTheNameAndOffersDelete": True,
        "deleteLayerWaitsForTheAgentsChoice": True,
        "choosingEnablesDelete": True,
        "aFailedLoadIsShownAndLogged": True,
    }
