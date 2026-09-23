"""Tasks: a column map that is total, and a Done column that tells the truth.

Spec 6.3. The Tasks place is the only surface that claims to show every task, so the
first property it owes the operator is that it actually does.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
TASKS = JS / "places" / "tasks"
HARNESS = Path(__file__).resolve().parent / "js_tasks_harness.cjs"

# Load order matters: the column map before the data layer that reads it, the
# views before the place that mounts them.
HARNESS_MODULES = [
    JS / "core" / "dom.js", JS / "core" / "fact-list.js",
    JS / "core" / "avatar.js", JS / "core" / "switch.js",
    JS / "core" / "search-field.js",
    JS / "core" / "store.js", JS / "core" / "bus.js",
    JS / "core" / "format.js", JS / "core" / "specialty.js",
    JS / "core" / "gates.js",
    JS / "core" / "overlay-focus.js", JS / "core" / "overlays.js", JS / "core" / "menu-select.js",
    JS / "shell" / "places.js", JS / "shell" / "agent-routes.js",
    # tasks-place.js scopes the board to the operator's floor through it.
    JS / "shell" / "floor-scope.js",
    TASKS / "tasks-columns.js", TASKS / "tasks-data.js", TASKS / "tasks-grid.js",
    TASKS / "task-card.js", TASKS / "task-deliverables.js", TASKS / "task-events.js",
    TASKS / "task-detail-sections.js", TASKS / "task-detail.js",
    TASKS / "assign-outcomes.js", TASKS / "assign-form.js",
    TASKS / "tasks-cancel.js", TASKS / "tasks-menu.js", TASKS / "tasks-archive.js",
    TASKS / "tasks-toolbar.js", TASKS / "tasks-place.js",
]

DETAIL_HARNESS = Path(__file__).resolve().parent / "js_task_detail_harness.cjs"

# The detail's own dependency chain, in load order, and nothing else: the
# harness opens real tasks in the real modal without mounting the place.
DETAIL_MODULES = [
    JS / "core" / "dom.js", JS / "core" / "avatar.js", JS / "core" / "format.js",
    JS / "core" / "specialty.js", JS / "core" / "gates.js",
    JS / "core" / "overlay-focus.js", JS / "core" / "overlays.js",
    JS / "core" / "fact-list.js",
    TASKS / "tasks-columns.js", TASKS / "tasks-data.js", TASKS / "task-deliverables.js",
    TASKS / "task-events.js", TASKS / "task-detail-sections.js", TASKS / "task-detail.js",
]

# Column ids, which are not statuses. Anything else the place names must be a
# real member of the engine's TaskStatus literal.
COLUMN_IDS = {"backlog", "working", "needs", "done", "closed"}


def _columns_source() -> str:
    return (TASKS / "tasks-columns.js").read_text(encoding="utf-8")


def _frozen_array(source: str, name: str) -> str:
    """The body of `const <name> = Object.freeze([ … ]);`."""
    head = f"const {name} = Object.freeze(["
    assert head in source, f"{name} must be exported as a frozen array of data"
    return source.split(head, 1)[1].split("]);", 1)[0]


def _frozen_object(source: str, name: str) -> str:
    """The body of `const <name> = Object.freeze({ … });`."""
    head = f"const {name} = Object.freeze({{"
    assert head in source, f"{name} must be exported as a frozen object of data"
    return source.split(head, 1)[1].split("});", 1)[0]


def _engine_statuses() -> set[str]:
    """The members of the engine's TaskStatus literal, which must be eleven."""
    model = (ROOT / "core" / "models" / "task.py").read_text(encoding="utf-8")
    literal = model.split("TaskStatus = Literal[", 1)[1].split("]", 1)[0]
    engine = set(re.findall(r'"([a-z]+)"', literal))
    assert len(engine) == 11, f"TaskStatus changed: {sorted(engine)}"
    return engine


def test_column_map_is_total_over_task_status() -> None:
    """Every engine status maps to exactly one column or the closed list.

    TaskStatus has eleven members. An earlier draft of the spec listed ten;
    `delegated` mapped to no column, so a delegated task would have been in the
    database and invisible in the list. This test is the reason that cannot
    happen again — and it must fail if the engine adds a status.

    Both directions are asserted, plus uniqueness: a status placed in two
    columns would render the same task twice, which is the same class of lie as
    not rendering it at all.
    """
    engine = _engine_statuses()

    js = _columns_source()
    columns = _frozen_array(js, "COLUMNS")
    closed = _frozen_array(js, "CLOSED_WITHOUT_COMPLETING")
    mapped = set(re.findall(r"'([a-z]+)'", columns + closed))
    listed = mapped & engine

    assert engine - listed == set(), f"statuses with no column: {sorted(engine - listed)}"
    assert mapped - engine - COLUMN_IDS == set(), \
        "the place names a status the engine does not have"

    # Exactly one home each: every placement across all columns plus the
    # closed list, counted with duplicates.
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
    data = (TASKS / "tasks-data.js").read_text(encoding="utf-8")
    place = (TASKS / "tasks-place.js").read_text(encoding="utf-8")
    assert "unplaced" in data and "return { columns, older, unplaced, undated };" in data
    assert "grouped.unplaced.length > 0" in place
    assert "role: 'alert'" in place.split("grouped.unplaced.length > 0", 1)[1][:300]


