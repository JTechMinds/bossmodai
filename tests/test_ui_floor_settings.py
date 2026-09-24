"""shell/floor-settings.js — People, Threads, Projects, the Add picker, and the move confirm."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_floor_settings_harness.cjs"

MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "store.js",
    JS / "core" / "format.js",
    JS / "core" / "avatar.js",
    JS / "core" / "search-field.js",
    JS / "core" / "inline-rename.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    JS / "core" / "menu.js",
    JS / "shell" / "floor-scope.js",
    JS / "shell" / "floor-api.js",
    JS / "shell" / "floor-delete.js",
    JS / "shell" / "floor-picker.js",
    JS / "shell" / "floor-move-confirm.js",
    JS / "shell" / "floor-people.js",
    JS / "shell" / "floor-threads.js",
    JS / "shell" / "floor-projects.js",
    JS / "shell" / "floor-settings.js",
]


def test_floor_settings_behaviour() -> None:
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "settingsRenderFourSectionsWithCounts": True,
        "lobbyHasNoFooterAndNoDelete": True,
        "nameRestsAsTextWithItsVisibleLabelAndNoButtons": True,
        "moveToMenuHangsOffTheRow": True,
        "pickerGroupsOtherFloorsAndWaits": True,
        "choosingEnablesNext": True,
        "confirmShowsEveryGroup": True,
        "uncheckingACompanionReplansWithItExcluded": True,
        "aStalePlanIsReReadAndSaid": True,
        "aMoveClosesBackToTheSettingsAndRefreshes": True,
        "otherFloorsOfferDeleteAsAHeadTool": True,
        "openingDoesNotFocusTheName": True,
        "clickOpensEditWithSaveAndCancel": True,
        "anEmptyNameIsRefused": True,
        "aFailureKeepsTheDraftAndSaysWhy": True,
        "enterSavesRetitlesAndRests": True,
        "escCancelsTheRenameNotTheSettings": True,
        "trashOpensTheDeleteLayerWithBack": True,
        "setTitleRenamesHeadCloseAndTheBackAbove": True,
        "closingDrainsTheStore": True,
    }
