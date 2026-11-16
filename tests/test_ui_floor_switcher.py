"""shell/floor-switcher.js — one trigger, every active floor, a panel that hangs off its host, the settings its `⋯` opens, and the Suspended floors door."""

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
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "avatar.js",
    JS / "core" / "search-field.js",
    JS / "core" / "inline-rename.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "modal-trail.js",
    JS / "core" / "overlay-actions.js",
    JS / "core" / "overlays.js",
    JS / "core" / "menu.js",
    JS / "core" / "menu-button.js",
    JS / "context" / "agent-api.js",
    JS / "shell" / "floor-scope.js",
    JS / "shell" / "floor-api.js",
    JS / "shell" / "floor-delete.js",
    JS / "shell" / "floor-suspend.js",
    JS / "shell" / "floor-picker.js",
    JS / "shell" / "floor-move-confirm.js",
    JS / "shell" / "floor-people.js",
    JS / "shell" / "floor-threads.js",
    JS / "shell" / "floor-projects.js",
    JS / "shell" / "floor-settings.js",
    JS / "shell" / "floor-suspended.js",
    JS / "shell" / "floor-switcher.js",
]


def test_floor_switcher_behaviour() -> None:
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "mountRequiresABus": True,
        "mountRequiresATakeover": True,
        "triggerNamesTheCurrentFloor": True,
        "floorsLoadIntoTheStore": True,
        "panelHangsOffTheSwitcherHost": True,
        "everyFloorIsARowWithItsCount": True,
        "noSuspendedDoorWhileNoneIs": True,
        "everyRowHasANamedSettingsButton": True,
        "pickingARowSetsTheCurrentFloor": True,
        # Moving to another floor, or creating one, leaves the Settings
        # takeover; re-picking the floor you are on, or a fallback to Lobby
        # because the floor was deleted elsewhere, does not.
        "pickingAFloorLeavesTheTakeover": True,
        "pickingTheCurrentFloorKeepsTheTakeover": True,
        "escInTheFieldReturnsToTheDoor": True,
        "newFloorCreatesAndMovesThere": True,
        "newFloorLeavesTheTakeover": True,
        "floorSettingsPrefillTheNameAndOfferDelete": True,
        "deleteLayerWaitsForTheAgentsChoice": True,
        "deleteLayerSaysTheFilesAreArchived": True,
        "deleteThemWarnsToBackUp": True,
        # Vacation ends the agents' work now (plan step 41).
        "sendThemHomeSaysTheirWorkEnds": True,
        "choosingEnablesDelete": True,
        "aRenameElsewhereRepaintsTheTrigger": True,
        "aDeleteElsewhereMovesTheOperatorToLobby": True,
        "aDeleteElsewhereLeavesTheTakeoverUp": True,
        # A suspended floor leaves the rows for the "Suspended floors" door;
        # the operator on it falls back to Lobby, which is not a choice.
        "aSuspendElsewhereSplitsTheListAndMovesTheOperatorToLobby": True,
        "aSuspendFallbackLeavesTheTakeoverUp": True,
        "suspendedFloorsAreADoorNotRows": True,
        "suspendedViewRequiresReloadFloors": True,
        "theDoorClosesThePanelAndOpensTheView": True,
        "theViewListsEachFloorInTheSettingsRows": True,
        "resumeIsBusyWhileItRuns": True,
        "aFailedResumeSaysWhyOnItsRow": True,
        # A resume reloads the list like Rename and Delete, so a down socket
        # cannot leave its row behind; a failed reload is said, not hidden.
        "aResumeReloadsTheListAndItsRowLeaves": True,
        "aResumeWhoseReloadFailsSaysSoAndStaysDone": True,
        "theLastResumeShowsTheEmptyState": True,
        "closingTheViewDrainsTheStore": True,
        "theDoorLeavesWithTheLastSuspendedFloor": True,
        "aMalformedBroadcastIsNotAdopted": True,
        "aListWithoutSuspendedAtOrAnActiveFloorIsNotAdopted": True,
        "resyncReReadsTheFloors": True,
        "destroyDrainsTheBus": True,
        "aFailedLoadIsShownAndLogged": True,
    }
