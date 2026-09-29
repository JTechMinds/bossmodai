"""Extensions dialog, Browser Vision status reader and viewer, in the fake DOM."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_extensions_harness.cjs"
MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "switch.js",
    JS / "extensions" / "extensions-api.js",
    JS / "extensions" / "browser-vision-status.js",
    JS / "extensions" / "extensions-live.js",
    JS / "extensions" / "extensions-dialog.js",
]


def _payload() -> dict:
    result = subprocess.run(
        ["node", str(HARNESS), *(str(path) for path in MODULES)],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_status_reader_viewer_and_card() -> None:
    payload = _payload()
    for key in (
        "cardHasNoWatchButton",
        "noFetchesWithoutSubscribers", "subscribingReadsAtOnce", "noPollingOverSixtySeconds",
        "anEventReadsExactlyOnce", "aNullEventReadsExactlyOnce", "anotherExtensionsEventReadsNothing",
        "aBurstOfFiveReadsAtMostTwice", "resyncReadsOnce",
        "failureKeepsLastStateAndLogs", "disabledIsAnEmptySet",
        "noSubscribersMeansNoFetches", "toggleRefreshesTheStatus",
        "viewerTitleAndHeadFocus", "viewerImageHasRealAlt", "viewerShowsEveryCaptionLine",
        "viewerDoesNotRefetchAnUnchangedShot", "viewerUpdatesOnANewShotAndRevokesTheOld",
        "viewerSaysSessionEnded", "viewerStopsReadingOnClose",
    ):
        assert payload.get(key) is True, (key, payload)


def test_the_status_module_has_no_timer() -> None:
    source = (JS / "extensions" / "browser-vision-status.js").read_text(encoding="utf-8")
    assert "setInterval" not in source and "setTimeout" not in source


def test_the_shell_wires_the_status_to_the_bus_before_the_socket() -> None:
    shell = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    assert "BossModBrowserVisionStatus.attach({ bus });" in shell
    assert shell.index("BossModBrowserVisionStatus.attach({ bus });") < shell.index("socket.connect();")


def test_the_live_tone_colours_the_icon_soft_green() -> None:
    css = (ROOT / "ui" / "static" / "css" / "conversation.css").read_text(encoding="utf-8")
    assert '.conversation-action[data-tone="live"] svg { color: var(--ok-mark); }' in css


def test_the_multi_agent_tile_layer_is_gone() -> None:
    live = (JS / "extensions" / "extensions-live.js").read_text(encoding="utf-8")
    dialog = (JS / "extensions" / "extensions-dialog.js").read_text(encoding="utf-8")
    assert "return { openForAgent, headerCapability };" in live
    assert "ext-live-tile" not in live and "market-cards" not in live
    assert "BossModExtensionsLive" not in dialog and "Watch" not in dialog