def test_tasks_opens_place_param_task() -> None:
    """Created/Accepted notes and blocked needs share this Tasks open path."""
    place = (TASKS / "tasks-place.js").read_text(encoding="utf-8")
    assert "placeParams.taskId" in place
    assert "function openLinkedDetail(taskId)" in place
    cards = (JS / "conversation" / "event-cards.js").read_text(encoding="utf-8")
    assert "ctx.navigate('tasks', { taskId })" in cards
    assert "function originTaskOpenId(message)" in cards
    needs = (JS / "needs" / "need-shape.js").read_text(encoding="utf-8")
    assert "place: 'tasks', params: { taskId: need.id }" in needs


def test_done_column_is_complete_only() -> None:
    """Done means done. Cancelled, declined and abandoned are a separate fact.

    Reporting a cancelled task as done would misrepresent the outcome, which is
    the honesty rule ARCHIVE_HONESTY_COPY and doneClaimGuidance already enforce
    elsewhere. The three live in CLOSED_WITHOUT_COMPLETING and in no column.
    """
    js = _columns_source()
    columns = _frozen_array(js, "COLUMNS")

    done = re.search(r"id: 'done',.*?statuses: \[([^\]]*)\]", columns, re.S)
    assert done, "the place has no Done column"
    assert re.findall(r"'([a-z]+)'", done.group(1)) == ["complete"]

    closed = _frozen_array(js, "CLOSED_WITHOUT_COMPLETING")
    assert re.findall(r"'([a-z]+)'", closed) == ["abandoned", "declined", "cancelled"]

    groups = re.findall(r"statuses: \[([^\]]*)\]", columns)
    for status in ("abandoned", "declined", "cancelled"):
        for group in groups:
            assert f"'{status}'" not in group, f"{status} is in a column, not the closed list"

    # TERMINAL_STATUSES still covers cancelled — it is what decides whether a
    # task can be cancelled at all, and company-tasks.js asserted the same.
    assert "const TERMINAL_STATUSES = Object.freeze(new Set([" in js
    terminal = js.split("const TERMINAL_STATUSES = Object.freeze(new Set([", 1)[1] \
        .split("]));", 1)[0]
    assert "CLOSED_WITHOUT_COMPLETING" in terminal, (
        "TERMINAL_STATUSES must be derived from the map, never written out again"
    )


def test_status_tables_are_total_over_task_status() -> None:
    """Every engine status has a label and a verb, and nothing else does.

    The card's Needs line, the detail's status pill and the activity sentences
    all read these two tables. A status missing from either would render as
    its raw id, or as a sentence with no verb; a stale key would be a status
    the engine no longer has.
    """
    engine = _engine_statuses()
    js = _columns_source()
    for name in ("STATUS_LABELS", "STATUS_VERBS"):
        keys = set(re.findall(r"(\w+):", _frozen_object(js, name)))
        assert keys == engine, f"{name} is not total over TaskStatus: {sorted(keys ^ engine)}"


def test_closed_without_completing_is_marked_and_never_counted() -> None:
    """The three closed statuses sit in Done, and never count as done.

    They are finished work, so they share Done's window and its day groups;
    what keeps the honesty rule is that they are marked in words and left out
    of every done count. The data layer tests `closed` BEFORE the done tally,
    over the same rows the column shows, so a cancelled task can never fall
    through into it.
    """
    columns = _columns_source()
    data = (TASKS / "tasks-data.js").read_text(encoding="utf-8")
    card = (TASKS / "task-card.js").read_text(encoding="utf-8")
    grid = (TASKS / "tasks-grid.js").read_text(encoding="utf-8")
    css = (ROOT / "ui" / "static" / "css" / "places.css").read_text(encoding="utf-8")

    assert "const CLOSED_NOTE = 'closed without completing';" in columns

    # The card says so, in the attribute the stylesheet keys on and in words.
    assert "'data-closed': COLUMNS.isClosedWithoutCompleting(" in card
    assert "COLUMNS.CLOSED_NOTE" in card

    # The Done heading counts only the rows that are not closed.
    render = grid.split("function renderGrid(", 1)[1]
    count = render.split("const count =", 1)[1].split(";", 1)[0]
    assert "column.id === 'done'" in count
    assert "isClosedWithoutCompleting" in count, "the Done heading counts closed rows"
    assert "header(column, count)" in render

    # And it looks unfinished: a dashed edge and a struck-through title.
    assert '.task-card[data-closed="true"] { border-style: dashed; }' in css
    assert '.task-card[data-closed="true"] .task-card-title { text-decoration: line-through;' in css

    counts = data.split("function counts(grouped) {", 1)[1].split("\n    }", 1)[0]
    assert "for (const task of grouped.columns.done) {" in counts, (
        "done and closed must be tallied from the rows the Done column shows"
    )
    assert "isClosedWithoutCompleting(task.status)) totals.closed += 1" in counts
    assert counts.index("totals.closed += 1") < counts.index("totals.done += 1"), (
        "closed must be tested before done, or done absorbs cancelled tasks"
    )


