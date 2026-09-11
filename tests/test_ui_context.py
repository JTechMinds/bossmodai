"""The context column: who the operator is talking to, and their desk."""

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
    JS / "core" / "dom.js",
    JS / "core" / "markdown.js",
    JS / "core" / "avatar.js",
    JS / "core" / "switch.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "specialty.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    CONVERSATION / "empty-state.js",
    CONVERSATION / "transcript.js",
    CONVERSATION / "transcript-cache.js",
    CONVERSATION / "message.js",
    CONVERSATION / "event-cards.js",
    CONVERSATION / "title-rename.js",
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
    CONTEXT / "agent-templates-api.js",
    CONTEXT / "agent-fields.js",
    CONTEXT / "agent-form-fields.js",
    CONTEXT / "agent-form-advanced.js",
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
    CONTEXT / "agent-template-picker.js",
    CONTEXT / "agent-form-template.js",
    CONTEXT / "agent-dialog-footer.js",
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
    # A summary, not a second canvas. The ban used to include /api/map itself,
    # which was the right property spelled through the wrong proxy: the room
    # LIST has to come from the floor plan or the panel only ever draws the
    # rooms somebody is standing in. What must stay out is the GEOMETRY — the
    # tiles, the dimensions, the desks, and every coordinate — because that is
    # what would make this a second renderer instead of a summary.
    assert "mapData.rooms" in source
    for forbidden in ("getContext", "'canvas'", "agent.x", "agent.y",
                      "mapData.tiles", "mapData.width", "mapData.height",
                      "mapData.desks", "bounds"):
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


def test_set_all_fans_out_through_the_published_form() -> None:
    """The convenience control that was silently dead, and the five nulls.

    "Set All" is bound while the form sits on a DETACHED stage and it runs
    after `renderInline` has published — and publishing MOVES the `<form>` out
    of that stage and empties it. Bound to the host it was handed, every
    `querySelector` inside the listener then answered null for the rest of the
    form's life, `if (sel)` swallowed it, and nothing said a word.

    That is not a cosmetic loss. `model_all` is the required AI question until
    the matrix has been answered, and `buildSubmitData` reads the five
    `model_*` selects and never `model_all` — so the fan-out IS the mechanism
    by which answering that one control gives the agent any connection at all.
    Answer the required select, click Create, and the
    agent was written with five null models, no connection_id and no
    api_base_url, over the words "Saved successfully".

    Run against the REAL builder, the REAL publish and the REAL submit path:
    tests/js_add_agent_harness.cjs stubs `BossModAgentForm` wholesale, which is
    why this sailed through a green suite once already.

    It drives BLANK, which is the full form. The quick layout the paragraph
    above describes — the promotion, the guard on it, and the fan-out through
    it into a real save — is
    test_the_quick_path_guard_survives_a_disclosure_and_lets_an_answer_through;
    this one holds the binding rule both of them stand on.
    """
    payload = _harness()
    assert payload["setAllFansOutAfterPublish"] is True
    assert payload["theFanOutIsWhatIsSaved"] is True
    # The rule, where it is made: the `<form>` is the node that survives being
    # published out of its host, so it is the node every binding holds.
    form = _read(CONTEXT / "agent-form.js")
    assert "const form = container.querySelector('#agent-form');" in form
    # ...and the host is not read again after that one line. Everything the
    # builder binds, it binds off the form.
    bindings = form.split("if (!form) throw", 1)[1]
    assert "container" not in bindings


def test_the_quick_path_guard_survives_a_disclosure_and_lets_an_answer_through() -> None:
    """The rule that decides whether a quick create gets a connection at all.

    The quick layout promotes `model_all` to the ONE required AI question and
    sweeps the five `model_*` selects behind a collapsed disclosure, and
    `buildSubmitData` reads those five and never `model_all`. So `required` on
    that select is the cheap gate, and the rule for when it comes off had it
    backwards: it came off when a disclosure was OPENED. That panel was where a
    template's specialty, description and what-done lived, so opening it to
    read them — the interaction the layout invited — disarmed the guard, and
    closing it again did not put it back. Type a name, click Create, and the
    agent was written with five null models, no connection_id and no
    api_base_url, over the words "Saved successfully".

    The corrected rule (spec 8.3) tracks the ANSWER: required until at least
    one of the five holds a value, re-armed when they are all cleared back to
    None, live on change in both directions. All four halves are here —
    open-and-close keeps it, one per-type select releases it, clearing them
    re-arms it, and the fan-out through "Set All" releases it and SAVES what it
    wrote.

    The layout that made the original defect reachable is gone: nothing a
    template fills is hidden any more, so the two paths are one form and the
    guard sits on a control that is always on screen. The open-and-close half
    is kept and re-pointed at the one disclosure that remains (Advanced),
    because the property it pins is not about any particular panel — a panel
    toggle is not an answer.

    Driven through the real builder, the real publish, the real
    `buildSubmitData` and the real POST. tests/js_add_agent_harness.cjs stubs
    the form and the submit path, which is where this class of defect has
    hidden three times; what the attribute buys — a refused submit — is native
    constraint validation, so the attribute itself is what the fake can read.
    """
    payload = _harness()
    for key in ("theTemplateFormAsksForAConnection",
                "readingTheDisclosureKeepsTheGuard",
                "answeringTheMatrixReleasesTheGuard",
                "clearingTheMatrixRearmsTheGuard",
                "theQuickFanOutReleasesTheGuard",
                "theQuickCreateSavesTheConnection"):
        assert payload[key] is True, key
    # ...and the layout that made this defect possible is gone with it: a
    # template no longer hides the five selects or the colour swatches behind
    # anything. The guard is on the visible "Set All" now, and the panel the
    # old rule watched (Advanced) is not where any template field lives.
    assert payload["nothingIsHiddenFromATemplate"] is True


