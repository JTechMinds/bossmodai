"""The roster rail — status precedence, search, select mode, thread copy.

Three modules since the visual-parity pass: shell/roster.js assembles,
shell/roster-people.js is the People half, shell/roster-threads.js the
Threads half. The harness drives the assembled rail, so every property here
is proven against all three at once.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_roster_harness.cjs"

MODULES = [
    ("core", "dom.js"), ("core", "avatar.js"), ("core", "store.js"), ("core", "bus.js"),
    ("core", "format.js"),
    ("core", "agent-status.js"), ("shell", "roster-row-meta.js"),
    ("shell", "roster-people.js"),
    ("shell", "thread-create.js"),
    ("shell", "roster-threads.js"), ("shell", "roster.js"),
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


def _people_source() -> str:
    return (JS / "shell" / "roster-people.js").read_text(encoding="utf-8")


def test_status_line_precedence_is_paused_then_needs_then_status() -> None:
    """Paused wins over an open need, which wins over the agent's own status."""
    payload = _run_harness()
    assert payload["pausedBeatsNeedBeatsStatus"] is True
    assert payload["usesSharedStatusLabel"] is True
    # statusLine() travelled with the People half when the rail was split; the
    # property is unchanged — it reads the shared helper, never a local copy.
    assert "BossModAgentStatus.getStatusLabel(" in _people_source(), (
        "the status label must come from the shared helper, not a local copy"
    )
    assert "BossModRosterPeople.createPeople(" in _source()


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

    Phase 2B split the rail and the copy travelled with the Threads half; the
    polish round split that half again, so the copy now lives in
    shell/thread-create.js. The property — the roster rail, not a dock-era
    pane, owns thread creation — is unchanged, and the chain from the rail to
    the copy is asserted rather than assumed.
    """
    source = (JS / "shell" / "thread-create.js").read_text(encoding="utf-8")
    # Re-pointed in the visual-parity pass: the one permanent "Create Thread"
    # button became two states — `New thread` opens select mode, `Create with N`
    # closes it. Re-pointed again in the polish round: `New thread` is the `+`
    # on the section header, so the copy is its accessible name. And again in
    # round three, which moved the confirm onto that same header row: it is
    # icon-only too, so `Create thread` is its accessible name and the count it
    # used to carry is the header's middle slot. Round four deleted the
    # invitation the slot showed at rest — it truncated at rail width and
    # explained a mode nobody was in — so the copy this module owns is the two
    # names and the count, and the count is the whole of the middle slot.
    assert "'New thread'" in source
    assert "'Create thread'" in source
    assert "${count} selected" in source
    assert "start a shared thread" not in source
    # People owns the selection a thread is created from; this only reads it.
    assert "deps.getSelection" in source
    # rail -> Threads -> creation. Each link named, so a broken one is loud.
    assert "BossModRosterThreads.createThreads(" in _source()
    assert "BossModThreadCreate.createThreadControls(" in _threads_source()
