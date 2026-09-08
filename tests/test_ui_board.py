"""The Board: a column map that is total, and a Done column that tells the truth.

Spec 6.3. The board is the only surface that claims to show every task, so the
first property it owes the operator is that it actually does.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
BOARD = JS / "places" / "board"
HARNESS = Path(__file__).resolve().parent / "js_board_harness.cjs"

# Load order matters: the column map before the data layer that reads it, the
# views before the place that mounts them.
HARNESS_MODULES = [
    JS / "core" / "dom.js", JS / "core" / "store.js", JS / "core" / "bus.js",
    JS / "core" / "format.js", JS / "core" / "specialty.js",
    JS / "core" / "gates.js", JS / "core" / "overlays.js",
    JS / "shell" / "places.js",
    BOARD / "board-columns.js", BOARD / "board-data.js", BOARD / "board-grid.js",
    BOARD / "task-card.js", BOARD / "task-deliverables.js", BOARD / "task-events.js",
    BOARD / "task-detail.js", BOARD / "assign-outcomes.js", BOARD / "assign-form.js",
    BOARD / "board-cancel.js", BOARD / "board-toolbar.js", BOARD / "board-place.js",
]

# Column ids, which are not statuses. Anything else the board names must be a
# real member of the engine's TaskStatus literal.
COLUMN_IDS = {"backlog", "working", "needs", "done", "closed"}


def _columns_source() -> str:
    return (BOARD / "board-columns.js").read_text(encoding="utf-8")


def _frozen_array(source: str, name: str) -> str:
    """The body of `const <name> = Object.freeze([ … ]);`."""
    head = f"const {name} = Object.freeze(["
    assert head in source, f"{name} must be exported as a frozen array of data"
    return source.split(head, 1)[1].split("]);", 1)[0]


def test_column_map_is_total_over_task_status() -> None:
    """Every engine status maps to exactly one column or the disclosure.

    TaskStatus has eleven members. An earlier draft of the spec listed ten;
    `delegated` mapped to no column, so a delegated task would have been in the
    database and invisible on the board. This test is the reason that cannot
    happen again — and it must fail if the engine adds a status.

    Both directions are asserted, plus uniqueness: a status placed in two
    columns would render the same task twice, which is the same class of lie as
    not rendering it at all.
    """
    model = (ROOT / "core" / "models" / "task.py").read_text(encoding="utf-8")
    literal = model.split("TaskStatus = Literal[", 1)[1].split("]", 1)[0]
    engine = set(re.findall(r'"([a-z]+)"', literal))
    assert len(engine) == 11, f"TaskStatus changed: {sorted(engine)}"

    js = _columns_source()
    columns = _frozen_array(js, "COLUMNS")
    closed = _frozen_array(js, "CLOSED_WITHOUT_COMPLETING")
    mapped = set(re.findall(r"'([a-z]+)'", columns + closed))
    board = mapped & engine

    assert engine - board == set(), f"statuses with no column: {sorted(engine - board)}"
    assert mapped - engine - COLUMN_IDS == set(), \
        "the board names a status the engine does not have"

    # Exactly one home each: every placement across all columns plus the
    # disclosure, counted with duplicates.
    placements = re.findall(r"statuses: \[([^\]]*)\]", columns)
    placed = [name for group in placements for name in re.findall(r"'([a-z_]+)'", group)]
    placed += re.findall(r"'([a-z_]+)'", closed)
    duplicates = sorted({name for name in placed if placed.count(name) > 1})
    assert duplicates == [], f"statuses placed in more than one column: {duplicates}"
    assert sorted(placed) == sorted(engine), (
        f"placements do not match TaskStatus exactly: {sorted(placed)}"
    )

    # The runtime net under the static guarantee: if a payload ever carries a
    # status this map does not know, it is surfaced, not dropped on the floor.
    data = (BOARD / "board-data.js").read_text(encoding="utf-8")
    place = (BOARD / "board-place.js").read_text(encoding="utf-8")
    assert "unplaced" in data and "return { columns, closed, unplaced };" in data
    assert "grouped.unplaced.length > 0" in place
    assert "role: 'alert'" in place.split("grouped.unplaced.length > 0", 1)[1][:300]


def test_done_column_is_complete_only() -> None:
    """Done means done. Cancelled, declined and abandoned are a separate fact.

    Reporting a cancelled task as done would misrepresent the outcome, which is
    the honesty rule ARCHIVE_HONESTY_COPY and doneClaimGuidance already enforce
    elsewhere. The three live in CLOSED_WITHOUT_COMPLETING and in no column.
    """
    js = _columns_source()
    columns = _frozen_array(js, "COLUMNS")

    done = re.search(r"id: 'done',.*?statuses: \[([^\]]*)\]", columns, re.S)
    assert done, "the board has no Done column"
    assert re.findall(r"'([a-z]+)'", done.group(1)) == ["complete"]

    closed = _frozen_array(js, "CLOSED_WITHOUT_COMPLETING")
    assert re.findall(r"'([a-z]+)'", closed) == ["abandoned", "declined", "cancelled"]

    groups = re.findall(r"statuses: \[([^\]]*)\]", columns)
    for status in ("abandoned", "declined", "cancelled"):
        for group in groups:
            assert f"'{status}'" not in group, f"{status} is in a column, not the disclosure"

    # TERMINAL_STATUSES still covers cancelled — it is what decides whether a
    # task can be cancelled at all, and company-tasks.js asserted the same.
    assert "const TERMINAL_STATUSES = Object.freeze(new Set([" in js
    terminal = js.split("const TERMINAL_STATUSES = Object.freeze(new Set([", 1)[1] \
        .split("]));", 1)[0]
    assert "CLOSED_WITHOUT_COMPLETING" in terminal, (
        "TERMINAL_STATUSES must be derived from the map, never written out again"
    )


def test_closed_without_completing_is_a_disclosure_not_a_column() -> None:
    """The three closed statuses render under Done, and never count as Done.

    Two independent halves, because either alone would let the lie back in:
    the grid builds the column heading from the column's own rows before the
    disclosure is appended, and the data layer tests `closed` BEFORE `terminal`
    so a cancelled task can never fall through into the done tally.
    """
    columns = _columns_source()
    grid = (BOARD / "board-grid.js").read_text(encoding="utf-8")
    data = (BOARD / "board-data.js").read_text(encoding="utf-8")

    assert "const CLOSED_LABEL = 'Closed without completing';" in columns
    assert "COLUMNS.CLOSED_LABEL" in grid

    body = grid.split("function disclosure(rows, renderCard) {", 1)[1]
    assert "h('details'" in body.split("\n    }", 1)[0], (
        "the closed rows must sit under a details element, not in a column"
    )

    render = grid.split("function renderGrid(", 1)[1]
    assert "const rows = grouped.columns[column.id] || [];" in render
    assert "header(column, rows.length)" in render
    assert render.index("header(column, rows.length)") < render.index("grouped.closed.length > 0"), (
        "the Done heading is counted before the disclosure is attached"
    )
    assert "grouped.closed" not in render.split("header(column, rows.length)", 1)[0]

    counts = data.split("function counts(tasks) {", 1)[1].split("\n    }", 1)[0]
    assert "isClosedWithoutCompleting(task.status)) totals.closed += 1" in counts
    assert counts.index("totals.closed += 1") < counts.index("totals.done += 1"), (
        "closed must be tested before terminal, or done absorbs cancelled tasks"
    )


def test_selection_and_bulk_cancel_are_keyboard_reachable_and_confirmed() -> None:
    """Multi-select and bulk cancel survived the table, with their edges intact.

    The checkbox is named and always in the tab order; the confirmation is the
    focus-trapped dialog rather than window.confirm, which cannot be styled,
    cannot be tested, and blocks the event loop.
    """
    card = (BOARD / "task-card.js").read_text(encoding="utf-8")
    place = (BOARD / "board-place.js").read_text(encoding="utf-8")
    toolbar = (BOARD / "board-toolbar.js").read_text(encoding="utf-8")
    css = (ROOT / "ui" / "static" / "css" / "places.css").read_text(encoding="utf-8")

    assert "h('label', { class: 'task-card-select' }" in card
    assert "'aria-label': `Select ${title}`" in card
    assert "type: 'checkbox'" in card

    # Revealed on hover AND on focus. Hover alone would hide it from anyone
    # driving the board by keyboard (SC 2.1.1, SC 2.4.7).
    assert ".task-card:focus-within .task-card-select" in css
    assert ".task-card-select { flex: 0 0 auto; opacity: 0; }" in css

    for path in sorted(BOARD.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert "window.confirm" not in text, f"{path.name} uses window.confirm"

    cancel = (BOARD / "board-cancel.js").read_text(encoding="utf-8")
    assert "BossModOverlays.createModal({" in cancel
    assert "Cancel selected" in toolbar
    assert "setSelectedCount" in toolbar
    assert "BossModOverlays.createModal(" not in place, (
        "the Board has one destructive path, and board-cancel.js owns it"
    )
    confirm = cancel.split("function confirmCancel(title, ids) {", 1)[1].split("\n        }", 1)[0]
    assert "BossModOverlays.createModal({" in confirm
    assert "if (ids.length === 0) return;" in confirm, (
        "an empty selection must not open a dialog that would cancel nothing"
    )


def test_assign_form_reuses_the_shared_specialty_helpers() -> None:
    """One opinion about whether an assignment is sensible, not two.

    The ranking, the match labels, and the warning copy all live in
    core/specialty.js.
    A local copy here would be a second opinion that drifts, and the operator
    would have no way to tell which one they were reading.
    """
    source = (BOARD / "assign-form.js").read_text(encoding="utf-8")
    assert "BossModSpecialty.specialtyRank(" in source
    assert "BossModSpecialty.specialtyWarningMessage(" in source
    assert "BossModSpecialty.specialtyMatch(" in source
    for helper in ("specialtyRank", "specialtyWarningMessage", "specialtyMatch",
                   "inferWorkFamily", "specialtyFamily"):
        assert f"function {helper}(" not in source, f"assign-form.js redefines {helper}"

    # The composer's clipboard target (spec 4.4). It is not wired in this phase;
    # the entry point has to exist so Phase 3B has no reason to build a second
    # form, and the signature is what Phase 3B will call.
    assert "function openAssignForm(deps)" in source
    assert "return { openAssignForm" in source


def test_board_harness() -> None:
    """The four behavioural promises, plus the two engine facts they rest on.

    The harness mounts the real place against the real modules: every one of the
    eleven statuses renders, Done counts only `complete`, and a task event
    arriving mid-typing repaints the columns without replacing the search input
    or forgetting the selection.

    The two source assertions after it pin the constants the runtime behaviour
    depends on. Both are cross-boundary agreements that nothing else would
    catch: a renamed engine event would freeze the board silently, and a change
    to either side of the blocked predicate would make the "Needs you" column
    and the needs queue disagree about the same tasks.
    """
    args = ["node", str(HARNESS)] + [str(path) for path in HARNESS_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "everyStatusLands": True,
        "doneExcludesClosed": True,
        "searchSurvivesRefresh": True,
        "selectionSurvivesRefresh": True,
        "needsColumnMatchesQueue": True,
        "refetchesOnResync": True,
    }

    # Every refresh trigger must still be emitted somewhere in the engine.
    data = (BOARD / "board-data.js").read_text(encoding="utf-8")
    block = data.split("TASK_ACTIVITY_EVENTS = Object.freeze([", 1)[1].split("]);", 1)[0]
    names = re.findall(r"'([a-z_]+)'", block)
    assert names, "the refresh trigger constant must not be empty"
    haystack = "\n".join(
        path.read_text(encoding="utf-8")
        for folder in ("core", "api")
        for path in (ROOT / folder).rglob("*.py")
    )
    missing = [n for n in names if f'"{n}"' not in haystack and f"'{n}'" not in haystack]
    assert not missing, f"board refresh triggers no engine event emits: {missing}"

    # The "Needs you" column and GET /api/needs must read the same statuses.
    needs_py = (ROOT / "api" / "routes" / "needs.py").read_text(encoding="utf-8")
    server = re.findall(
        r'"([a-z]+)"', needs_py.split("BLOCKED_STATUSES = (", 1)[1].split(")", 1)[0])
    columns = _frozen_array(_columns_source(), "COLUMNS")
    board = re.findall(
        r"'([a-z]+)'", re.search(r"id: 'needs'.*?statuses: \[([^\]]*)\]", columns, re.S).group(1))
    assert sorted(server) == sorted(board), (
        f"the queue treats {sorted(server)} as blocked but the column shows {sorted(board)}"
    )


def test_board_modules_stay_focused() -> None:
    """No monolith, no markup from strings, and no native confirmation.

    h() escapes by construction, and task titles are operator and model input
    that flows straight into these views.
    """
    files = sorted(BOARD.glob("*.js"))
    assert files, "places/board holds no modules"
    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = len(text.splitlines())
        assert lines <= 300, f"{path.name} is {lines} lines; the cap is 300"
        assert "innerHTML" not in text, f"{path.name} assigns innerHTML"
        assert "insertAdjacentHTML" not in text, f"{path.name} uses insertAdjacentHTML"
        assert "window.confirm" not in text, f"{path.name} uses window.confirm"
        # Dependencies arrive through deps. The optional-global guard is how a
        # missing script tag used to degrade silently instead of failing.
        assert "!== 'undefined'" not in text, (
            f"{path.name} guards a global with typeof; deps are injected, not sniffed"
        )
