"""shell/roster.js + roster-threads.js — status precedence, search, thread copy."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_roster_harness.cjs"

MODULES = [
    ("core", "dom.js"), ("core", "store.js"), ("core", "bus.js"),
    (".", "utils.js"), ("shell", "roster-threads.js"), ("shell", "roster.js"),
]


def _run_harness() -> dict:
    args = ["node", str(HARNESS)] + [str((JS / d / f).resolve()) for d, f in MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def _source() -> str:
    return (JS / "shell" / "roster.js").read_text(encoding="utf-8")


def _threads_source() -> str:
    return (JS / "shell" / "roster-threads.js").read_text(encoding="utf-8")


def test_status_line_precedence_is_paused_then_needs_then_status() -> None:
    """Paused wins over an open need, which wins over the agent's own status."""
    payload = _run_harness()
    assert payload["pausedBeatsNeedBeatsStatus"] is True
    assert payload["usesSharedStatusLabel"] is True
    assert "BossModUtils.getStatusLabel(" in _source(), (
        "the status label must come from the shared helper, not a local copy"
    )


def test_search_filters_on_name_and_role_and_preserves_the_caret() -> None:
    """Only the lists re-render, so typing never moves the operator's cursor."""
    payload = _run_harness()
    assert payload["searchMatchesNameAndRole"] is True
    assert payload["caretSurvivesRerender"] is True
    assert payload["disposersDrain"] is True


def test_creating_a_thread_clears_the_people_selection() -> None:
    """The selection is People's; the create button is Threads'.

    The reset crosses that seam, so nothing local to either half would catch
    its loss. Behavioural, through the harness.
    """
    assert _run_harness()["selectionClearsAfterCreate"] is True


def test_roster_owns_the_thread_creation_copy() -> None:
    """Moved from company-view.js; test_ui_channel_gaps.py points here from Task 14.

    Phase 2B split the rail: the copy travelled with the Threads half, so the
    subject is shell/roster-threads.js. The property — the roster rail, not a
    dock-era pane, owns thread creation — is unchanged.
    """
    source = _threads_source()
    assert "Create Thread" in source
    assert "start a shared thread" in source
    # People owns the selection a thread is created from; Threads only reads it.
    assert "deps.getSelection" in source
    assert "BossModRosterThreads.createThreads(" in _source()
