"""The context column (the office summary) and the agent desk modal."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CONTEXT = JS / "context"
CONVERSATION = JS / "conversation"
NEEDS = JS / "needs"
HARNESS = Path(__file__).resolve().parent / "js_context_harness.cjs"

# The order the context harness evaluates its modules in.
CONTEXT_MODULES = [
    # The shared REST client: BossModScheduleApi words refusals with its formatError.
    JS / "api-client.js",
    JS / "core" / "dom.js",
    JS / "core" / "markdown.js",
    JS / "core" / "clamped-markdown.js",
    JS / "core" / "fact-list.js",
    JS / "core" / "avatar.js",
    JS / "core" / "switch.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "operator-invalidate.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "specialty.js",
    JS / "core" / "communication.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "modal-trail.js",
    JS / "core" / "overlay-actions.js",
    JS / "core" / "overlays.js",
    JS / "core" / "menu.js",
    # A desk task's Edit mode picks its assignee from a dropdown.
    JS / "core" / "menu-select.js",
    # Edit-mode widgets: the growing textarea, and the schedule editor's time and date fields.
    JS / "core" / "autogrow.js",
    JS / "core" / "time-field.js",
    JS / "core" / "date-field.js",
    # The desk Files section and the task file picker share its crumbs and rows.
    JS / "core" / "file-listing.js",
    CONVERSATION / "empty-state.js",
    CONVERSATION / "transcript.js",
    CONVERSATION / "transcript-cache.js",
    CONVERSATION / "message.js",
    CONVERSATION / "event-cards.js",
    CONVERSATION / "title-rename.js",
    CONVERSATION / "chrome-menu.js",
    CONVERSATION / "chrome.js",
    JS / "core" / "desktop-clipboard.js",
    CONVERSATION / "composer-attachments.js",
    CONVERSATION / "composer.js",
    CONVERSATION / "system-receipts.js",
    NEEDS / "need-shape.js",
    NEEDS / "need-coalesce.js",
    NEEDS / "needs-store.js",
    NEEDS / "needs-bar.js",
    CONVERSATION / "sources" / "thread-archive.js",
    CONVERSATION / "sources" / "thread-seat.js",
    CONVERSATION / "sources" / "thread-requests.js",
    CONVERSATION / "auto-approve-switch.js",
    CONVERSATION / "sources" / "thread-source.js",
    CONVERSATION / "sources" / "agent-requests.js",
    CONVERSATION / "sources" / "agent-source.js",
    CONVERSATION / "conversation-focus-invalidate.js",
    CONVERSATION / "chat-rewind-dialog.js",
    CONVERSATION / "chat-rewind.js",
    CONVERSATION / "consent-activity.js",
    CONVERSATION / "conversation.js",
    JS / "shell" / "places.js",
    # The shared viewer the desk browser opens, with the two modules it is
    # built from. Re-pointed in Phase 3B: it is places/files/file-viewer.js now.
    JS / "places" / "files" / "file-content.js",
    JS / "places" / "files" / "file-form.js",
    JS / "places" / "files" / "file-ops.js",
    JS / "places" / "files" / "file-viewer.js",
    CONTEXT / "floor-plan.js",
    CONTEXT / "mini-office.js",
    CONTEXT / "office-chatter.js",
    CONTEXT / "desk-opener.js",
    CONTEXT / "desk-files.js",
    CONTEXT / "desk-notes.js",
    CONTEXT / "desk-tasks.js",
    CONTEXT / "desk-actions.js",
    CONTEXT / "agent-api.js",
    CONTEXT / "agent-templates-api.js",
    CONTEXT / "agent-fields.js",
    CONTEXT / "agent-form-fields.js",
    CONTEXT / "agent-form-advanced.js",
    CONTEXT / "agent-form-choices.js",
    CONTEXT / "agent-form-connections.js",
    CONTEXT / "agent-form-bindings.js",
    CONTEXT / "agent-form-hydrate.js",
    CONTEXT / "agent-form.js",
    CONTEXT / "agent-submit.js",
    CONTEXT / "agent-recovery.js",
    CONTEXT / "agent-form-save.js",
    JS / "marketplace" / "marketplace-items.js",
    JS / "marketplace" / "pack-card.js",
    JS / "marketplace" / "filter-rail.js",
    JS / "core" / "search-field.js",
    JS / "core" / "tabs.js",
    CONTEXT / "agent-template-picker.js",
    CONTEXT / "agent-form-template.js",
    CONTEXT / "agent-dialog-footer.js",
    CONTEXT / "agent-add-pane.js",
    CONTEXT / "agent-dialog-slot.js",
    JS / "shell" / "floor-scope.js",
    CONTEXT / "agent-edit.js",
    CONTEXT / "agents-dialog.js",
    # The desk's Schedules section: the recurrence editor, its layer, the section.
    CONTEXT / "schedule-api.js",
    CONTEXT / "schedule-view.js",
    CONTEXT / "schedule-fields.js",
    CONTEXT / "schedule-preview.js",
    CONTEXT / "schedule-layer.js",
    CONTEXT / "desk-schedules.js",
    CONTEXT / "desk-pack.js",
    CONTEXT / "desk-memory.js",
    CONTEXT / "desk-panel.js",
    JS / "places" / "tasks" / "tasks-columns.js",
    # A desk task row opens the task as a layer over the desk: the Tasks
    # place's loader, detail, canceller and layer controller, and the desk's
    # opener over them (index.html loads the desk after all of these).
    JS / "places" / "tasks" / "tasks-data.js",
    JS / "places" / "tasks" / "task-deliverables.js",
    JS / "places" / "tasks" / "task-events.js",
    JS / "places" / "tasks" / "task-detail-sections.js",
    JS / "places" / "tasks" / "task-detail.js",
    JS / "places" / "tasks" / "tasks-cancel.js",
    JS / "places" / "tasks" / "assign-outcomes.js",
    JS / "places" / "tasks" / "assign-form.js",
    JS / "places" / "tasks" / "task-file-picker.js",
    JS / "places" / "tasks" / "task-edit-files.js",
    JS / "places" / "tasks" / "task-edit-mode.js",
    JS / "places" / "tasks" / "tasks-complete.js",
    JS / "places" / "tasks" / "task-actions.js",
    JS / "places" / "tasks" / "task-layers.js",
    CONTEXT / "desk-task-opener.js",
    JS / "shell" / "agent-routes.js",
    CONTEXT / "desk-dialog.js",
    JS / "extensions" / "extensions-api.js",
    JS / "extensions" / "browser-vision-status.js",
    JS / "extensions" / "extensions-live.js",
    # The desk's Extensions section reads through BossModExtensionsApi at load.
    CONTEXT / "desk-extensions.js",
    JS / "places" / "chat" / "chat-place.js",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _harness() -> dict:
    args = ["node", str(HARNESS)] + [str(path) for path in CONTEXT_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_context_column_is_torn_down_when_chat_unmounts() -> None:
    """The column is the place's, not the shell's.

    #app-context lives outside #app-place, so the shell hides it on navigation
    but never clears it. If Chat did not destroy the column, its subscriptions
    would survive every navigation away and accumulate one set per visit.

    The column holds the office summary and the office chatter under it: the
    desk that used to be a second view is a modal, one at a time, which
    drains everything it subscribed to when it closes.
    """
    payload = _harness()
    assert payload["drainsOnDestroy"] is True
    assert payload["columnHoldsOfficeAndChatter"] is True
    assert payload["oneDeskAtATime"] is True
    assert payload["deskDrainsOnClose"] is True

    place = _read(JS / "places" / "chat" / "chat-place.js")
    assert "BossModMiniOffice.createMiniOffice(" in place
    assert "miniOffice.destroy()" in place
    assert "BossModOfficeChatter.createOfficeChatter(" in place
    assert "officeChatter.destroy()" in place
    # The view switcher is gone with the second view it switched to.
    assert not (CONTEXT / "context-column.js").exists()
    assert "BossModContextColumn" not in place
    # The shell hands the element down and knows nothing else about it.
    # Phase 4 resolved it into a local so the responsive layer can move the
    # same element into an overlay below 1200px; it is still resolved ONCE and
    # still handed down rather than filled in here.
    shell = _read(JS / "shell" / "shell.js")
    assert "const contextElement = requireElement('app-context');" in shell
    assert shell.count("requireElement('app-context')") == 1
    assert "contextEl: contextElement," in shell
    # It hands the element down and never builds what goes in it. (The shell's
    # own applyContextColumn only toggles the column from place.hasContext.)
    assert "BossModContextColumn" not in shell
    assert "BossModMiniOffice" not in shell
    assert "BossModOfficeChatter" not in shell


def test_mini_office_groups_by_location_including_unknown() -> None:
    """An off-map agent still gets a seat (spec 7), on a summary that is the map.

    `agent.location` is a room NAME derived from coordinates, and it is absent
    for an agent the world could not place. Grouping them away rather than
    under a real heading would hide them from the operator completely.

    The summary used to be barred from all geometry. The operator reversed
    that: it must read as the same floor as the Office map, so rooms take the
    plan's bounds and types — agents still group by room name.
    """
    payload = _harness()
    assert payload["rendersUnknownRoom"] is True
    # Phase 2 (refresh efficiency): one agent's change rebuilds one seat.
    assert payload["miniOfficeSeatsArePatched"] is True
    assert payload["seatOpensDesk"] is True
    # Floor-scoped: only the visible floor's agents get a seat, a floor switch
    # re-seats without refetching the plan, and each empty state says which.
    assert payload["miniOfficeSeatsOnlyTheVisibleFloor"] is True
    assert payload["miniOfficeFollowsTheFloorSwitch"] is True
    assert payload["miniOfficeSaysTheFloorIsEmpty"] is True
    assert payload["miniOfficeSaysTheRosterIsEmpty"] is True
    # The summary is the Office map: rooms coloured by type with the canvas's
    # tokens, placed at their bounds, the tall Hallway turned, Unknown across
    # the floor, and a plan it cannot draw refused rather than guessed at.
    assert payload["miniOfficeColoursByRoomType"] is True
    assert payload["miniOfficePlacesRoomsByBounds"] is True
    assert payload["miniOfficeTurnsTallRooms"] is True
    assert payload["miniOfficeUnplacedSpansTheFloor"] is True
    assert payload["miniOfficeRejectsAnUndrawablePlan"] is True
    # Mapped rooms show the plan's short label, with the full name kept for
    # screen readers and the tooltip; Unknown has no plan entry and keeps its own.
    assert payload["miniOfficeShowsShortLabels"] is True
    assert payload["unknownKeepsItsFullLabel"] is True

    source = _read(CONTEXT / "mini-office.js")
    plan = _read(CONTEXT / "floor-plan.js")
    assert "UNPLACED_ROOM = 'Unknown'" in source
    assert "agent.location" in source
    # A floor plan, not a second canvas. The operator reversed the old ban on
    # geometry: the summary must look like the Office map, so it reads each
    # room's `bounds` and `room_type` from the plan (floor-plan.js holds that
    # pure half). What stays out is what would make it a renderer — it never
    # paints tiles or desks, never uses a canvas, never reads the map's own
    # dimensions, and never positions agents by coordinates.
    assert "mapData.rooms" in source
    assert "room_type" in plan
    for path, text in (("mini-office.js", source), ("floor-plan.js", plan)):
        for forbidden in ("getContext", "'canvas'", "agent.x", "agent.y",
                          "mapData.tiles", "mapData.width", "mapData.height",
                          "mapData.desks"):
            assert forbidden not in text, f"{path} must not render a map ({forbidden})"


def test_desk_memory_is_a_layer_with_a_confirmed_remove() -> None:
    """The `⋯`'s Memory row: what the agent is shown every turn, prunable.

    The store sits outside every agent path, so neither Files nor Notes can
    show it. The layer reads GET /api/agents/{id}/memory, lists `n — text`,
    and removes by number behind the standard confirm layer.
    """
    payload = _harness()
    for key in (
        "theMemoryToolOpensALayer", "memorySaysLoading", "memoryListsEachRow",
        "memoryRemoveAsksFirst", "memoryCancelSendsNothing", "memoryConfirmDeletesAndRefreshes",
        "memoryAlreadyGoneIsANotice", "memoryFailureKeepsTheRow", "memoryBackReturnsToTheDesk",
        "memoryReadFailureOffersRetry", "memoryRetryRecovers", "closingTheDeskClosesTheMemoryLayer",
        "memorySaysNothingSaved",
    ):
        assert payload[key] is True, key
    assert payload["toolsAreInTheHead"] is True

    source = _read(CONTEXT / "desk-memory.js")
    assert len(source.splitlines()) < 400
    assert "const loads = BossModGates.createLoadGeneration()" in source
    assert "BossModOverlays.createModal(" in source
    assert "innerHTML" not in source
    panel = _read(CONTEXT / "desk-panel.js")
    assert len(panel.splitlines()) < 400
    assert "BossModDeskMemory.createDeskMemory({ api, agentId })" in panel
    assert "menuRow('desk-memory', 'brain', LABELS.memory, pick(() => memory.open()))" in panel
    assert "memory.destroy()" in panel
    # Open chat, Memory, Edit role, the divider, then the operational rows.
    assert (panel.index("menuRow('desk-chat'") < panel.index("menuRow('desk-memory'")
            < panel.index("menuRow('desk-edit'") < panel.index("h('hr', { class: 'menu-divider' })")
            < panel.index("menuRow('desk-diagnostics'"))
    index = _read(ROOT / "ui" / "templates" / "index.html")
    assert index.index("js/context/desk-memory.js") < index.index("js/context/desk-panel.js")


def test_desk_notes_read_the_workspace_not_a_column() -> None:
    """Spec 7, resolved: Notes is a surfacing problem, not a data one.

    Two phases carried "Notes" as a requirement no column could satisfy, and
    Phase 2B put the done/fail bar in the slot as a stand-in. The operator's
    answer was that agents already write markdown into their own workspace, so
    the panel reads `/me/notes` and opens what it finds in the one viewer.

    The half that matters most is the distinction: a new agent has written
    nothing, so Notes is empty, and that is the EMPTY state. The desk GET
    soft-empties `/me/notes`; a leftover 404 is treated the same way. Rendering
    either as an error would tell every operator their brand new agent was
    broken. A genuine failure still has to look like one, which is why both
    are proven.
    """
    payload = _harness()
    assert payload["readsTheWorkspace"] is True
    assert payload["listsNewestFirst"] is True
    assert payload["opensSharedViewer"] is True
    assert payload["absentIsEmptyNotError"] is True
    assert payload["failureSurfaces"] is True

    notes = _read(CONTEXT / "desk-notes.js")
    assert "NOTES_PATH = '/me/notes'" in notes
    assert "No notes yet" in notes
    # A leftover 404 is still absence. It must be checked BEFORE the generic
    # !res.ok branch, or a probe reports a failure that never happened.
    assert notes.index("res.status === 404") < notes.index("if (!res.ok)")
    # No schema work: this reads the desk endpoint that already exists.
    assert "/desk?path=" in notes
    assert "done_fail_bar" not in notes, "Notes is the workspace now, not the stand-in"
    # One viewer, the same one the desk browser opens.
    assert "BossModFileViewer.open(" in notes
    assert "innerHTML" not in notes

    # The stand-in was not deleted with the slot it occupied: the agent's
    # contract copy moved to the profile, where test_role_contracts.py still
    # finds it.
    panel = _read(CONTEXT / "desk-panel.js")
    assert "BossModDeskNotes.createDeskNotes(" in panel
    assert "What done looks like for this agent:" in panel
    # The copy is read off the agent row and rendered into the .desk-bar node,
    # which the polish round moved inside a <details> disclosure. Source order
    # was the old proxy for that and stopped meaning anything once the node was
    # built at construction rather than inside the render; the harness reads
    # the rendered VALUE instead, which is what the proxy was standing in for.
    assert "const bar = who.done_fail_bar" in panel
    assert "contractEl.append(bar ?" in panel
    assert _harness()["deskFields"]["contract"] is True
    # The section is drained with the rest of the panel.
    assert "notes.destroy();" in panel


def test_the_connection_pick_lands_through_the_published_form() -> None:
    """The AI connection picker writes into the form that was published.

    The picker is mounted while the form sits on a DETACHED stage, and the
    operator answers it after `renderInline` has published — and publishing
    MOVES the `<form>` out of that stage and empties it. A binding rooted on
    the host would search an emptied node for the rest of the form's life,
    and the agent would be saved with no connection over "Saved
    successfully".

    The save carries the connection's id and the two thinking levels, and
    nothing a connection owns: no model name, base URL, extra body or key —
    the runtime reads the connection live. The thinking choices follow the
    picked connection.

    Run against the REAL builder, the REAL publish and the REAL submit path:
    tests/js_add_agent_harness.cjs stubs `BossModAgentForm` wholesale.
    """
    payload = _harness()
    assert payload["pickerAnswersAfterPublish"] is True
    assert payload["thePickIsWhatIsSaved"] is True
    # The rule, where it is made: the `<form>` is the node that survives being
    # published out of its host, so it is the node every binding holds.
    form = _read(CONTEXT / "agent-form.js")
    assert "const form = container.querySelector('#agent-form');" in form
    # ...and the host is not read again after that one line. Everything the
    # builder binds, it binds off the form.
    bindings = form.split("if (!form) throw", 1)[1]
    assert "container" not in bindings
    assert "BINDINGS.bindAiConnection(form, connections, values);" in bindings


def test_a_template_form_asks_the_ai_question_on_screen() -> None:
    """A template hides nothing: the picker and the colour are on the form.

    The layout a template once got swept the connection controls and the
    colour behind a collapsed disclosure, so the one decision that decides
    whether a new agent can take a turn at all was the one the template path
    hid. The picker starts unanswered, every AI control is outside the one
    disclosure left (Advanced), and what is picked is what the save sends.

    Driven through the real builder, the real publish, the real
    `buildSubmitData` and the real POST.
    """
    payload = _harness()
    for key in ("theTemplateFormAsksForAConnection",
                "theQuickCreateSavesTheConnection"):
        assert payload[key] is True, key
    assert payload["nothingIsHiddenFromATemplate"] is True


def test_a_create_with_no_ai_connection_is_refused_at_the_create() -> None:
    """The invariant lives on what would be SENT, not on a control.

    Five UI routes once each produced an agent with no connection, each
    fixed at the control that exposed it until the next appeared. So the rule
    is enforced once (spec 8.3), in `agent-form-save.js`'s submit handler, on
    the built `agentData` rather than on the DOM. That handler is the seam
    because it is the last point that still knows the save is a create;
    `buildSubmitData` is not, because telling a form-level mapper about
    create-from-edit would push a dialog-level rule into it. The server
    refuses it as well (`connection_id` is required).

    Driven end to end through the real builder, the real publish, the real
    `buildSubmitData` and a POST that would have SUCCEEDED — a refusal proven
    against an endpoint that refuses anyway proves nothing.
    """
    payload = _harness()
    # Nothing POSTed, dialog open, draft intact, told why, primary usable.
    assert payload["theConnectionlessCreateIsRefused"] is True
    # ...and the same refusal on the BLANK path.
    assert payload["theBlankRefusalNamesThePicker"] is True
    # ...and the other shape, where the section renders a link to Settings
    # and no control at all: the only next action is Settings.
    assert payload["theUnconfiguredRefusalSendsThemToSettings"] is True
    # ...and it is refused EARLIER than that: with nothing to choose from, the
    # dialog withholds its primary the moment the form lands.
    assert payload["theUnconfiguredCreateIsWithheldNotOffered"] is True
    # A gate, not a dead end: answer it and the same click goes through.
    assert payload["theCorrectedCreateGoesThrough"] is True

    save = _read(CONTEXT / "agent-form-save.js")
    # It reads what would be SENT.
    assert "if (isCreating && !agentData.connection_id) {" in save
    # ...and it is upstream of the POST it is refusing.
    assert save.index("say('bad', noConnection(form))") < save.index("apiCreateAgent(agentData)")
    # One refusal mechanism, not two: the form's existing feedback line.
    assert "say('bad', noConnection(form))" in save
    # The handler picks its sentence from ONE attribute read, and the module
    # that RENDERS the two shapes is the one that names them.
    assert "NO_CONNECTION_NEXT[BossModAgentFormConnections.aiQuestion(form)]" in save
    # No class, id or section name from any layout below this point: the
    # handler must not be able to name a control, only to ask which shape the
    # form is in.
    for layout in ("quick-disclosure", "quick-ai", "Review & customise",
                   "connection-grid", "form-section", "agent-form-grid", "agent-ai-mount"):
        assert layout not in save.split("const noConnection", 1)[1], layout
    # The vocabulary has one owner: the module that builds both shapes decides
    # which was built and answers for it later.
    conn = _read(CONTEXT / "agent-form-connections.js")
    assert "function shapeFor(connections)" in conn
    assert "function aiQuestion(form)" in conn
    assert "return (connections || []).length ? PICKER : UNAVAILABLE;" in conn
    # ...and the assembler writes it from the SAME list the section was built
    # from, so the attribute and the markup cannot disagree.
    form_js = _read(CONTEXT / "agent-form.js")
    assert "BossModAgentFormConnections.AI_QUESTION," in form_js
    assert "BossModAgentFormConnections.shapeFor(connections)," in form_js
    # The mapper stays a mapper.
    submit = _read(CONTEXT / "agent-submit.js")
    for leaked in ("NO_CONNECTION", "isCreating"):
        assert leaked not in submit, f"the create-only rule leaked into the mapper ({leaked})"


def test_an_edit_with_no_ai_connection_is_still_permitted() -> None:
    """The refusal is scoped to CREATE, deliberately.

    An agent with no connection is a real row — the roster predates the
    invariant, and a connection can be deleted out from under one. Refusing
    that save would trap the operator in a dialog they cannot leave without
    losing every other edit they came to make, which is a worse outcome than
    the one the rule exists to prevent: the agent already exists either way.

    Driven the same way as the create: the real edit dialog of an UNLINKED
    agent (what the upgrade leaves when it cannot tell which connection one
    used), which says so under the section, and the PATCH watched for on the
    wire.
    """
    payload = _harness()
    assert payload["anEditWithNoConnectionStillSaves"] is True


def test_the_forms_recovery_tools_are_bound_to_the_form_not_the_stage() -> None:
    """The same shape that silently killed "Set All", one module along.

    context/agent-form-save.js stages the form on a detached host and publishes
    it with `replaceChildren(...stage.children)`, which MOVES the `<form>` out
    and leaves the stage EMPTY. `bindRecoveryTools` was handed that stage. It
    happens to be harmless — the two buttons are queried and bound while the
    stage still holds them, and the nodes survive publication — but it is the
    exact latent shape agent-form.js's header forbids, and `form` was already
    resolved one line above it.

    The parameter is named `form` rather than `container` for the same reason:
    a name that invites a host is how the wrong node gets passed again.
    """
    save = _read(CONTEXT / "agent-form-save.js")
    tools = save.split("RECOVERY.bindRecoveryTools({", 1)[1].split("});", 1)[0]
    assert "form," in tools
    assert "stage" not in tools
    recovery = _read(CONTEXT / "agent-recovery.js")
    body = recovery.split("function bindRecoveryTools(", 1)[1]
    assert "container" not in body
    assert "form.querySelector('#btn-clear-chat-history')" in body
    assert "form.querySelector('#btn-reset-runtime')" in body


def test_the_context_harness_ends_when_its_work_does() -> None:
    """Debounce checks and cosmetic cleanup must not wait on the real clock.

    The harness advances timeout deadlines explicitly, then releases pending
    timers after its verdict. The unchanged limit also catches a return to
    the three-second feedback-hide timer keeping Node's event loop alive.
    """
    args = ["node", str(HARNESS)] + [str(path) for path in CONTEXT_MODULES]
    started = time.monotonic()
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr or result.stdout
    assert elapsed < 2.5, (
        f"the harness idled for {elapsed:.2f}s after printing its verdict; "
        "a pending timer is holding the run open"
    )


def test_a_failed_dependency_read_still_renders_the_form() -> None:
    """`loadFormData`'s documented degradation, made true for an HTTP error.

    It promises empty lists so the form still renders with its "no connections
    configured" link to Settings. It had no
    `res.ok` and no `Array.isArray`, so a 500's `{detail}` object was assigned
    straight through, the matrix iterated it, and `connections.map is not a
    function` came out of the renderer — reaching the operator as the generic
    "The agent editor failed to load."

    The SAVE is a separate decision and still refuses (that read is
    `readConnections`, which answers null rather than an empty list), so this
    also pins the withheld primary's accessible half: the reason is named as
    its description, the line carrying it is a live region, and the keyboard
    goes to that line — a disabled button cannot take focus back.
    """
    payload = _harness()
    assert payload["aFailedConnectionsReadStillRendersTheForm"] is True
    assert payload["theBlockedPrimaryHandsOverTheKeyboard"] is True
    form = _read(CONTEXT / "agent-form.js")
    assert "async function readList(res, what)" in form
    assert "if (!res.ok) throw new Error(`GET ${what} answered ${res.status}`);" in form
    assert "if (!Array.isArray(body))" in form
    # ...and it still degrades rather than refusing to render. The one shared
    # catch that used to do that is gone, and its replacement is the point: it
    # degraded EVERY read whenever any one of them rejected, so a dead sibling
    # read emptied the connections list of an operator who had two. Each read
    # now settles alone and names itself when it fails.
    assert "await Promise.allSettled(requests)" in form
    assert "if (outcome.status === 'rejected') throw outcome.reason;" in form
    assert "failed.push(what);" in form
    assert "Promise.all(requests)" not in form


def test_one_failing_dependency_read_does_not_erase_the_others() -> None:
    """A rejected sibling cost three healthy reads their results.

    `loadFormData` ran its requests through one `Promise.all` and one
    catch, so a REJECTED request — a network error, an abort — rejected the
    batch and left every list empty. With the roster read down and
    `/api/connections` perfectly healthy the operator was shown "No connections
    configured. Add one in Settings" while holding two, no matrix to choose
    one in, and a live primary: `readConnections` is a separate call, so
    nothing blocked the save and nothing said anything had failed. Closing the
    dialog and reopening it was the only exit, and nothing said that either.

    The empty list and the failed read must stay different, because they have
    different answers — "you have none, add one" against "we could not find
    out". So each read settles alone and a failure NAMES itself, both in the
    console and at the top of the form it degraded.
    """
    payload = _harness()
    assert payload["aRejectedSiblingKeepsTheOtherReads"] is True
    # ...and the form it degraded still saves, which is what makes the notice
    # a notice rather than a dead end with an explanation attached.
    assert payload["aDegradedFormStillSaves"] is True
    form = _read(CONTEXT / "agent-form.js")
    # The notice is a node, not markup: the exemption this module carries is
    # for its <form> wrapper and does not stretch to cover a second string.
    assert "h('p', { class: 'form-degraded', id: 'agent-form-degraded' }," in form
    # One live region in this editor, and it belongs to the save path
    # (context/agent-recovery.js's feedback line). This notice is present when
    # the form arrives rather than announced into it, so the whole composition
    # module declares no announcement channel of its own.
    for announced in ("aria-live", "role:", "'role'", 'role="'):
        assert announced not in form, announced
    # Amber on the tint tests/test_ui_tokens.py measures, not an inline colour.
    css = (ROOT / "ui" / "static" / "css" / "overlays.css").read_text(encoding="utf-8")
    block = css.split(".form-degraded {", 1)[1].split("}", 1)[0]
    assert "background: var(--amber);" in block
    assert "color: var(--amber-ink);" in block


def test_agent_edit_modules_stay_focused() -> None:
    """825 lines with a 380-line function inside it, split by responsibility.

    Every piece has one owner and one reason to change: the requests, the field
    vocabulary, the two markup groups, the per-field bindings, the composition,
    what the server is told, and the destructive tools. The cap is the cheap
    half of that; the half that matters is that each module names exactly one
    of those jobs, so this also checks nothing kept a private second copy of
    the two things both halves of the form need.
    """
    js = ROOT / "ui" / "static" / "js"
    assert not (js / "agent-panel.js").exists(), "agent-panel.js is still on disk"
    assert "AgentPanel" not in _read(CONTEXT / "agent-edit.js")

    modules = sorted(CONTEXT.glob("agent-*.js"))
    names = [path.name for path in modules]
    assert names == [
        "agent-add-pane.js", "agent-api.js", "agent-dialog-footer.js",
        "agent-dialog-slot.js", "agent-edit.js",
        "agent-fields.js",
        "agent-form-advanced.js", "agent-form-bindings.js",
        "agent-form-choices.js", "agent-form-connections.js", "agent-form-fields.js",
        "agent-form-hydrate.js", "agent-form-save.js",
        "agent-form-template.js", "agent-form.js", "agent-recovery.js",
        "agent-save-template.js", "agent-submit.js", "agent-template-picker.js",
        "agent-templates-api.js",
    ], names
    for path in modules:
        lines = len(_read(path).splitlines())
        assert lines < 400, f"{path.relative_to(JS)} is {lines} lines"

    # The vocabulary has ONE owner. A private THINKING_MODES in the form and
    # another in the submit path is a level the operator sets and the agent
    # never receives.
    for name in ("THINKING_MODES = [", "THINKING_CHOICES = [",
                 "DEFAULT_PROMPT_HISTORY_POLICY = {"):
        owners = [path.name for path in modules if name in _read(path)]
        assert owners == ["agent-fields.js"], f"{name} is declared in {owners}"
    # The desks have no copy in the UI at all: GET /api/map serves
    # core/world/tilemap.py's DEFAULT_DESKS, so growing the map cannot desync
    # the form.
    assert [path.name for path in modules if "DESK_OPTIONS" in _read(path)] == []
    for name in ("THINKING_MODES", "DEFAULT_PROMPT_HISTORY_POLICY"):
        assert f"BossModAgentFields.{name}" in _read(CONTEXT / "agent-submit.js")

    # Requests live in one module; nothing else calls the agent endpoints.
    api = _read(CONTEXT / "agent-api.js")
    for fn in ("fetchAgent", "apiCreateAgent", "apiUpdateAgent", "apiDeleteAgent",
               "fetchPromptHistoryPolicy", "apiUpdatePromptHistoryPolicy",
               "apiClearChatHistory", "apiResetRuntime",
               "fetchCatalog"):
        assert f"async function {fn}(" in api, f"agent-api.js lost {fn}"
        assert f"{fn}," in api.rsplit("return {", 1)[-1], f"agent-api.js does not export {fn}"

    # The dead canvas refresh went with the split: OfficeCanvas has not been a
    # global since Phase 3B, so `refreshCanvas` fetched /api/world on every
    # save and threw the answer away behind a guard that can never be true.
    for path in modules:
        source = _read(path)
        assert "refreshCanvas" not in source, f"{path.name} kept the dead canvas refresh"
        assert "OfficeCanvas" not in source, f"{path.name} names a retired global"

    # The three window.confirm calls the form inherited are gone: the guard is
    # kept, but through the focus-trapped dialog (spec 8.4).
    recovery = _read(CONTEXT / "agent-recovery.js")
    assert "BossModOverlays.createModal(" in recovery
    for path in modules:
        source = _read(path)
        assert "confirm(" not in source.replace("confirmDestructive(", ""), (
            f"{path.name} still uses the native dialog"
        )
    # The delete confirmation travelled with the form's wiring when that split
    # out of agent-edit.js; the copy itself is what must survive, not the file
    # it sits in.
    guarded = recovery + _read(CONTEXT / "agent-edit.js") + _read(CONTEXT / "agent-form-save.js")
    for copy in ("Clear chat history", "Reset runtime", "Delete agent"):
        assert copy in guarded, copy


def _delete_warnings() -> dict:
    """What context/agent-api.js says before a delete, evaluated for real."""
    probe = (
        "const fs = require('fs');"
        "eval(fs.readFileSync(process.argv[1], 'utf8') + ';global.Api = BossModAgentApi;');"
        "let refusesNoName = false;"
        "try { Api.agentDeleteWarning(''); } catch (err) { refusesNoName = true; }"
        "process.stdout.write(JSON.stringify({"
        "one: Api.agentDeleteWarning('Jim'), all: Api.allAgentsDeleteWarning(), refusesNoName}));"
    )
    result = subprocess.run(
        ["node", "-e", probe, str(CONTEXT / "agent-api.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_the_delete_warning_says_what_is_destroyed() -> None:
    """Every door to a delete says what goes, and to back it up first.

    The desk's Remove used to promise that artifacts and diagnostics were
    preserved while the delete removed both. The copy now has one owner,
    agent-api.js beside apiDeleteAgent, and each entry point reads it from
    there. Settings' delete-all keeps its native confirm(), so what the
    operator sees there is exactly the string it is handed.
    """
    warnings = _delete_warnings()
    assert warnings["one"].startswith("Deleting Jim permanently deletes their files")
    assert warnings["all"].startswith("Deleting all agents permanently deletes every agent")
    for text in (warnings["one"], warnings["all"]):
        assert "back up anything you need" in text
        assert "cancels their open tasks" in text
        assert "preserved" not in text
    # A warning about "Deleting undefined" would not say whose files go.
    assert warnings["refusesNoName"] is True

    desk = _read(CONTEXT / "desk-actions.js")
    assert "BossModAgentApi.agentDeleteWarning(detail.name)" in desk
    assert "diagnostics are preserved" not in desk
    save = _read(CONTEXT / "agent-form-save.js")
    assert "BossModAgentApi.agentDeleteWarning(agent.name)" in save
    advanced = _read(JS / "settings" / "settings-advanced.js")
    assert "if (!confirm(BossModAgentApi.allAgentsDeleteWarning())) return;" in advanced


def test_the_desk_and_the_form_render_the_delete_warning() -> None:
    """The warning is what the Remove and Delete dialogs actually show.

    Read off the rendered dialogs, because a source check passes while the
    wrong string reaches the screen. Remove also has no dialog before the
    agent's name has loaded: the footer says why and the read is retried.
    """
    payload = _harness()
    # Diagnostics opens the Log filtered to this agent, by the Log's own
    # param name (`agentId`), not the Tasks one.
    assert payload["diagnosticsFiltersTheLog"] is True
    assert payload["removeWarnsWhatIsDeleted"] is True
    assert payload["removeWaitsForTheName"] is True
    assert payload["removeOpensOnceTheNameIsIn"] is True
    assert payload["deleteWarnsWhatIsDeleted"] is True
    # ...and a Remove that lands closes the desk of the agent it deleted.
    assert payload["removeClosesTheDesk"] is True


def test_a_desk_task_opens_as_a_layer_over_the_desk() -> None:
    """A task row opens the task OVER the desk, and ‹ comes back to it.

    It used to navigate to the Tasks place, which closed the desk with no way
    back. The row now opens the Tasks place's own detail as a layer, through
    places/tasks/task-layers.js: the head's trail reads `Jim › Write TDD
    specs`, a subtask link is a third crumb, a cancel re-reads the desk's rows,
    and a list that cannot be read is said on the desk. Leaving — another
    desk, a removal, a close — takes everything stacked on the desk with it.
    """
    payload = _harness()
    for key in (
        "taskRowOpensTheTask", "aSubtaskPushesAThirdCrumb", "aCancelRefreshesTheDeskRows",
        "taskBackReturnsToTheDesk", "aFailedTaskListIsSaidOnTheDesk",
        "anotherDeskClosesTheWholeStack", "aRemovalClosesTheWholeStack",
        "closingTheDeskClosesTheViewerOverIt", "seeAllOpensTheAgentsTasks",
        "chatToolOpensTheConversation",
    ):
        assert payload[key] is True, key
    panel = _read(CONTEXT / "desk-panel.js")
    assert "navigate('tasks', { taskId })" not in panel
    assert "onOpenChat" not in panel
    assert "() => openConversation(agentId, 'agent')" in panel
    assert "onOpenTask: (taskId) => { void taskOpener.open(taskId); }," in panel
    opener = _read(CONTEXT / "desk-task-opener.js")
    assert "BossModTaskLayers.create({" in opener
    assert "loadedTasks = await BossModTasksData.loadTasks(api);" in opener
    assert "tasks.showError('Could not open that task.');" in opener
    dialog = _read(CONTEXT / "desk-dialog.js")
    assert "if (current) current.modal.closeFrom();" in dialog
    assert "openConversation: (id, kind) => {" in dialog


def test_desk_schedules_section_and_layer() -> None:
    """The desk's Schedules section and the schedule layer, through the real desk.

    The section renders rows (title, summary, next run, a missed-run tone),
    its empty and error states, and retries; it repaints on a
    ``schedule_ran``/``schedule_changed`` activity for its own agent only. A
    row opens the layer over the desk, and an edit PATCHes only the changed
    field; New opens the layer in edit mode, refuses weekly with no weekday
    before any request, then POSTs the exact rule shape. Edit mode keeps the
    view's facts list, with the recurrence editor, the preview and the Notify
    dropdown in the Repeats, Next run and Notify cells; times are typed into
    text fields, shown formatted, and an unreadable one is said, not sent.
    Leaving the desk
    closes an open schedule layer. Row times come from
    ``BossModFormat.formatClockTime``; the lock reads "Agent can manage this
    task"; refusals surface the server's sentence for a string, a 422 and
    Run now's structured 409 ``detail``.
    """
    payload = _harness()
    for key in (
        "scheduleSectionRendersRows", "scheduleSectionSaysEmpty", "scheduleSectionSaysError",
        "scheduleRetryRecovers", "otherActivityIsIgnored", "aScheduleRunRefreshesTheRows",
        "aRowOpensTheScheduleLayer", "anEditPatchesOnlyWhatChanged", "newOpensInEditMode",
        "weeklyNeedsAWeekday", "aCreatePostsTheExactRule", "leavingTheDeskClosesTheScheduleLayer",
        "repeatModeShowsOnlyItsControls", "anOvernightWindowIsRefusedHere", "aRepeatPostsMinutesAndAWindow",
        "aStoredRepeatEditsInHours", "runNowIsDisabledWhileRunning", "runNowStartsARealRun",
        "anOpenRunRefusalSaysWhy", "theDraftIsPreviewed", "anInvalidDraftIsSaidNotSent", "aNewScheduleCanBeSavedOff",
        "anUnfinishedSaveSaysWhy",
        "anOpenRunShowsThePausedTone", "aSkipOffersTheOpenRun",
        "theLockAndTheAuthorShow", "setUpByShows", "theLockSwitchPatches", "createSendsTheLock",
        "timesUseTheSharedClock", "scheduleRefusalsSayWhy",
        "aNewScheduleEditsInPlace", "anEditKeepsTheFactsInPlace", "anInvalidTypedTimeIsSaidNotSent",
        "aTypedTimeIsShownFormatted", "scheduleInstructionsUncapped",
    ):
        assert payload[key] is True, key
    panel = _read(CONTEXT / "desk-panel.js")
    assert "section('Schedules', { label: 'New', icon: 'plus', onSelect: () => schedules.openNew() }," in panel
    assert "schedules.destroy();" in panel
    fields = _read(CONTEXT / "schedule-fields.js")
    assert "BossModMenuSelect.create({" in fields
    # Dropdowns are BossModMenuSelect; no module builds a native select.
    for name in ("schedule-api.js", "schedule-view.js", "schedule-fields.js", "schedule-preview.js",
                 "schedule-layer.js", "desk-schedules.js"):
        assert "h('select'" not in _read(CONTEXT / name), name
        # The webview's native date, time and number inputs have no picker and
        # read as disabled grey boxes; the editor uses core/date-field.js,
        # core/time-field.js and numeric text fields instead.
        for native in ("type: 'date'", "type: 'time'", "type: 'number'"):
            assert native not in _read(CONTEXT / name), f"{name} builds a native {native}"


def test_context_modules_stay_focused() -> None:
    """The cap moved tree-wide in Phase 4; what stays here is context/'s own.

    test_ui_index.py::test_every_module_stays_under_the_line_cap owns the 300
    lines now, for every directory at once. The rule that is specific to this
    one is the markup boundary: the desk renders file names and agent output,
    so everything here builds nodes with h() — except the three form modules
    covered by the named exemption, which render operator-entered config.
    """
    exempt = {"agent-form-fields.js", "agent-form-advanced.js", "agent-form.js"}
    for path in sorted(CONTEXT.rglob("*.js")):
        source = _read(path)
        if path.name in exempt:
            assert "MARKUP EXEMPTION" in source, path.name
            continue
        assert "innerHTML" not in source, f"{path.name} builds markup from a string"
        assert "insertAdjacentHTML" not in source, f"{path.name} injects markup"
        # Dependencies arrive by injection; a probe for a global is how app.js
        # silently no-opped when a module failed to load.
        assert "typeof BossMod" not in source, f"{path.name} probes for a global"


def test_desk_files_guard_stale_loads() -> None:
    """Re-points the desk half of test_ui_load_generation.py.

    Both guards are load-bearing and both are kept: the generation drops a
    response for an agent the operator has left, and the active-path check
    drops one for a folder they have navigated out of. Either alone still
    paints a stale listing over the folder actually on screen.
    """
    source = _read(CONTEXT / "desk-files.js")
    assert "const load = BossModGates.createLoadGeneration()" in source
    assert "const loadId = load.next()" in source
    assert "load.isCurrent(loadId) && activePath === requestedPath" in source

    body = source.split("async function open(path) {", 1)[1]
    assert body.index("const loadId = load.next()") < body.index("await api(deskUrl(")
    assert body.index("await api(deskUrl(") < body.index("if (!isLive(loadId, requestedPath)) return;")
    # Both failure and success go through the same guard before painting.
    assert body.count("if (!isLive(loadId, requestedPath)) return;") == 2

    # Entry names are file names off disk; they are never interpolated markup.
    assert "innerHTML" not in source
    assert "BossModDom" in source
    # The controls the operator knows, by the ids they have always had.
    for control in ("desk-root-switch-btn", "desk-open-parent-btn",
                    "desk-open-folder-btn", "desk-refresh-btn"):
        assert control in source, f"the desk browser lost {control}"
    # The rows and crumbs are drawn by the shared core/file-listing.js (the
    # task file picker is its second user); the desk still owns the copy.
    assert "LISTING.breadcrumbs(payload.breadcrumbs" in source
    assert "LISTING.entries(payload.entries" in source
    listing = _read(JS / "core" / "file-listing.js")
    for name in ("desk-entry", "desk-crumb"):
        assert f"class: '{name}'" in listing
    assert "This folder is empty." in source
    # A live write repaints the folder on screen — the auto-refresh that used
    # to hang off the desk cache in agent-context.js.
    assert "bus.subscribe('chat_message'" in source
    assert "data.desk_path" in source

    opener = _read(CONTEXT / "desk-opener.js")
    assert "desk_open_folder_handler_required" in opener
    assert "desk_open_folder_handler_invalid" in opener
    assert "/api/settings/desktop_open_folder_handler" in opener
    assert "BossModOverlays.createModal(" in opener
    # Exactly one retry: a second 409 is a real failure, not another prompt.
    assert "return attempt(false);" in opener


def test_desk_panel_keeps_role_contract_copy() -> None:
    """Re-points test_role_contracts.py's agent-context.js block.

    These strings are the operator's only view of what "done" means for an
    agent and for each of their tasks. They moved with the desk; none of them
    was dropped.
    """
    panel = _read(CONTEXT / "desk-panel.js")
    tasks = _read(CONTEXT / "desk-tasks.js")

    assert "No specialty" in panel
    assert "done_fail_bar" in panel
    assert "who.description" in panel, "the agent's about line must survive"
    assert "What done looks like for this agent:" in panel

    # The per-task done-claim guidance left the desk's rows on purpose: every
    # open row repeated three lines of it. It is the task detail's contract
    # section now, one click away from a desk row (task rows open the task).
    detail_sections = _read(JS / "places" / "tasks" / "task-detail-sections.js")
    assert "doneClaimGuidance" in detail_sections
    assert "Blocked — checkable claim missing" in detail_sections
    assert "onOpenTask(task.id)" in tasks
    assert "scope=self" in tasks and "scope=owned" in tasks
    assert "const load = BossModGates.createLoadGeneration()" in tasks

    # Remove and Reset runtime destroy work; neither may be a click-through.
    footer = _read(CONTEXT / "desk-actions.js")
    assert "BossModOverlays.createModal(" in footer
    remove = footer.split("async function removeAgent() {", 1)[1]
    assert "method: 'DELETE'" in remove
    for spec in ("REMOVE_TITLE", "RESET_TITLE"):
        assert f"title: {spec}," in footer, f"{spec} must gate its action"
    # The API is never reached from the click itself, only from the modal's
    # confirm action. The desk's `⋯` rows call the two confirm runners, and
    # this module renders no button that could reach the API another way.
    for runner in ("function confirmReset() {", "function confirmRemove() {"):
        assert "confirmThen({" in footer.split(runner, 1)[1].split("\n        }\n", 1)[0], runner
    assert "onclick" not in footer
    panel = _read(CONTEXT / "desk-panel.js")
    assert "pick(() => actions.confirmReset()), { danger: true })" in panel
    assert "pick(() => actions.confirmRemove()), { danger: true }))" in panel
    assert "onclick: () => { void removeAgent(); }" not in footer
    assert "onclick: () => { void resetRuntime(); }" not in footer
    # Cancel is last, so it holds focus and Esc and Enter agree.
    confirm = footer.split("function confirmThen(spec) {", 1)[1]
    assert confirm.index("tone: 'danger'") < confirm.index("label: 'Cancel'")


def test_desk_toggle_and_open_desk_are_injected() -> None:
    """The Desk toggle and the capability behind it ship together."""
    source = _read(CONVERSATION / "sources" / "agent-source.js")
    assert "typeof ctx.openDesk === 'function'" in source
    assert "id: 'conversation-desk-toggle'" in source
    assert "label: 'Desk'" in source
    assert "ctx.openDesk(agentId)" in source
    # The guard comes before the action is pushed, not after.
    chrome = source.split("function chrome() {", 1)[1]
    assert chrome.index("typeof ctx.openDesk === 'function'") < chrome.index(
        "id: 'conversation-desk-toggle'"
    )

    # The capability reaches the source through the controller.
    conversation = _read(CONVERSATION / "conversation.js")
    assert "api, bus, store, presence, openDesk," in conversation

    place = _read(JS / "places" / "chat" / "chat-place.js")
    # The capability is the shell's one desk modal, handed down as ctx.openDesk
    # and passed straight through — the conversation's lamp, a deliverable the
    # desk opens on, a mention's View Desk and the summary's seats all open it.
    assert "openDesk: ctx.openDesk," in place
    assert "onViewDesk: (agent) => ctx.openDesk(agent.id)" in place
    navigator = _read(JS / "shell" / "navigator.js")
    assert "openDesk," in navigator.split("const ctx = {", 1)[1].split("};", 1)[0]
    dialog = _read(CONTEXT / "desk-dialog.js")
    assert "BossModOverlays.createModal({" in dialog
    assert "size: 'panel'," in dialog
    assert "initialPath: path || '/me'," in dialog

    # Hiring selects the new agent's conversation — and deliberately NOT their
    # desk, which as a modal would spring open over the chat the operator just
    # landed in. The create flow is the Agents dialog's Add agent pane, and the
    # Edit role dialog is edit-only, so the routing lives in the pane and the
    # edit's save routes nowhere.
    edit = _read(CONTEXT / "agent-edit.js")
    # Phase 4 split agent-panel.js away; renderInline is this module's own now.
    assert "void renderInline({ container: formEl, agent, primary, onSave, onDelete })" in edit
    assert "conversationId" not in edit, "an edit leaves the operator where they were"
    pane = _read(CONTEXT / "agent-add-pane.js")
    # A snapshot pick carries its values in as `prefill`; the create is still
    # a create, so `agent` stays null whichever cell was picked.
    assert "container: formEl, agent: null, prefill, primary, onSave," in pane
    saved = pane.split("function onSave(savedAgent) {", 1)[1]
    assert "if (savedAgent) {" in saved
    assert "conversationKind: 'agent'" in saved
    saved_body = saved.split("onDone();", 1)[0]
    for desk_key in ("contextMode", "deskAgentId", "deskPath"):
        assert desk_key not in saved_body, f"a create must not open the desk ({desk_key})"
    assert _harness()["createOpensTheConversationOnly"] is True

    # Round three made hire and edit one centred dialog, and the column that
    # hosted a form mode is gone altogether now. The property these lines
    # guarded — hiring has exactly one entry point and it lands somewhere
    # real — is asserted on the new one.
    assert "AgentEdit" not in place, "the Chat place hosts no form"
    assert "placeParams.hire" not in place
    shell_source = _read(JS / "shell" / "shell.js")
    assert "onHire:" in shell_source
    # The row opens the two-door menu; the dialog is one of the doors, and the
    # menu module is where that call now lives.
    assert "addAgent.toggle();" in shell_source
    assert "BossModAgentsDialog.open({ store, tab: 'add' })" in _read(
        JS / "shell" / "add-agent-menu.js")

    # No desk state in the store or the session: a modal is transient UI, and
    # where its file browser opens is an argument to the one door into it.
    shell = _read(JS / "shell" / "shell.js")
    state = shell.split("const INITIAL_STATE = {", 1)[1].split("};", 1)[0]
    for desk_key in ("contextMode", "deskAgentId", "deskPath"):
        assert desk_key not in state, desk_key
    session = _read(JS / "shell" / "session.js")
    persisted = session.split("PERSISTED_KEYS = Object.freeze([", 1)[1].split("]);", 1)[0]
    assert "contextMode" not in persisted
    panel = _read(CONTEXT / "desk-panel.js")
    assert "s.deskPath" not in panel
    assert "void files.open(initialPath);" in panel


def test_recreating_a_recent_agent_fills_the_form_and_still_creates() -> None:
    """Spec §3.2, driven through the REAL builders and the real submit path.

    `agent` answered two questions at once: what the fields show, and who the
    form is for. A snapshot answers only the first — the agent it was taken
    from may have been deleted — so it arrives as `prefill`, and the proof is
    on both sides of that seam: every field the snapshot carries is filled
    (including the colour, which a missing radio would silently rewrite), and
    none of what identity decides is there — no Delete, no recovery tools, no
    runtime pill — while the save is a `POST /api/agents`.

    Two things a snapshot cannot promise are named rather than dropped: a
    thinking level its connection no longer offers is listed under the AI
    Connection section (and its control falls back to Server default). AI
    Personalities are retired, so a recreate carries no personality control,
    no kept prompt and no template link.
    """
    payload = _harness()
    for key in (
        "recreateFillsTheFormFromTheSnapshot", "recreateIsACreateNotAnEdit",
        "theUnofferedLevelIsNamed", "recreateCarriesNoPersonality",
        "recreateSavesAsACreate",
    ):
        assert payload[key] is True, key
    # The policy a recreate starts from is the SNAPSHOT'S, and the one fallback
    # is stated where it is taken (spec §5.3).
    form = _read(CONTEXT / "agent-form.js")
    policy = form.split("function prefillPolicy(prefill) {", 1)[1].split("\n    }", 1)[0]
    assert "if (!prefill.prompt_history_policy) return { ...DEFAULTS };" in policy
    assert "The one fallback here, and it is stated" in policy
    assert "const promptHistoryPolicy = prefill ? prefillPolicy(prefill) : loadedPolicy;" in form
    # The personality vocabulary is gone from every half of the form.
    fields = _read(CONTEXT / "agent-fields.js")
    assert "KEPT_PERSONALITY" not in fields
    choices = _read(CONTEXT / "agent-form-choices.js")
    assert "personality" not in choices.lower()
    advanced = _read(CONTEXT / "agent-form-advanced.js")
    assert 'name="prompt_template_kept"' not in advanced
    submit = _read(CONTEXT / "agent-submit.js")
    assert "/api/personalities" not in submit
    assert "agentData.template_id = formData.get('template_id') || null;" in submit
    # The notes are the connections module's, from the one reading of the
    # stored values that the markup and the bindings share.
    conn = _read(CONTEXT / "agent-form-connections.js")
    assert "function startingValues(values, connections)" in conn
    assert "this connection doesn't offer it; pick one." in conn
    assert "const start = startingValues(values, connections);" in conn
    bindings = _read(CONTEXT / "agent-form-bindings.js")
    assert "const start = CONNECTIONS.startingValues(values, connections);" in bindings


def test_the_desk_names_the_pack_and_offers_its_update() -> None:
    """The desk's pack line (context/desk-pack.js), driven through the real desk.

    An agent hired from a pack says which, at which catalog commit, under its
    status in About. When the installed template is newer it offers Update
    behind the standard confirm layer, which names what is replaced and warns
    when the agent's contract was edited. The update sends back the template
    hash that was on screen; an agent not hired from a pack has no line.
    """
    payload = _harness()
    for key in (
        "thePackLineNamesThePackAndTheUpdate", "thePackConfirmWarnsAboutEdits",
        "thePackUpdateSendsTheHashItShowed", "anUnlinkedAgentHasNoPackLine",
    ):
        assert payload[key] is True, key
