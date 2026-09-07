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
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "utils.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    JS / "core" / "overlays.js",
    CONVERSATION / "transcript.js",
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
    CONTEXT / "desk-tasks.js",
    CONTEXT / "desk-actions.js",
    JS / "agent-panel.js",
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
    shell = _read(JS / "shell" / "shell.js")
    assert "contextEl: requireElement('app-context')" in shell
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


def test_context_modules_stay_focused() -> None:
    for path in sorted(CONTEXT.rglob("*.js")):
        lines = len(_read(path).splitlines())
        assert lines < 300, f"{path.relative_to(JS)} is {lines} lines"


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
    assert "AgentPanel.renderInline(" in edit
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
