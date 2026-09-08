"""The context column: who the operator is talking to, and their desk."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CONTEXT = JS / "context"
CONVERSATION = JS / "conversation"
NEEDS = JS / "needs"
HARNESS = Path(__file__).resolve().parent / "js_context_harness.cjs"

# The order the context harness evaluates its modules in.
CONTEXT_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "switch.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "specialty.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    JS / "core" / "overlays.js",
    CONVERSATION / "empty-state.js",
    CONVERSATION / "transcript.js",
    CONVERSATION / "transcript-cache.js",
    CONVERSATION / "message.js",
    CONVERSATION / "event-cards.js",
    CONVERSATION / "chrome.js",
    CONVERSATION / "composer.js",
    CONVERSATION / "system-receipts.js",
    NEEDS / "need-shape.js",
    NEEDS / "needs-store.js",
    NEEDS / "needs-bar.js",
    CONVERSATION / "sources" / "thread-archive.js",
    CONVERSATION / "sources" / "thread-source.js",
    CONVERSATION / "sources" / "agent-source.js",
    CONVERSATION / "conversation.js",
    JS / "shell" / "places.js",
    # The shared viewer the desk browser opens, with the two modules it is
    # built from. Re-pointed in Phase 3B: it is places/files/file-viewer.js now.
    JS / "places" / "files" / "file-content.js",
    JS / "places" / "files" / "file-form.js",
    JS / "places" / "files" / "file-ops.js",
    JS / "places" / "files" / "file-viewer.js",
    CONTEXT / "mini-office.js",
    CONTEXT / "desk-opener.js",
    CONTEXT / "desk-files.js",
    CONTEXT / "desk-notes.js",
    CONTEXT / "desk-tasks.js",
    CONTEXT / "desk-actions.js",
    CONTEXT / "agent-api.js",
    CONTEXT / "agent-fields.js",
    CONTEXT / "agent-form-fields.js",
    CONTEXT / "agent-form-advanced.js",
    CONTEXT / "agent-form-bindings.js",
    CONTEXT / "agent-form.js",
    CONTEXT / "agent-submit.js",
    CONTEXT / "agent-recovery.js",
    CONTEXT / "agent-edit.js",
    CONTEXT / "desk-panel.js",
    CONTEXT / "context-column.js",
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
    """
    payload = _harness()
    assert payload["drainsOnDestroy"] is True
    assert payload["switchesModes"] is True

    place = _read(JS / "places" / "chat" / "chat-place.js")
    assert "BossModContextColumn.createContextColumn(" in place
    assert "contextColumn.destroy()" in place
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


def test_mini_office_groups_by_location_including_unknown() -> None:
    """An off-map agent still gets a seat (spec 7).

    `agent.location` is a room NAME derived from coordinates, and it is absent
    for an agent the world could not place. Grouping them away rather than
    under a real heading would hide them from the operator completely.
    """
    payload = _harness()
    assert payload["rendersUnknownRoom"] is True
    assert payload["seatOpensDesk"] is True

    source = _read(CONTEXT / "mini-office.js")
    assert "UNPLACED_ROOM = 'Unknown'" in source
    assert "agent.location" in source
    # A summary, not a second canvas: no tilemap, no coordinates, no map fetch.
    for forbidden in ("getContext", "/api/map", "'canvas'", "agent.x", "agent.y"):
        assert forbidden not in source, f"the mini office must not render a map ({forbidden})"


