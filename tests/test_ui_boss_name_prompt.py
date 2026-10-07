"""shell/boss-name-prompt.js — the one-time "What should your team call you?" dialog."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_boss_name_prompt_harness.cjs"
INDEX = ROOT / "ui" / "templates" / "index.html"

MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "modal-trail.js",
    JS / "core" / "overlay-actions.js",
    JS / "core" / "overlays.js",
    JS / "api-client.js",
    JS / "shell" / "boss-name-prompt.js",
]


def test_boss_name_prompt_behaviour() -> None:
    args = ["node", str(HARNESS)] + [str(path) for path in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "opensOnlyWhenUnsetAndUnprompted": True,
        "saveStoresTheTrimmedNameThenTheFlag": True,
        "notNowStoresOnlyTheFlag": True,
        "closeStoresOnlyTheFlag": True,
        "aRefusalIsShownInlineAndKeepsTheDraft": True,
        "escAfterARefusalStoresTheFlag": True,
        "aFailedReadRejects": True,
        "ok": True,
    }


def test_the_prompt_loads_before_the_shell_and_is_asked_once_at_boot() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert html.index("js/shell/boss-name-prompt.js") < html.index("js/shell/shell.js")
    shell = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    assert shell.count("BossModBossNamePrompt.maybeAsk(") == 1
    # Asked after the socket connects, so the frame is live underneath it.
    assert shell.index("socket.connect();") < shell.index("BossModBossNamePrompt.maybeAsk(")
