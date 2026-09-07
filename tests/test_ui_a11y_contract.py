"""The shell's accessibility floor, enforced so Phase 2 cannot regress it quietly."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
SHELL = JS / "shell"
HARNESS = Path(__file__).resolve().parent / "js_a11y_harness.cjs"

HARNESS_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "utils.js",
    JS / "core" / "overlays.js",
    SHELL / "places.js",
    SHELL / "header.js",
    SHELL / "roster-threads.js",
    SHELL / "roster.js",
    SHELL / "footer.js",
]

# A gesture-only control has no keyboard equivalent (SC 2.1.1). None of them
# belong anywhere in the shell, least of all on the emergency stop.
HOLD_GESTURES = ("mousedown", "touchstart", "pointerdown", "pointerup", "touchend")

# Anything that would make a container an unbounded live region.
TRANSCRIPT_WORDS = ("log", "transcript", "message-list", "messages")


def _shell_sources() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(SHELL.glob("*.js"))}


def test_every_icon_only_control_in_the_shell_has_an_accessible_name() -> None:
    args = ["node", str(HARNESS)] + [str(path) for path in HARNESS_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"ok": True, "everyShellControlIsNamed": True}


def test_no_shell_control_is_bound_to_a_hold_gesture() -> None:
    for name, source in _shell_sources().items():
        for gesture in HOLD_GESTURES:
            assert gesture not in source, f"{name} binds the gesture-only event {gesture}"
    header = (SHELL / "header.js").read_text(encoding="utf-8")
    assert "BossModOverlays.createModal(" in header, (
        "Pause confirms through a focus-trapped dialog, not a hold"
    )
    assert "setTimeout" not in header, "no timed hold may creep back onto Pause"


def test_no_shell_source_makes_a_transcript_a_live_region() -> None:
    """Spec 4.2: the transcript is role="log" and never aria-live.

    Agents talk continuously; an aria-live message list narrates forever with
    no way to stop. Phase 2 owns the transcript, so the guard lands here first.
    """
    for name, source in _shell_sources().items():
        for index, _ in enumerate(source):
            if not source.startswith("aria-live", index):
                continue
            window = source[max(0, index - 200): index + 200].lower()
            for word in TRANSCRIPT_WORDS:
                assert word not in window, (
                    f"{name} puts aria-live on a '{word}' container; the transcript "
                    f'must be role="log" without aria-live'
                )


def test_transcript_is_a_log_not_a_live_region() -> None:
    """Agents talk continuously; narrating every message is worse than silence."""
    source = (JS / "conversation" / "transcript.js").read_text(encoding="utf-8")
    assert "role: 'log'" in source
    listing = source.split("class: 'transcript'", 1)[1].split("transcript-jump", 1)[0]
    assert "aria-live" not in listing
    assert "aria-live" in source  # the jump affordance announces the count