def test_desk_notes_read_the_workspace_not_a_column() -> None:
    """Spec 7, resolved: Notes is a surfacing problem, not a data one.

    Two phases carried "Notes" as a requirement no column could satisfy, and
    Phase 2B put the done/fail bar in the slot as a stand-in. The operator's
    answer was that agents already write markdown into their own workspace, so
    the panel reads `/me/notes` and opens what it finds in the one viewer.

    The half that matters most is the distinction: a new agent has written
    nothing, so the folder 404s, and that is the EMPTY state. Rendering it as
    an error would tell every operator their brand new agent was broken. A
    genuine failure still has to look like one, which is why both are proven.
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
    # 404 is absence. It must be checked BEFORE the generic !res.ok branch, or
    # a new agent's desk reports a failure that never happened.
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
    assert panel.index("const bar = who.done_fail_bar") < panel.index("desk-bar")
    # The section is drained with the rest of the panel.
    assert "notes.destroy();" in panel


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
        "agent-api.js", "agent-edit.js", "agent-fields.js",
        "agent-form-advanced.js", "agent-form-bindings.js",
        "agent-form-fields.js", "agent-form.js", "agent-recovery.js",
        "agent-submit.js",
    ], names
    for path in modules:
        lines = len(_read(path).splitlines())
        assert lines < 300, f"{path.relative_to(JS)} is {lines} lines"

    # The vocabulary has ONE owner. A private MODEL_TYPES in the form and
    # another in the submit path is a connection the operator sets and the
    # agent never receives.
    for name in ("MODEL_TYPES = [", "DEFAULT_PROMPT_HISTORY_POLICY = {", "DESK_OPTIONS = ["):
        owners = [path.name for path in modules if name in _read(path)]
        assert owners == ["agent-fields.js"], f"{name} is declared in {owners}"
    for name in ("MODEL_TYPES", "DEFAULT_PROMPT_HISTORY_POLICY"):
        assert f"BossModAgentFields.{name}" in _read(CONTEXT / "agent-submit.js")

    # Requests live in one module; nothing else calls the agent endpoints.
    api = _read(CONTEXT / "agent-api.js")
    for fn in ("fetchAgent", "apiCreateAgent", "apiUpdateAgent", "apiDeleteAgent",
               "fetchPromptHistoryPolicy", "apiUpdatePromptHistoryPolicy",
               "apiClearChatHistory", "apiResetRuntime"):
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
    for copy in ("Clear chat history", "Reset runtime", "Delete agent"):
        assert copy in recovery or copy in _read(CONTEXT / "agent-edit.js"), copy


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
    for name in ("desk-entry", "desk-crumb"):
        assert f"class: '{name}'" in source
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

    assert "doneClaimGuidance" in tasks
    assert "Blocked — checkable claim missing" in tasks
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
    # confirm action.
    assert "onclick: () => confirmThen({" in footer
    assert "onclick: () => { void removeAgent(); }" not in footer
    assert "onclick: () => { void resetRuntime(); }" not in footer
    # Cancel is last, so it holds focus and Esc and Enter agree.
    confirm = footer.split("function confirmThen(spec) {", 1)[1]
    assert confirm.index("tone: 'danger'") < confirm.index("label: 'Cancel'")


def test_desk_toggle_and_open_desk_are_injected() -> None:
    """The Desk toggle and the capability behind it ship together.

    `ctx.openDesk` is an optional, documented capability (spec 4.1). The agent
    source renders the toggle only when it is injected, and Phase 2B is the
    phase that injects it — the same rule event-cards.js follows for its
    "Open in Desk" affordance.
    """
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
    assert "openDesk:" in place
    column = _read(CONTEXT / "context-column.js")
    assert "function openDeskFrom(store, target)" in column
    assert "deskPath: typeof target === 'string' && target.startsWith('/')" in column

    # Hiring lands in the column's create mode and selects the new agent.
    edit = _read(CONTEXT / "agent-edit.js")
    # Phase 4 split agent-panel.js away; renderInline is this module's own now.
    assert "void renderInline(formEl, agent || null, onSave, onDelete)" in edit
    assert "const wasCreating = !agent;" in edit
    saved = edit.split("function onSave(savedAgent) {", 1)[1]
    assert "savedAgent && wasCreating" in saved
    assert "conversationKind: 'agent'" in saved
    assert "deskAgentId: savedAgent.id" in saved
    assert "if (mode === 'desk' && !agentId)" in column
    assert "placeParams.hire === true" in place

    # deskPath is a real store key with a real consumer, not dead state.
    shell = _read(JS / "shell" / "shell.js")
    assert "deskPath: null," in shell
    panel = _read(CONTEXT / "desk-panel.js")
    assert "store.subscribe((s) => s.deskPath" in panel
    assert "files.open(store.getState().deskPath || '/me')" in panel
