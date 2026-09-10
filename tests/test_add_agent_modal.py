"""Add agent: the two-door menu, the template picker, and the quick layout.

Rewritten, not extended. The dialog this file used to describe had a browse
panel and the create form in one scrolling body, with "Browse packs" and
"Start blank" doors above them. Browsing is the marketplace's now, the doors
are a menu on the roster row, and the dialog is two steps over one body — so
every assertion about that copy is gone rather than re-pointed.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CONTEXT = JS / "context"
HERE = Path(__file__).resolve().parent

# The order the add-agent harness evaluates its modules in.
HARNESS_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    JS / "core" / "gates.js",
    CONTEXT / "agent-api.js",
    CONTEXT / "agent-templates-api.js",
    CONTEXT / "agent-fields.js",
    CONTEXT / "agent-form-hydrate.js",
    CONTEXT / "agent-recovery.js",
    CONTEXT / "agent-form-save.js",
    CONTEXT / "agent-template-picker.js",
    CONTEXT / "agent-quick-connection.js",
    CONTEXT / "agent-form-quick.js",
    CONTEXT / "agent-dialog-footer.js",
    CONTEXT / "agent-edit.js",
    JS / "shell" / "add-agent-menu.js",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _harness() -> dict:
    args = ["node", str(HERE / "js_add_agent_harness.cjs")] + [str(p) for p in HARNESS_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_picker_states_two_steps_and_the_quick_layout() -> None:
    """One run of the real dialog, read as a verdict per property.

    The three invariants the design names are in here by name:
    `operatorFieldsUntouched` (applying a template never writes the name, the
    colour or a connection), `noCreateOnStepOne` (the primary submits a form
    that does not exist yet), and `expandingAloneKeepsTheGuard` — which is the
    corrected form of the third. It used to read `expandingClearsRequired`, and
    that rule is gone: see
    test_the_connection_guard_tracks_the_answer_not_the_disclosure.
    """
    payload = _harness()
    for key in (
        # Step 1 — the picker's four states.
        "emptyState", "failedThenRetry", "categories",
        "filterHidesEmptyCategory", "noMatchCopy",
        # Step 1 — the footer, and the door to the marketplace.
        "noCreateOnStepOne", "browseClosesAndReopens",
        # Step 2 — what a template fills, and what it must never fill.
        "stepTwoFooter", "provenanceChip", "hydrated", "operatorFieldsUntouched",
        "connectionLifted", "reviewHidesWhatTheTemplateAnswered",
        "summaryNamesTheTools", "focusLandsOnName", "expandingAloneKeepsTheGuard",
        # Back, the chip's dismissal, and Blank.
        "backKeepsTheDraft", "chipClearsTemplateOnly", "blankIsThePlainForm",
        "closes",
        # The roster row's own two doors.
        "bothMenuDoorsCarryALucideIcon", "neitherMenuDoorCarriesATrailingArrow",
        "theMarketplaceDoorIsBlocksAndNotTheOfficesIcon",
        "theMarketplaceDoorStillOpensTheTakeover",
    ):
        assert payload[key] is True, key


def test_the_connection_guard_tracks_the_answer_not_the_disclosure() -> None:
    """The rule this file used to pin was the wrong rule, and it shipped a hole.

    `expandingClearsRequired` said that opening "Review & customise" hands the
    matrix back to the operator, so the lifted AI select stops being required —
    one way, and never restored. But that disclosure is also where the
    template's specialty, description and what-done live, so opening it to READ
    them — the interaction the layout invites — disarmed the guard. Pick a
    template, open the panel, close it again without touching anything, type a
    name, Create: five null `model_*` columns, no connection_id, no
    api_base_url, and "Saved successfully" over the top of it.

    The requirement changed, so this test changed with it (spec 8.3, corrected).
    The intent the old rule was reaching for is kept — an operator who sets the
    matrix by hand must not be blocked by the convenience select above it — but
    an ANSWER is what proves that, not a panel toggle:

    * the guard survives expand-and-close;
    * any one of the five per-type selects releases it;
    * clearing them all back to None re-arms it, because it is a live check in
      both directions rather than a one-way flip.

    The fan-out half — answering the lifted select itself, which writes the
    five from script — needs the real builder to bind it and the real submit
    path to read it back, so it is pinned in test_ui_context.py.
    """
    payload = _harness()
    for key in ("expandingAloneKeepsTheGuard", "answeringOneTypeReleasesTheGuard",
                "clearingThemAllRearmsTheGuard"):
        assert payload[key] is True, key
    # One rule, one owner: the module that sets `required` is the module that
    # decides when it comes off. The layout above it has no say — a listener on
    # the disclosure is exactly what the old rule was.
    lift = _read(CONTEXT / "agent-quick-connection.js")
    assert "function armGuard(form, select)" in lift
    quick = _read(CONTEXT / "agent-form-quick.js")
    assert "addEventListener('toggle'" not in quick
    assert "removeAttribute('required')" not in quick
    # ...and the guard reads the five keys the SAVE reads, never the lifted
    # select's own value: a "Set All" that stopped reaching them must leave the
    # submit refused rather than answered.
    guard = lift.split("function armGuard(form, select) {", 1)[1]
    assert "BossModAgentFields.MODEL_TYPES" in guard
    assert "matrix.some((sel) => sel.value)" in guard


def test_no_configured_connection_is_said_in_front_and_refused() -> None:
    """The state that could create an agent that fails on its first turn.

    With nothing configured the matrix renders a link to Settings and no select
    at all, so the quick layout had nothing to lift: no AI field was added, no
    `required` was set, and the sweep filed the one sentence explaining any of
    it inside the COLLAPSED disclosure. Add agent → template → name → Create
    then wrote five null `model_*` columns without a word.

    The block is native constraint validation on a required select with nothing
    to select — the mechanism the populated case already uses — rather than
    bespoke logic disabling the dialog's primary, and the message that explains
    it is joined to that control by `aria-describedby`.
    """
    payload = _harness()
    for key in ("noConnectionsIsToldInFront", "noConnectionsBlocksCreate",
                "noConnectionsStaysBlockedAfterTheChipGoes"):
        assert payload[key] is True, key
    # The shape is one module's, and it names the rule it exists for.
    lift = _read(CONTEXT / "agent-quick-connection.js")
    assert "function liftNoConnections(" in lift
    assert "#btn-goto-connections" in lift
    assert "required: true" in lift
    # Never disabled: a disabled control is exempt from validation, and the
    # submit would go through.
    assert "disabled" not in lift.split("function liftNoConnections(", 1)[1]
    # And the pair reads as ONE thing. The stand-in select's only option sat
    # directly above the matrix's notice saying almost the same sentence, so
    # the state was reported twice; the option is the placeholder now and the
    # notice is the explanation.
    assert "aiNone: '— None available —'" in lift
    assert "No connection configured" not in lift
    assert "No connections configured." in _read(CONTEXT / "agent-form-connections.js")


def test_a_failed_form_render_leaves_a_footer_that_can_recover() -> None:
    """An enabled primary that submits a form that is not there does nothing.

    `Create Agent` is `<button type="submit" form="agent-form">`, and the
    failure path clears the host — so the button was associated with no form
    and clicking it was silent. What is left now is Back, which is a real
    recovery: the picker is still mounted, and nothing stayed recorded as
    built. The copy says so instead of "Refresh the page and try again".
    """
    payload = _harness()
    for key in ("failedRenderDropsTheDeadPrimary",
                "pickingAgainAfterAFailedRenderRetries"):
        assert payload[key] is True, key
    dialog = _read(CONTEXT / "agent-edit.js")
    assert "Refresh the page" not in dialog
    assert "Go back and pick again." in dialog


def test_a_pick_that_is_still_building_cannot_save_the_one_before_it() -> None:
    """The wrong agent, created from the template the operator had just left.

    `Create Agent` is `<button type="submit" form="agent-form">` and finds its
    form BY ID. A build is four requests long, and until it lands the PREVIOUS
    pick's form is still mounted, still named `agent-form` and still carrying
    its submit listener — so clicking the primary mid-build posted the earlier
    draft, then navigated the operator to that agent and closed the dialog.

    The assertion is on what reaches `POST /api/agents`, not on what the dialog
    looks like: the dialog looked right the whole time.
    """
    payload = _harness()
    for key in ("theBuildingPickCannotSaveTheLastOne", "theWithheldPrimarySaysWhy",
                "theSavedAgentIsThePickOnScreen"):
        assert payload[key] is True, key
    # Withheld rather than torn down, and BEFORE the build it is withholding
    # for. Re-spelled, not weakened: the withhold used to be painted inline
    # here with a document-wide lookup, and it is now a named state on the one
    # owner of that button — so the same fact is pinned in two halves, the call
    # in the render and the word in the owner.
    save = _read(CONTEXT / "agent-form-save.js")
    render = save.split("async function renderInline(", 1)[1]
    assert render.index("primary.building(token)") < render.index("stageForm(")
    assert "const BUILDING = 'Loading…';" in _read(CONTEXT / "agent-dialog-footer.js")


def test_a_superseded_build_never_lands_on_the_pick_that_won() -> None:
    """Two renders over one host, and the loser arriving last.

    Back-then-pick leaves both in flight. The first used to replace the
    winner's form and chip when it finally resolved, and what the dialog
    recorded as built was written BEFORE the build — so it named the pick that
    was not on screen, re-picking it was a no-op, and the operator could only
    escape by picking a third template.

    A render now claims its host and publishes only while it still holds the
    claim, so the record and the screen cannot disagree.
    """
    payload = _harness()
    for key in ("aSupersededBuildNeverLands", "theRecordedBuildIsTheOneOnScreen",
                "theLosingPickRebuilds"):
        assert payload[key] is True, key
    dialog = _read(CONTEXT / "agent-edit.js")
    # Cleared before the build, recorded only once it has landed AND is still
    # the step on screen.
    picked = dialog.split("async function pickTemplate(template) {", 1)[1]
    assert picked.index("builtFor = undefined;") < picked.index("renderInline(")
    assert picked.index("renderInline(") < picked.index("builtFor = key;")
    assert "if (!landed || step !== 'form') return;" in picked


def test_a_failed_connections_read_is_told_and_blocks_the_save() -> None:
    """The second door to the connectionless agent, and it was silent.

    The submit path resolves the operator's answer against its own read of
    `/api/connections`. That read had no `res.ok` check and an empty catch, so
    a failure became `[]` — indistinguishable from "the operator configured
    none" — and `buildSubmitData` then wrote null for all five model types.
    The operator answered the required AI select, was told the agent was
    created, and got exactly the agent the required select exists to refuse.

    It blocks an EDIT too, and for a stronger reason: the same null-writing
    would erase the connections the agent already had.

    Not to be confused with `loadFormData`'s documented empty lists — that one
    decides what the form SHOWS, and its "no connections configured" link to
    Settings says what is missing. This one decides what is SAVED.
    """
    payload = _harness()
    assert payload["aFailedConnectionsReadIsToldAndBlocks"] is True
    assert payload["aFailedConnectionsReadSavesNothing"] is True
    save = _read(CONTEXT / "agent-form-save.js")
    assert "if (!res.ok) throw new Error" in save
    assert "catch { /* empty */ }" not in save
    # The guard is right and the reason written beside it was not: HTML's
    # implicit-submission algorithm DOES honour a disabled default button, so
    # "implicit submission is not blocked" was a false claim standing in for a
    # true one. The handler-level refusal earns its place on other grounds.
    guard = save.split("if (!connections) {", 1)[0].rsplit("await hireSubmit.run", 1)[0]
    assert "implicit submission is not" not in guard
    assert "does honour a disabled" in guard
    assert "`requestSubmit()`" in guard
    # The documented behaviour it must NOT have copied: the form still renders
    # when its dependencies cannot be read. Each read degrades ALONE now — the
    # single shared catch this used to name degraded all four at once — so the
    # log line names the read, and the list it could not fill is still handed
    # back empty rather than withheld.
    form = _read(CONTEXT / "agent-form.js")
    assert "console.error(`[agent-form] ${what} could not be read:`, err);" in form
    assert "connections: connections || []," in form


def test_the_picker_retry_places_focus_after_the_repaint() -> None:
    """`Try again` rebuilds the button that was clicked.

    Same class as the marketplace rail: a control the operator activated must
    never hand the keyboard to <body>. Both outcomes are covered — the read
    that fails again, and the one that succeeds.
    """
    assert _harness()["retryPlacesFocus"] is True


def test_remove_template_undoes_the_personality_too() -> None:
    """`applyHireFields`' documented contract, made true.

    It says that called with empty strings it clears exactly the fields a
    template can write. It wrote four and cleared three: the personality was
    only ever SET, so Remove template left the template's personality selected
    on an otherwise emptied form. The cleared shape is now derived from the
    same mapping that writes it, so a field added to one cannot be missed here.
    """
    assert _harness()["chipClearsThePersonalityToo"] is True
    quick = _read(CONTEXT / "agent-form-quick.js")
    assert "function clearedFields()" in quick
    assert "return templateFields({});" in quick
    assert "applyHireFields(formRoot, clearedFields())" in quick


def test_the_primary_is_disabled_for_as_long_as_the_save_runs() -> None:
    """Behaviour, not a source string.

    The in-flight gate stops a second submit, and the primary says so and
    refuses the click: this drives a held save and reads the button in both
    states. Nothing in the suite asserted the disabled half after the busy
    label moved behind `paintPrimary`.
    """
    payload = _harness()
    assert payload["primaryIsDisabledWhileSaving"] is True
    assert payload["primaryComesBackWhenTheSaveIsRefused"] is True


def test_a_refused_save_cannot_revive_a_build_s_withheld_primary() -> None:
    """The race, reopened from the other end.

    Back and Cancel stay live while a save runs, so: fill template A, click
    Create, press Back, pick template B. B's build correctly withholds the
    primary — and then A's save is refused, and its `finally` painted the
    button live again while A's form was still the mounted `#agent-form`. One
    click posted DRAFT-FROM-A, closed the dialog, and navigated the operator to
    an agent they had walked away from. The in-flight gate cannot help: that
    run has already finished.

    A withhold held by an in-flight render is not an unrelated save's to clear,
    which is what the shared claim now makes true rather than likely.
    """
    payload = _harness()
    assert payload["aRefusedSaveCannotReviveAWithheldPrimary"] is True
    assert payload["aRefusedSaveCannotPostTheDraftLeftBehind"] is True
    # One claim, taken by the render and INHERITED by the save it wires — not a
    # second claim of the save's own, which is how the two could disagree.
    save = _read(CONTEXT / "agent-form-save.js")
    assert "const token = primary.claim();" in save
    assert "stageForm({ agent, primary, token, onSave, onDelete })" in save
    assert "primary.ready(token, heldTheKeyboard);" in save
    # The guard the ownership model replaces: the save used to decide for
    # itself whether the button was still its to paint.
    assert "if (!isCreating || !savedAgent)" not in save


def test_a_build_cannot_repaint_a_dialog_that_is_not_its_own() -> None:
    """The primary was reached by a document-wide id lookup.

    So a create dialog dismissed mid-build relabelled whatever dialog was open
    when its build finally landed — and when that dead build's own connections
    read had also failed, it left a healthy edit dialog with a permanently
    disabled primary described by an empty hidden line, recoverable only by
    closing and reopening. The harness drives exactly that shape.

    The lookup is scoped to one dialog's root now, and refused entirely once
    that root has left the document.
    """
    assert _harness()["anOrphanedBuildLeavesTheOpenDialogAlone"] is True
    footer = _read(CONTEXT / "agent-dialog-footer.js")
    assert "if (!document.body.contains(root)) return null;" in footer
    assert "return root.querySelector(`#${ID}`);" in footer
    # Nothing else in the agent modules LOOKS that button up, by any root.
    # Naming it in a docstring is how a reader finds its owner; querying for it
    # is how it acquires a second one.
    for path in sorted(CONTEXT.glob("agent-*.js")):
        if path.name == "agent-dialog-footer.js":
            continue
        source = _read(path)
        assert "querySelector('#agent-form-submit')" not in source, path.name
        assert 'id: \'agent-form-submit\'' not in source, path.name


def test_a_build_that_fails_after_back_leaves_step_one_alone() -> None:
    """`failed()` ignored the step; `pickTemplate` does not.

    Back is live while a build runs, so a build can fail after the operator has
    left it. The failure path then rewrote step ONE: `Browse marketplace` — the
    only door out of an empty library — was replaced by a second Back that led
    to the step already on screen and was then given focus, and the error
    paragraph went into the hidden form host where nobody could read it.
    """
    payload = _harness()
    assert payload["aBuildFailingAfterBackLeavesThePickerAlone"] is True
    assert payload["pickingAgainAfterABuriedFailureRetries"] is True
    dialog = _read(CONTEXT / "agent-edit.js")
    failed = dialog.split("function failed(err) {", 1)[1]
    # Nothing is written before the step is checked, and the record is cleared
    # before it — a build that failed is never a build that landed.
    assert failed.index("builtFor = undefined;") < failed.index("step !== 'form'")
    assert failed.index("if (wasCreating && step !== 'form') return;") < failed.index("clear(formEl)")
    # And the docstring no longer claims a repair it does not always make.
    doc = dialog.split("function failed(err) {", 1)[0].rsplit("/**", 1)[1]
    assert "ONLY ON THE STEP THAT ASKED FOR IT" in doc
    assert "Where it does repair" in doc


def test_a_rebuilt_footer_does_not_un_withhold_the_primary() -> None:
    """The state model's own gap, found while giving the button one owner.

    Back and re-picking the SAME template is documented to keep the draft, so
    it rebuilds the ROW without rebuilding the form — and `setActions` makes a
    fresh button at the resting label and enabled. The state the current render
    was holding went with the button it replaced: a form that could not save
    offered a live primary whose only remaining answer was an error on click,
    with the reason no longer named as its description.

    Not one of the reported defects; it is the same class as all of them, and a
    state that evaporates when the row underneath it is rebuilt is not a state
    model. The owner remembers what it painted and puts a new button back into
    it.
    """
    assert _harness()["aRebuiltFooterKeepsTheWithhold"] is True
    footer = _read(CONTEXT / "agent-dialog-footer.js")
    assert "painted = { token, state: { label, disabled, reason } };" in footer
    # The row swap is what re-applies it, and it re-applies the SAY, not the
    # focus: a repaint that moved the keyboard would steal it from the picker.
    show = footer.split("show(step) {", 1)[1].split("},", 1)[0]
    assert "primary.repaint()" in show
    assert "refocus" not in footer.split("repaint() {", 1)[1].split("},", 1)[0]


def test_the_withheld_primary_hands_over_the_keyboard() -> None:
    """A control that disables itself under the operator's finger.

    The save disabled the button that had just been pressed and put focus
    nowhere, and a blocked render did the same — `refocus` was suppressed
    precisely because the target was disabled, which is the case that needed
    handling rather than skipping. The line that says why is a live region and
    a focus target, so it is where the keyboard goes; the button takes it back
    when it is live again.
    """
    payload = _harness()
    assert payload["theBusyPrimaryHandsTheKeyboardToTheLine"] is True
    assert payload["theSettledPrimaryTakesTheKeyboardBack"] is True
    recovery = _read(CONTEXT / "agent-recovery.js")
    for attr in ("'role', 'status'", "'aria-live', 'polite'", "'tabindex', '-1'"):
        assert f"element.setAttribute({attr})" in recovery, attr
    # One rule, in one place: withheld with a reason hands over, live takes back.
    footer = _read(CONTEXT / "agent-dialog-footer.js")
    handover = footer.split("if (refocus) {", 1)[1]
    assert "if (!disabled) button.focus();" in handover
    assert "else if (reason) reason.focus();" in handover


def test_the_summary_names_the_tools_a_template_expects() -> None:
    """`tools_hint` at the moment the agent is created, not a surface away.

    It is stored, and the marketplace shows it while browsing, but the picker
    cell and the quick layout both dropped it — so the tool expectations were
    invisible exactly where the decision is made. It is on the template row
    already; no API change follows.
    """
    assert _harness()["summaryNamesTheTools"] is True


def test_the_roster_row_opens_two_doors() -> None:
    """The row is a menu button, not the dialog's trigger.

    Click-triggered and toggling: a hover-only menu is unreachable by keyboard
    and by touch, which is why the panel is core/overlays.js's rather than a
    second popover, and why the row carries aria-haspopup from the start.
    """
    menu = _read(JS / "shell" / "add-agent-menu.js")
    # The operator's copy: the door that leaves says where it goes, and the one
    # that stays here is named for the thing it makes.
    assert "'Agent Marketplace'" in menu
    assert "'Add Agent'" in menu
    assert "BossModOverlays.createMenu({" in menu
    assert "BossModMarketplace.open()" in menu
    assert "BossModAgentEdit.openAgentModal({ store })" in menu
    assert "aria-haspopup" in menu and "aria-expanded" in menu
    # Closed before either door opens, so focus returns to the row and the
    # dialog that follows captures the row rather than the vanished panel.
    assert "onclick: () => { menu.close(); open(); }," in menu

    shell = _read(JS / "shell" / "shell.js")
    assert "addAgent.toggle();" in shell
    assert "BossModAddAgentMenu.createAddAgentMenu({" in shell
    assert "BossModAgentEdit" not in shell, "the shell reaches the dialog through the menu"


def test_the_browse_door_and_its_copy_are_gone() -> None:
    """Direct replacement: the catalog module and the URL box are deleted.

    Not hidden behind a flag and not left loadable. A pack is browsed in the
    marketplace and installed into the library; the form's only input is a row
    from that library.
    """
    assert not (CONTEXT / "agent-form-catalog.js").exists()
    index = _read(ROOT / "ui" / "templates" / "index.html")
    assert "agent-form-catalog.js" not in index
    for module in ("agent-template-picker.js", "agent-form-quick.js",
                   "agent-form-save.js", "shell/add-agent-menu.js"):
        assert module in index, module
    # Load order: the picker and the quick layout are called by the dialog.
    def at(name: str) -> int:
        return index.index(f"static_url('js/{name}')")

    assert at("context/agent-template-picker.js") < at("context/agent-edit.js")
    assert at("context/agent-form-quick.js") < at("context/agent-edit.js")
    assert at("context/agent-quick-connection.js") < at("context/agent-form-quick.js")
    assert at("context/agent-templates-api.js") < at("context/agent-template-picker.js")
    assert at("context/agent-edit.js") < at("shell/add-agent-menu.js")

    joined = "\n".join(_read(p) for p in sorted(CONTEXT.glob("agent-*.js")))
    for gone in ("Browse packs", "Start blank", "Loading packs…",
                 "pack-url-input", "btn-import-pack-url", "pack-url-import-status",
                 "applyImport", "bindUrlImport", "pack-browse"):
        assert gone not in joined, gone
    assert "pack-url-input" not in _read(CONTEXT / "agent-form-advanced.js")

    # What the hydrate module is left with is its whole job.
    hydrate = _read(CONTEXT / "agent-form-hydrate.js")
    assert "function applyHireFields(" in hydrate
    assert hydrate.rsplit("return {", 1)[-1].strip().startswith("applyHireFields }")
    # shortSha moved to the module that renders the pinned SHA on the chip.
    assert "function shortSha(" in _read(CONTEXT / "agent-form-quick.js")
    assert "shortSha" not in hydrate


def test_a_template_can_never_write_name_colour_or_a_connection() -> None:
    """The rule, at the seam that has to hold it.

    `templateFields` is the one place that maps an AgentTemplate row onto form
    field names, so a field it cannot name is a field a template cannot fill.
    """
    quick = _read(CONTEXT / "agent-form-quick.js")
    mapping = quick.split("function templateFields(template) {", 1)[1].split("}", 1)[0]
    assert "template.specialty" in mapping
    assert "template.description" in mapping
    assert "template.what_done_looks_like" in mapping
    assert "template.personality_hint" in mapping
    for forbidden in ("name", "color", "colour", "model_"):
        assert forbidden not in mapping, forbidden

    hydrate = _read(CONTEXT / "agent-form-hydrate.js")
    body = hydrate.split("function applyHireFields(", 1)[1]
    for forbidden in ('name="name"', 'name="agent-color"', 'name="model_'):
        assert forbidden not in body, forbidden


def test_the_pack_client_is_the_catalog_read_and_nothing_else() -> None:
    """`importPack` went with the form's URL box; the catalog read stayed.

    The client's import call had no caller left once the marketplace installed
    through BossModAgentTemplatesApi — a second, unreachable path to the same
    endpoint. The ROUTE is a documented API surface with its own tests and is
    untouched; what is gone is the dead client function, so this pins the one
    pack call the app actually makes rather than the one it did not.
    """
    api = _read(CONTEXT / "agent-api.js")
    assert "importPack" not in api
    assert "/api/agent-packs/import" not in api
    assert "fetchCatalog" in api
    assert "/api/agent-packs" in api


def test_catalog_pin_is_3c1e0a6() -> None:
    github = _read(ROOT / "core" / "agent_pack" / "github.py")
    assert 'DEFAULT_CATALOG_PIN = "3c1e0a6"' in github
    settings = _read(ROOT / "db" / "settings.py")
    assert '("agent_pack_catalog_pin", "3c1e0a6", "agent_packs")' in settings


def test_both_menu_doors_are_an_icon_and_a_label() -> None:
    """`Browse Marketplace →` and `+ Add Agent` marked themselves two ways.

    One hung a glyph off the end of its label, the other typed one into the
    front of it, and neither was the shell's own icon system — so the two rows
    of one menu did not line up and the arrow read as decoration nobody chose.
    Both are a lucide icon followed by the text that names them now, built the
    way header.js builds every other icon in the shell: an `<i data-lucide>`
    placeholder through `BossModDom.h`, swapped for its SVG by
    `BossModIcons.paint` once the panel is in the document.

    That call used to be `lucide.createIcons({ nodes: [menu.element] })`, and
    this test asserted it verbatim. `nodes` is not one of createIcons' options
    — it destructures `{ icons, nameAttr, attrs }` and the string does not
    appear in the vendored bundle — so the argument was dropped and the paint
    ran over the whole document, rebuilding every icon in the shell each time
    this menu opened. The assertion is now on the scoped painter, which is a
    different requirement, not a weaker one: the old line proved the menu asked
    for a paint, the new one proves it asks for a paint of the panel.

    `blocks` for the marketplace, and the ban on `building` is asserted against
    shell/places.js rather than spelled out here: the Office owns that mark, and
    the rule is that the two must differ however either of them changes.

    The literal `+` goes too. It is not the roster row's `+` — that one is an
    empty SEAT, a dashed avatar lining up with the people above it — it was a
    character in a label, and a character has the metrics of whatever font
    renders it while an icon has the icon's. Two rows that are supposed to line
    up cannot line up on one of each.
    """
    payload = _harness()
    for key in (
        "bothMenuDoorsCarryALucideIcon", "neitherMenuDoorCarriesATrailingArrow",
        "theMarketplaceDoorIsBlocksAndNotTheOfficesIcon",
        "theMarketplaceDoorStillOpensTheTakeover",
    ):
        assert payload[key] is True, key
    menu = _read(JS / "shell" / "add-agent-menu.js")
    assert "h('i', { 'data-lucide': icon, 'aria-hidden': 'true' })," in menu
    assert "BossModIcons.paint(menu.element, 'add-agent-menu');" in menu
    assert "door('blocks', 'Agent Marketplace'" in menu
    assert "door('plus', 'Add Agent'," in menu
    # The copy the rows actually render, comments stripped: the prose above
    # quotes both retired labels.
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", menu, flags=re.S)
    assert "\u2192" not in code, "the trailing arrow is gone from the label"
    assert "'+ Add Agent'" not in code
    assert "Browse Marketplace" not in code
    # The Office's mark, read from the registry that owns it.
    places = _read(JS / "shell" / "places.js")
    office = places.split("office: Object.assign(", 1)[1].split("}),", 1)[0]
    assert "icon: 'building'," in office, "places.js changed under this test"
    assert "door('building'" not in menu, "the Office already wears that mark"
    # An icon slot both rows fill, so the labels start on the same edge.
    overlays = _read(ROOT / "ui" / "static" / "css" / "overlays.css")
    choice = overlays.split(".add-agent-choice {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in choice
    assert "align-items: center;" in choice
    assert "gap: 8px;" in choice
    icon = overlays.split(".add-agent-choice svg {", 1)[1].split("}", 1)[0]
    assert "width: 16px;" in icon and "height: 16px;" in icon
    assert "flex: 0 0 auto;" in icon
    # And the accent treatment .roster-hire carries, hover correction included.
    assert "color: var(--accent);" in choice
    assert (
        ".add-agent-choice:hover { background: var(--accent-bg); color: var(--blue-ink); }"
    ) in overlays
