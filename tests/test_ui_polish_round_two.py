"""The eight defects the operator found on the first hands-on run.

Six of the eight were found BY EYE and no test caught them, which is the
measure of what a structural assertion is worth here: it can pin a mechanism so
it cannot silently regress, and it cannot tell you the result looks right. Each
test below says which of the two it is doing.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
CONVERSATION = JS / "conversation"
HERE = Path(__file__).resolve().parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """The declaration block of exactly one rule.

    Anchored on a newline and a following `{`, so `.transcript` never picks up
    `.transcript-wrap` and `.event-card` never picks up `.event-card.tone-ok`.
    """
    opener = re.search(rf"(?m)^{re.escape(selector)}\s*\{{", css)
    assert opener, f"no rule for {selector}"
    return css[opener.end():].split("}", 1)[0]


def _bg(rule: str) -> str:
    """The token a rule paints itself with, or '' when it paints nothing."""
    found = re.search(r"background(?:-color)?:\s*([^;]+);", rule)
    return found.group(1).strip() if found else ""


def _run(harness: str, modules: list[Path]) -> dict:
    """Run a Node harness over an explicit module list and read its payload."""
    args = ["node", str(HERE / harness)] + [str(path) for path in modules]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


# The order tests/js_context_harness.cjs evaluates its modules in. A second copy
# of tests/test_ui_context.py's list, on the same reasoning the roster harness's
# duplicate carries: both files drive the same column, and a shared constant
# would hide which of them a breakage belongs to.
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
    JS / "needs" / "need-shape.js",
    JS / "needs" / "needs-store.js",
    JS / "needs" / "needs-bar.js",
    CONVERSATION / "sources" / "thread-archive.js",
    CONVERSATION / "sources" / "thread-source.js",
    CONVERSATION / "sources" / "agent-source.js",
    CONVERSATION / "conversation.js",
    JS / "shell" / "places.js",
    JS / "places" / "files" / "file-content.js",
    JS / "places" / "files" / "file-form.js",
    JS / "places" / "files" / "file-ops.js",
    JS / "places" / "files" / "file-viewer.js",
    JS / "context" / "mini-office.js",
    JS / "context" / "desk-opener.js",
    JS / "context" / "desk-files.js",
    JS / "context" / "desk-notes.js",
    JS / "context" / "desk-tasks.js",
    JS / "context" / "desk-actions.js",
    JS / "context" / "agent-api.js",
    JS / "context" / "agent-templates-api.js",
    JS / "context" / "agent-fields.js",
    JS / "context" / "agent-form-fields.js",
    JS / "context" / "agent-form-advanced.js",
    JS / "context" / "agent-form-connections.js",
    JS / "context" / "agent-form-bindings.js",
    JS / "context" / "agent-form-hydrate.js",
    JS / "context" / "agent-form.js",
    JS / "context" / "agent-submit.js",
    JS / "context" / "agent-recovery.js",
    JS / "context" / "agent-form-save.js",
    JS / "marketplace" / "marketplace-items.js",
    JS / "marketplace" / "pack-card.js",
    JS / "marketplace" / "filter-rail.js",
    JS / "context" / "agent-template-picker.js",
    JS / "context" / "agent-form-template.js",
    JS / "context" / "agent-dialog-footer.js",
    JS / "context" / "agent-edit.js",
    JS / "context" / "desk-panel.js",
    JS / "context" / "context-column.js",
    JS / "places" / "chat" / "chat-place.js",
]


def _context_payload() -> dict:
    return _run("js_context_harness.cjs", CONTEXT_MODULES)


# The order tests/js_roster_harness.cjs evaluates its modules in.
ROSTER_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    JS / "shell" / "roster-row-meta.js",
    JS / "shell" / "roster-people.js",
    JS / "shell" / "thread-create.js",
    JS / "shell" / "thread-view-menu.js",
    JS / "shell" / "roster-threads.js",
    JS / "shell" / "roster.js",
]


def _roster_payload() -> dict:
    return _run("js_roster_harness.cjs", ROSTER_MODULES)


def _app_js() -> list[Path]:
    return [p for p in sorted(JS.rglob("*.js")) if "vendor" not in p.parts]


# The order tests/js_conversation_harness.cjs evaluates its modules in.
CONVERSATION_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "markdown.js",
    JS / "core" / "avatar.js",
    JS / "core" / "switch.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
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
    JS / "needs" / "needs-bar.js",
    CONVERSATION / "sources" / "thread-archive.js",
    CONVERSATION / "sources" / "thread-source.js",
    CONVERSATION / "sources" / "agent-source.js",
    CONVERSATION / "conversation.js",
]


def _conversation_payload() -> dict:
    return _run("js_conversation_harness.cjs", CONVERSATION_MODULES)


# ─── Task 1: the chat column is white again ───


def test_an_agent_bubble_is_visible_against_what_it_sits_on() -> None:
    """The defect was two identical greys, not a wrong grey.

    Asserted as a relationship rather than a literal: any future change that
    makes the transcript and the agent bubble the same token fails here, which
    is the only thing that actually broke.
    """
    css = _read(CSS / "conversation.css")
    transcript = _rule(css, ".transcript")
    bubble = _rule(css, ".msg-agent")
    assert "background: var(--panel)" in transcript
    assert "background: var(--bg)" in bubble
    assert _bg(transcript) != _bg(bubble), "the bubble must not match its ground"


def test_the_whole_centre_column_is_the_panel() -> None:
    """The chrome and the composer were already --panel and the transcript was
    not, so the column had a seam across it wherever the transcript did not
    reach — the empty state, and the gap under a short conversation."""
    assert "background: var(--panel)" in _rule(_read(CSS / "conversation.css"), ".conversation")


def test_no_surface_in_the_column_relied_on_sitting_on_grey() -> None:
    """The same bug mirrored: a white card on a column that is now white.

    Three surfaces in the transcript drew themselves as --panel over --bg and
    let the FILL carry the separation. On a --panel column the fill says
    nothing, so each one needs a boundary that does — measured, not guessed:
    --line-strong is 1.50:1 on white and --line-control is 3.10:1 (SC 1.4.11).
    """
    css = _read(CSS / "conversation.css")

    # The neutral event card. Its three tone variants override the background
    # and still read; only the untinted one disappeared.
    card = _rule(css, ".event-card")
    assert "background: var(--panel)" in card
    assert "border: 1px solid var(--line-control)" in card, card

    # The loading/empty/error card inside the transcript: a content block, so
    # it takes the same ground an agent bubble does rather than a border.
    status = _rule(css, ".transcript-status")
    assert "background: var(--bg)" in status, status

    # ...which moves the skeleton bars onto --bg, where --line is 1.06:1.
    assert "background: var(--line-strong)" in _rule(css, ".transcript-skeleton")


# ─── Task 2: the avatar initial sits centred ───


def test_the_avatar_initial_is_optically_centred() -> None:
    """Cap-height centring, not em-box centring.

    Flex centres the LINE BOX; the glyph sits on the baseline inside it, and
    the baseline's position depends on the font's ascent and descent rather
    than on the box. Measured on the font this stack actually resolves to
    (Noto Sans: ascent 1.069em, descent 0.293em, cap-height 0.714em), the
    baseline lands 0.888em down a 1em line box, which leaves 0.174em above the
    capital and 0.112em below it — the glyph reads LOW by 0.031em.

    This test pins the mechanism so the correction cannot be mistaken for a
    stray value and removed. It cannot say the result looks right; only the
    operator can.
    """
    css = _read(CSS / "controls.css")
    rule = _rule(css, ".avatar")

    # The mechanism the correction exists to correct.
    assert "align-items: center" in rule
    assert "line-height: 1" in rule

    # The correction, in em — one value across chip/sm/md/lg, not a pixel
    # tuned against whichever size happened to be on screen.
    lift = re.search(r"padding-bottom:\s*([0-9.]+)em;", rule)
    assert lift, f"the correction must be an em lift: {rule}"
    assert 0 < float(lift.group(1)) < 0.2, lift.group(1)

    # ...and it says why it is there.
    assert "OPTICAL" in css

    # The em only scales if the four size rules change font-size and nothing
    # else. A size that set its own padding would opt out of the correction.
    for size, px in (("chip", 8), ("sm", 10), ("md", 11), ("lg", 15)):
        size_rule = _rule(css, f".avatar-{size}")
        assert f"font-size: {px}px" in size_rule, size_rule
        assert "padding" not in size_rule, size_rule


# ─── Task 3: the office map draws the whole floor ───


def test_the_mini_map_draws_rooms_nobody_is_standing_in() -> None:
    """The room set is the FLOOR PLAN; only the occupancy is the roster.

    Grouping by `agent.location` alone conflated the two, so with every agent
    in one room exactly one box drew and the panel read as broken.
    """
    js = _read(JS / "context/mini-office.js")
    assert "'/api/map'" in js
    assert "mapData.rooms" in js
    # Occupancy still comes from the roster, not from the map.
    assert "agent.location" in js
    # An empty room is a real room, and says so rather than rendering blank.
    assert "mini-office-room-empty" in js


def test_an_unreachable_map_does_not_blank_the_panel() -> None:
    """A failed /api/map is reported, never swallowed into an empty floor.

    The roster still answers "who is around", so the panel degrades to the
    occupied-rooms view it had before and says the floor plan is unavailable.
    """
    js = _read(JS / "context/mini-office.js")
    assert "console.error" in js
    assert "could not load the floor plan" in js.lower()
    # The degraded path is the grouping that was there before, not a blank.
    assert "function byRoom(" in js


def test_the_whole_floor_is_drawn_on_the_built_panel() -> None:
    """Source can say a fetch exists; only the built panel says what drew.

    Five rooms come back from the map and two of them hold people, so an
    occupancy-derived panel would draw two boxes and this would catch it.
    """
    payload = _context_payload()
    assert payload["drawsEveryMappedRoom"] is True
    assert payload["emptyRoomsSaySo"] is True
    # The off-map agent still gets a seat, in a bucket rendered last.
    assert payload["rendersUnknownRoom"] is True
    # ...and a map that will not load degrades to what the roster knows.
    assert payload["mapFailureDegradesRatherThanBlanks"] is True


# ─── Task 4: the Threads section ───


def test_thread_rows_carry_the_group_avatar() -> None:
    """The rows had no avatar at all, so they did not line up with People.

    The `⋯` glyph already existed — the chat header renders it for a thread —
    but as a span the header hand-built. A second hand-rolled copy in the rail
    is exactly the duplication the primitives exist to prevent, so the glyph
    moves into core/avatar.js and both surfaces ask for it.
    """
    js = _read(JS / "shell/roster-threads.js")
    assert "group: true" in js
    # The same size a person row's avatar takes, or the two lists misalign.
    assert "size: 'md'" in js

    definers = sorted(
        path.relative_to(JS).as_posix()
        for path in _app_js()
        if "avatar-group" in _read(path)
    )
    assert definers == ["core/avatar.js"], definers

    payload = _roster_payload()
    assert payload["threadRowsCarryTheGroupAvatar"] is True


def test_new_thread_is_an_icon_button_on_the_section_header() -> None:
    """Right-aligned on the THREADS title row: always visible, costing no
    vertical space, and never scrolling away as the list grows."""
    js = _read(JS / "shell/thread-create.js")
    assert "'aria-label': 'New thread'" in js, "icon-only needs its own name"
    assert "roster-section-action" in js
    assert "'data-lucide': 'plus'" in js
    # It reports whether the mode it opens is open rather than going dead in it.
    assert "'aria-expanded'" in js
    # The centred borderless text button, and the row it sat in, are gone.
    assert "roster-thread-actions" not in js
    assert "roster-thread-actions" not in _read(CSS / "shell.css")
    # The header row it sits on belongs to the section, so it is built by the
    # half that owns the section.
    threads = _read(JS / "shell/roster-threads.js")
    assert "roster-section-head" in threads
    assert "create.action" in threads
    assert ".roster-section-head {" in _read(CSS / "shell.css")


def test_the_select_hint_never_costs_the_rail_a_row() -> None:
    """A sentence of instructions, moved off the permanent line it stood on.

    Round three moved it once more, into the section header's middle slot.
    Round four DELETED it: it truncated to about fourteen characters at rail
    width, which reads as broken, and it explained a mode nobody was in.

    The property this has always guarded survives all three moves — whatever
    the mode has to say never occupies a line of its own — so it is asserted
    that way rather than on which words are in the slot. What the slot says is
    now the count alone, and that is what these assertions follow.
    """
    js = _read(JS / "shell/thread-create.js")
    assert "${count} selected" in js
    # It lives on the header row, which the Threads half builds.
    assert "roster-thread-hint" not in js
    assert "roster-thread-hint" not in _read(JS / "shell/roster-threads.js")
    assert "roster-section-hint" in js
    head = _read(JS / "shell/roster-threads.js").split(
        "class: 'roster-section-head'", 1)[1].split("threadFilters", 1)[0]
    assert "create.middle" in head, head
    payload = _roster_payload()
    assert payload["hasStandaloneCreateRow"] is False
    assert payload["idleMiddleSlot"] == ""
    assert payload["selectingMiddleSlotAtOne"] == "1 selected"


# ─── Task 5: select a person by clicking their row ───


def test_the_whole_row_selects_in_select_mode() -> None:
    """Proven on BUILT NODES, because the property is about what a click does.

    The 16px checkbox was the only target and the name opened the conversation
    instead, which is the opposite of what the mode is for. Both of the row's
    existing controls are re-bound while the mode is on.
    """
    payload = _roster_payload()
    assert payload["rowClickSelectsInSelectMode"] is True
    # ...and still opens the conversation when not selecting.
    assert payload["rowClickOpensConversationNormally"] is True
    # The desk affordance does not fire while selecting.
    assert payload["avatarDoesNotOpenDeskWhileSelecting"] is True
    # The checkbox survives as the state indicator.
    assert payload["checkboxStillRendersInSelectMode"] is True


def test_no_button_is_nested_inside_another() -> None:
    """A nested interactive control is invalid HTML and unreachable by keyboard.

    Wrapping the row in a button would have been the short way to make the row
    the target, and it would have put the avatar and name buttons inside it.
    """
    assert _roster_payload()["noNestedButtons"] is True


def test_the_row_reads_as_selectable_while_selecting() -> None:
    css = _read(CSS / "shell.css")
    assert "cursor: pointer" in _rule(css, '.roster-row[data-selecting="true"]')
    selected = _rule(css, '.roster-row[data-selected="true"]')
    assert "background: var(--accent-bg)" in selected
    # --hint measures 4.19:1 on --accent-bg, which fails AA; --muted is 5.67:1.
    assert '.roster-row[data-selected="true"] .roster-status { color: var(--muted); }' in css

    js = _read(JS / "shell/roster-people.js")
    assert "'data-selecting'" in js
    assert "'data-selected'" in js


# ─── Task 6: the chat header overflow menu ───


def test_view_options_live_behind_one_menu_not_in_the_header() -> None:
    """`Desk` is an action on the person; the receipts switch is a preference
    about the view. Different kinds of thing, so not the same level.

    The menu is where later view options go, which is what makes it a place to
    put things rather than a place to hide one thing — and a later round put
    something else there: an action the SOURCE marks `slot: 'menu'`, which is how
    Archive got off the header row. So the `⋯` is the overflow rather than a
    preferences drawer, and it says `More actions`. What this has always
    guarded is that there is one panel and one focus trap behind it, and both
    are still true.
    """
    chrome = _read(CONVERSATION / "chrome.js")
    assert "const MENU_LABEL = 'More actions';" in chrome
    assert "'aria-label': MENU_LABEL" in chrome
    # Named twice from one constant: the label a screen reader hears and the
    # bubble a pointer gets cannot drift apart.
    assert "'data-tooltip': MENU_LABEL" in chrome
    assert "aria-haspopup" in chrome
    # It reuses the one overlay implementation.
    assert "BossModOverlays." in chrome

    # And there is still exactly ONE focus trap in the tree — the whole reason
    # the menu is an overlays.js function rather than a popover of its own.
    # Round five moved that one definition into core/overlay-focus.js so the
    # builders had room to hold a bug fix; the count is what this asserts and
    # the count has not moved.
    definers = sorted(
        path.relative_to(JS).as_posix()
        for path in _app_js()
        if "function trapKeydown(" in _read(path)
    )
    assert definers == ["core/overlay-focus.js"], definers
    # ...and the menu still reaches for it rather than growing its own.
    assert "trapKeydown(event, element, close)" in _read(JS / "core/overlays.js")
    assert "createMenu" in _read(JS / "core/overlays.js")


def test_the_receipts_preference_still_works_from_its_new_home() -> None:
    """Only where the control lives changed.

    Proven on built nodes: the switch is not in the action row, it IS in the
    menu the `⋯` opens, toggling it writes the same storage key, and the `⋯`
    gets focus back when the menu closes.
    """
    payload = _conversation_payload()
    assert payload["receiptsToggleReachableFromMenu"] is True
    assert payload["receiptsPreferencePersists"] is True
    # The switch is gone from the header row itself.
    assert payload["headerHasNoReceiptsSwitch"] is True
    assert payload["menuReturnsFocusToItsButton"] is True
    # The preference control survives being opened and closed, rather than
    # being rebuilt each time and losing what it holds.
    assert payload["receiptsNodeSurvivesReopen"] is True


def test_the_overflow_menu_is_keyboard_operable() -> None:
    """Esc closes it, Tab stays inside, and focus goes back to the `⋯`.

    Proven at the overlay level rather than through the chrome, because that is
    where the behaviour lives: the menu is core/overlays.js's third shape, and
    it shares one focus trap with the modal and the slide-over. The payload is
    tests/test_ui_overlays.py's, which owns the contract; this reads it under
    the name Task 6 gave it.
    """
    harness = HERE / "js_overlays_harness.cjs"
    args = ["node", str(harness), str(JS / "core/dom.js"), str(JS / "core/overlay-focus.js"),
            str(JS / "core/overlays.js")]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["menuEscCloses"] is True
    assert payload["menuRestoresFocusToTheAnchor"] is True
    assert payload["menuTrapsTab"] is True
    assert payload["menuFocusesFirstOption"] is True
    # A panel with nothing to hand focus back to is a keyboard dead end.
    assert payload["menuNeedsAnAnchor"] is True


# ─── Task 7: the desk panel gets a hierarchy ───


def test_the_desk_panel_has_one_identity_block_and_labelled_sections() -> None:
    """"Looks like it was slapped together" is a hierarchy problem.

    Eight sections at one visual level with no grouping. The identity block is
    closed by a rule, each section carries its own labelled header with its
    action right-aligned on it, and the vocabulary is the panel's — the section
    modules render content and stop authoring headers of their own.
    """
    js = _read(JS / "context/desk-panel.js")
    assert "desk-profile" in js
    css = _read(CSS / "context.css")
    assert "border-bottom" in _rule(css, ".desk-profile")

    # Section actions belong to their section's header, not to the gap below it.
    assert "desk-section-action" in js
    assert "desk-section-head" in js
    assert "function section(" in js
    # ...and there is one owner of the vocabulary, so the panel cannot grow a
    # second header style one module at a time.
    for owned in ("context/desk-tasks.js", "context/desk-notes.js"):
        assert "desk-section-title" not in _read(JS / owned), owned


def test_the_done_contract_is_a_disclosure_not_a_standing_alert() -> None:
    """Reference material the operator reads once, not a standing alert.

    At full amber volume above the task list it outranked the task it
    describes. It is not deleted — it is one click away, and the summary says
    what is behind it.
    """
    js = _read(JS / "context/desk-panel.js")
    assert "h('details'" in js
    assert "h('summary'" in js
    assert "desk-contract" in js
    # Closed until asked for: `open` is never set on it.
    details = js.split("h('details'", 1)[1].split(")", 1)[0]
    assert "open" not in details, details
    # The copy itself is untouched, and still inside the disclosure.
    assert "What done looks like for this agent:" in js
    assert "desk-bar" in js


def test_the_folder_buttons_are_quiet_and_the_footer_is_pinned() -> None:
    """Three equal bordered buttons competed with the content they act on.

    They stay in the Files section, where their path scope lives, and stop
    shouting. The footer's own four actions are quiet text links on a block
    pinned to the bottom, which is the concept's `.desk-foot`.
    """
    files = _read(JS / "context/desk-files.js")
    controls = files.split("function controlsRow(", 1)[1].split("\n        function ", 1)[0]
    assert "desk-files-btn" not in controls, controls
    assert "btn-link desk-files-link" in controls
    # The duplicate header is gone: the panel labels the section, the browser
    # names the folder it is showing.
    assert "desk-section-title" not in files
    assert "desk-files-path" in files

    css = _read(CSS / "context.css")
    assert "border" not in _rule(css, ".desk-files-link")
    assert "btn-link desk-action" in _read(JS / "context/desk-actions.js")
    assert "margin-top: auto" in _rule(css, ".desk-footer")


def test_an_empty_section_still_reads_as_a_section() -> None:
    """A dashed placeholder, so an empty Files section is a section and not a
    gap the operator reads as a rendering failure."""
    assert "dashed" in _rule(_read(CSS / "context.css"), ".desk-empty")
    for owner in ("context/desk-files.js", "context/desk-notes.js", "context/desk-tasks.js"):
        assert "desk-empty" in _read(JS / owner), owner


def test_the_desk_panel_lost_no_content() -> None:
    """Every field on screen before this task is still on screen after it.

    Read off the BUILT panel, because "nothing was removed" is a claim about
    what renders. The reorganisation moves eight blocks; a source-reading check
    would pass while one of them sat in a branch that never runs.
    """
    payload = _context_payload()
    for field in ("name", "role", "about", "status", "contract",
                  "tasks", "files", "desk", "notes"):
        assert payload["deskFields"][field] is True, field