def test_selection_and_bulk_cancel_are_keyboard_reachable_and_confirmed() -> None:
    """Multi-select and bulk cancel survived the table, with their edges intact.

    The checkbox is named and always in the tab order; the confirmation is the
    focus-trapped dialog rather than window.confirm, which cannot be styled,
    cannot be tested, and blocks the event loop.
    """
    card = (TASKS / "task-card.js").read_text(encoding="utf-8")
    place = (TASKS / "tasks-place.js").read_text(encoding="utf-8")
    toolbar = (TASKS / "tasks-toolbar.js").read_text(encoding="utf-8")
    css = (ROOT / "ui" / "static" / "css" / "places.css").read_text(encoding="utf-8")

    assert "h('label', { class: 'task-card-select' }" in card
    assert "'aria-label': `Select ${title}`" in card
    assert "type: 'checkbox'" in card

    # Revealed on hover AND on focus. Hover alone would hide it from anyone
    # driving the list by keyboard (SC 2.1.1, SC 2.4.7).
    assert ".task-card:focus-within .task-card-select" in css
    # Absolutely positioned, so a resting card spends no gutter on it.
    assert ".task-card-select { position: absolute; top: 6px; right: 6px; opacity: 0; }" in css

    for path in sorted(TASKS.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert "window.confirm" not in text, f"{path.name} uses window.confirm"

    cancel = (TASKS / "tasks-cancel.js").read_text(encoding="utf-8")
    assert "BossModOverlays.createModal({" in cancel
    assert "Cancel selected" in toolbar
    assert "setSelectedCount" in toolbar
    assert "BossModOverlays.createModal(" not in place, (
        "the Tasks place has one destructive path, and tasks-cancel.js owns it"
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
    source = (TASKS / "assign-form.js").read_text(encoding="utf-8")
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


def test_tasks_harness() -> None:
    """The four behavioural promises, plus the two engine facts they rest on.

    The harness mounts the real place against the real modules: every one of the
    eleven statuses renders, Done counts only `complete`, and a task event
    arriving mid-typing repaints the columns without replacing the search input
    or forgetting the selection.

    The two source assertions after it pin the constants the runtime behaviour
    depends on. Both are cross-boundary agreements that nothing else would
    catch: a renamed engine event would freeze the list silently, and a change
    to either side of the blocked predicate would make the "Needs you" column
    and the needs queue disagree about the same tasks.
    """
    args = ["node", str(HARNESS)] + [str(path) for path in HARNESS_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "windowSplitsDone": True,
        "undatedIsReported": True,
        "unknownStatusIsUnplaced": True,
        "countsExcludeClosed": True,
        "chatTargetRules": True,
        "sortFollowsFinishTime": True,
        "unknownWindowThrows": True,
        "everyStatusLands": True,
        "doneExcludesClosed": True,
        "closedCardsAreMarked": True,
        "doneGroupsByDay": True,
        "summaryCountsTheWindow": True,
        "cancelHiddenUntilSelected": True,
        "olderOpensArchive": True,
        "archiveOpensTaskAsLayer": True,
        "windowMovesDone": True,
        "sortReverses": True,
        "needsCardOpensChat": True,
        "threadTaskOpensThread": True,
        "searchSurvivesRefresh": True,
        "selectionSurvivesRefresh": True,
        "needsColumnMatchesQueue": True,
        "refetchesOnResync": True,
        "opensLinkedTask": True,
        "offFloorTaskHidden": True,
    }

    # Every refresh trigger must still be emitted somewhere in the engine.
    data = (TASKS / "tasks-data.js").read_text(encoding="utf-8")
    block = data.split("TASK_ACTIVITY_EVENTS = Object.freeze([", 1)[1].split("]);", 1)[0]
    names = re.findall(r"'([a-z_]+)'", block)
    assert names, "the refresh trigger constant must not be empty"
    haystack = "\n".join(
        path.read_text(encoding="utf-8")
        for folder in ("core", "api")
        for path in (ROOT / folder).rglob("*.py")
    )
    missing = [n for n in names if f'"{n}"' not in haystack and f"'{n}'" not in haystack]
    assert not missing, f"task refresh triggers no engine event emits: {missing}"

    # The "Needs you" column and GET /api/needs must read the same statuses.
    needs_py = (ROOT / "api" / "routes" / "needs.py").read_text(encoding="utf-8")
    server = re.findall(
        r'"([a-z]+)"', needs_py.split("BLOCKED_STATUSES = (", 1)[1].split(")", 1)[0])
    columns = _frozen_array(_columns_source(), "COLUMNS")
    column = re.findall(
        r"'([a-z]+)'", re.search(r"id: 'needs'.*?statuses: \[([^\]]*)\]", columns, re.S).group(1))
    assert sorted(server) == sorted(column), (
        f"the queue treats {sorted(server)} as blocked but the column shows {sorted(column)}"
    )


def test_tasks_modules_stay_focused() -> None:
    """No monolith, no markup from strings, and no native confirmation.

    h() escapes by construction, and task titles are operator and model input
    that flows straight into these views.
    """
    files = sorted(TASKS.glob("*.js"))
    assert files, "places/tasks holds no modules"
    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = len(text.splitlines())
        assert lines <= 400, f"{path.name} is {lines} lines; the cap is 400"
        assert "innerHTML" not in text, f"{path.name} assigns innerHTML"
        assert "insertAdjacentHTML" not in text, f"{path.name} uses insertAdjacentHTML"
        assert "window.confirm" not in text, f"{path.name} uses window.confirm"
        # Dependencies arrive through deps. The optional-global guard is how a
        # missing script tag used to degrade silently instead of failing.
        assert "!== 'undefined'" not in text, (
            f"{path.name} guards a global with typeof; deps are injected, not sniffed"
        )


def test_task_detail_and_assign_are_modals() -> None:
    """Task detail reads in a panel modal; Assign is a form modal.

    The detail has nothing to type into, so an outside click closes it.
    Assign holds a half-written task, so it does not — and its primary is
    pinned in the footer band through createModal's `form:` action, which
    submits without closing so an outcome (a mismatch, an ambiguous match) is
    shown in the dialog rather than lost with it.
    """
    detail = (TASKS / "task-detail.js").read_text(encoding="utf-8")
    assign = (TASKS / "assign-form.js").read_text(encoding="utf-8")
    assert "BossModOverlays.createModal({" in detail
    assert "size: 'panel'" in detail
    assert "closeOnBackdrop: true" in detail
    assert "BossModOverlays.createModal({" in assign
    assert "form: 'ct-assign-form'" in assign
    assert "id: 'ct-assign-submit'" in assign
    assert "closeOnBackdrop" not in assign
    for source in (detail, assign):
        assert "slideOver" not in source


def test_task_detail_harness() -> None:
    """The task detail reads as the mockup: one column, nothing said twice.

    The harness opens real tasks in the real modal. It proves the status line
    measures progress by the watchdog's own clock, the facts are pairs with a
    human requester named "You", each kind of state gets its callout (and a
    blocked one offers the chat), the instructions go through the markdown
    renderer and clamp only when they overflow, the subtask checklist counts
    what is done, Cancel lives behind the `⋯` of an unfinished task only, the
    role contract folds away, and the activity reads as sentences.
    """
    args = ["node", str(DETAIL_HARNESS)] + [str(path) for path in DETAIL_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "noDuplicateTitle": True,
        "statusLineReadsProgress": True,
        "factsArePairs": True,
        "humanRequesterIsYou": True,
        "alertCalloutOffersChat": True,
        "completeCalloutShowsSummary": True,
        "instructionsUseMarkdown": True,
        "clampToggleFollowsOverflow": True,
        "subtasksCountDone": True,
        "optionsHoldCancel": True,
        "contractIsCollapsible": True,
        "activityReadsAsSentences": True,
        "deliverablesCounted": True,
    }


def test_a_linked_task_opens_as_a_layer_over_the_one_it_came_from() -> None:
    """Parent/subtask links push a layer; a task card opens a fresh base.

    From inside a task, following a link is a step deeper — ‹ walks back to the
    task it came from. Clicking a card in the list is a new errand, so any
    open task layers close first.
    """
    place = (TASKS / "tasks-place.js").read_text(encoding="utf-8")
    assert "let details = [];" in place
    assert "onNavigate: pushDetail," in place
    open_body = place.split("function openDetail(taskId) {", 1)[1].split("\n    }", 1)[0]
    assert "closeDetails();" in open_body
    push_body = place.split("function pushDetail(taskId) {", 1)[1].split("\n    }", 1)[0]
    assert "closeDetails" not in push_body