def test_a_create_with_no_ai_connection_is_refused_at_the_create() -> None:
    """The fifth route, and the last one: the invariant left the controls.

    Five UI routes each produced an agent with no connection on any activation
    type — none configured, a failed `/api/connections` read, the Set All
    fan-out dying at publish, the disclosure toggle disarming the guard, and
    this one. Each was fixed at the control that exposed it and a new one
    appeared, always ending the same way: an agent that fails on its first turn
    while the operator is told "Saved successfully".

    This route is what proves a control can never be the guarantee. Answer the
    lifted AI select — the fan-out fills the five, the guard releases — then
    open "Review & customise" and set all five back to None. The guard re-arms
    and it buys nothing: `required` asks the lifted select for A VALUE, and it
    still holds the one that was answered. Native validation passes and
    `buildSubmitData` writes five nulls.

    So the rule is enforced once (spec 8.3), in `agent-form-save.js`'s submit
    handler, on the built `agentData` rather than on the DOM — what would
    actually be SENT. That handler is the seam because it is the last point
    that still knows the save is a create; `buildSubmitData` is not, because
    telling a form-level mapper about create-from-edit would push a
    dialog-level rule into it. The field guards stay: they tell the operator
    before they commit, and they are now convenience, not the guarantee.

    Driven end to end through the real builder, the real publish, the real
    `buildSubmitData` and a POST that would have SUCCEEDED — a refusal proven
    against an endpoint that refuses anyway proves nothing.
    """
    payload = _harness()
    # The route is real: armed guard, satisfied anyway.
    assert payload["theRearmedGuardIsAlreadySatisfied"] is True
    # Nothing POSTed, dialog open, draft intact, told why, primary usable — and
    # "told why" now means told the NEXT ACTION that exists on this path: the
    # lifted AI field and "Review & customise", never the "AI Connections"
    # heading, which on the template path is inside the collapsed disclosure
    # and on a connectionless one is not in the document at all.
    assert payload["theFiveNullCreateIsRefused"] is True
    # ...and the same refusal on the BLANK path names the matrix instead, which
    # is what IS on screen there. Asserted per path on purpose: one substring
    # both wordings satisfy would pin nothing, and every single-sentence
    # version of this message has been wrong on one path or the other.
    assert payload["theBlankRefusalNamesTheMatrix"] is True
    # ...and the other shape, where the matrix renders a link to Settings and
    # no select at all: nothing on screen can be chosen, so the only next
    # action is Settings and the sentence says so. An empty connections list is
    # a HEALTHY read, so nothing else refuses this save first — this invariant
    # is the one the create actually meets.
    assert payload["theUnconfiguredRefusalSendsThemToSettings"] is True
    # ...and it is refused EARLIER than that now: with nothing to choose from,
    # the dialog withholds its primary the moment the form lands and names the
    # line that says why. The unanswerable stand-in select this replaces let
    # the operator fill the whole form before native validation stopped them,
    # and it wrote to nothing.
    assert payload["theUnconfiguredCreateIsWithheldNotOffered"] is True
    # A gate, not a dead end: answer it and the same click goes through.
    assert payload["theCorrectedCreateGoesThrough"] is True

    save = _read(CONTEXT / "agent-form-save.js")
    # It reads what would be SENT, keyed off the one owner of the vocabulary.
    assert "isCreating && BossModAgentFields.MODEL_TYPES" in save
    assert "agentData[key] == null" in save
    # ...and it is upstream of the POST it is refusing.
    assert save.index("say('bad', noConnection(form))") < save.index("apiCreateAgent(agentData)")
    # One refusal mechanism, not two: the form's existing feedback line, which
    # is already the live region everything else in this editor reports
    # through. No second announcement channel was invented for this tone.
    assert "say('bad', noConnection(form))" in save
    # The handler picks its sentence from ONE attribute read, and the module
    # that RENDERS the two shapes is the one that names them. A save handler
    # that queried for a section or a select would be carrying a copy of the
    # layout, and the copy is what goes stale.
    assert "NO_CONNECTION_NEXT[BossModAgentFormConnections.aiQuestion(form)]" in save
    # No class, id or section name from either layout below this point: the
    # handler must not be able to name a control, only to ask which shape the
    # form is in. (The old three are in the list too — a re-point that silently
    # dropped them would let the stale vocabulary back in.)
    for layout in ("quick-disclosure", "quick-ai", "Review & customise",
                   "connection-grid", "form-section", "agent-form-grid"):
        assert layout not in save.split("const noConnection", 1)[1], layout
    # The vocabulary has one owner: the module that builds both shapes decides
    # which was built and answers for it later.
    conn = _read(CONTEXT / "agent-form-connections.js")
    assert "function shapeFor(connections)" in conn
    assert "function aiQuestion(form)" in conn
    assert "return (connections || []).length ? MATRIX : UNAVAILABLE;" in conn
    # ...and the assembler writes it from the SAME list the section was built
    # from, so the attribute and the markup cannot disagree.
    form_js = _read(CONTEXT / "agent-form.js")
    assert "BossModAgentFormConnections.AI_QUESTION," in form_js
    assert "BossModAgentFormConnections.shapeFor(connections)," in form_js
    # The mapper stays a mapper. A dialog-level rule inside it would have to be
    # told which of create and edit it was serving.
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

    Driven the same way as the create: the real edit dialog, all five cleared
    to None by hand, and the PATCH watched for on the wire.
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
    """Three of every 3.1 seconds this harness took were an idle timer.

    context/agent-form-save.js hides its "Saved successfully" line three
    seconds after a save lands. That is right, and it is untouched. But the
    harness's own section 3d performs the first successful save it has ever
    run, and a pending timer keeps Node's event loop alive: `main()` finished
    at ~60ms and the process exited at ~3060ms, once for every test body that
    spawns this file — twelve of them, across four files, or about half the
    suite's wall clock.

    The harness registers its timers and drops whatever is still pending once
    the verdict is written. This pins that: the budget is far below the 3s hide
    it is protecting against, and generous enough that it can only fail if a
    timer is holding the process open again.
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
    configured" and "no personalities configured" links to Settings. It had no
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
    # degraded ALL FOUR reads whenever any one of them rejected, so a dead
    # /api/personalities emptied the connections list of an operator who had
    # two. Each read now settles alone and names itself when it fails.
    assert "await Promise.allSettled(requests)" in form
    assert "if (outcome.status === 'rejected') throw outcome.reason;" in form
    assert "failed.push(what);" in form
    assert "Promise.all(requests)" not in form


def test_one_failing_dependency_read_does_not_erase_the_others() -> None:
    """A rejected sibling cost three healthy reads their results.

    `loadFormData` ran its four requests through one `Promise.all` and one
    catch, so a REJECTED request — a network error, an abort — rejected the
    batch and left every list empty. With `/api/personalities` down and
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
        "agent-api.js", "agent-dialog-footer.js", "agent-edit.js",
        "agent-fields.js",
        "agent-form-advanced.js", "agent-form-bindings.js",
        "agent-form-connections.js", "agent-form-fields.js",
        "agent-form-hydrate.js", "agent-form-save.js",
        "agent-form-template.js", "agent-form.js", "agent-recovery.js",
        "agent-submit.js", "agent-template-picker.js", "agent-templates-api.js",
    ], names
    for path in modules:
        lines = len(_read(path).splitlines())
        assert lines < 400, f"{path.relative_to(JS)} is {lines} lines"

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

    # Hiring selects the new agent and opens their desk.
    edit = _read(CONTEXT / "agent-edit.js")
    # Phase 4 split agent-panel.js away; renderInline is this module's own now.
    assert "void renderInline({ container: formEl, agent: agent || null, primary, onSave, onDelete })" in edit
    assert "const wasCreating = !agent;" in edit
    saved = edit.split("function onSave(savedAgent) {", 1)[1]
    assert "savedAgent && wasCreating" in saved
    assert "conversationKind: 'agent'" in saved
    assert "deskAgentId: savedAgent.id" in saved

    # Round three made hire and edit one centred dialog, so the column's
    # form-hosting mode and the navigation that reached it are gone rather than
    # left reachable. The property these two lines guarded — hiring has exactly
    # one entry point and it lands somewhere real — is asserted on the new one.
    assert "if (mode === 'desk' && !agentId)" not in column
    assert "AgentEdit" not in column, "the column hosts no form"
    assert "placeParams.hire" not in place
    shell_source = _read(JS / "shell" / "shell.js")
    assert "onHire:" in shell_source
    # The row opens the two-door menu; the dialog is one of the doors, and the
    # menu module is where that call now lives.
    assert "addAgent.toggle();" in shell_source
    assert "BossModAgentEdit.openAgentModal({ store })" in _read(
        JS / "shell" / "add-agent-menu.js")

    # deskPath is a real store key with a real consumer, not dead state.
    shell = _read(JS / "shell" / "shell.js")
    assert "deskPath: null," in shell
    panel = _read(CONTEXT / "desk-panel.js")
    assert "store.subscribe((s) => s.deskPath" in panel
    assert "files.open(store.getState().deskPath || '/me')" in panel
