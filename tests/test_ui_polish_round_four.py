"""The six defects the third hands-on run turned up.

Round three's lesson still stands: a structural assertion pins a mechanism so
it cannot silently regress, and it cannot tell you the result looks right.
Each test below says which of the two it is doing, and anything that is a
claim about what a CLICK does is proven on built nodes rather than by reading
source.

Three of round three's and round two's assertions guarded properties this
round respells rather than removes. Each of those was MOVED to the new
spelling in place — an assertion deleted because the string it pinned went
away is coverage lost, not coverage updated.
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

    Anchored on a newline and a following `{`, so `.modal-panel` never picks
    up `.modal-panel[data-size="wide"]` and `.conversation-title` never picks
    up `.conversation-title-edit`.
    """
    opener = re.search(rf"(?m)^{re.escape(selector)}\s*\{{", css)
    assert opener, f"no rule for {selector}"
    return css[opener.end():].split("}", 1)[0]


def _run(harness: str, modules: list[Path]) -> dict:
    """Run a Node harness over an explicit module list and read its payload."""
    args = ["node", str(HERE / harness)] + [str(path) for path in modules]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


# ─── Task 1: the modal gets a backdrop ───


def _overlays_payload() -> dict:
    return _run("js_overlays_harness.cjs",
                [JS / "core/dom.js", JS / "core/overlay-focus.js", JS / "core/overlays.js"])


def test_a_modal_actually_blocks_the_page_behind_it() -> None:
    """There was no backdrop node anywhere — not in the JS, not in the CSS.

    So the dialog floated over a fully live page and clicks reached the
    controls behind it. A dialog that does not block the page is not modal,
    whatever `aria-modal` says.
    """
    payload = _overlays_payload()
    assert payload["backdropExists"] is True
    assert payload["backdropSitsBehindThePanel"] is True
    # Both are removed together — a leaked backdrop bricks the app.
    assert payload["closeRemovesBackdrop"] is True
    # A stray click outside a half-filled form must not discard it.
    assert payload["backdropClickDoesNotDismiss"] is True
    # Esc still works, unchanged.
    assert payload["escStillCloses"] is True


def test_the_scrim_reads_without_the_blur() -> None:
    """backdrop-filter is an enhancement; the scrim is the actual affordance."""
    css = _read(CSS / "overlays.css")
    rule = _rule(css, ".modal-backdrop")
    assert "rgba(" in rule
    assert "backdrop-filter" in rule
    assert rule.index("background") < rule.index("backdrop-filter")
    # Between the app and the panel, so it covers the page and not the dialog.
    scrim_layer = int(re.search(r"z-index:\s*(\d+)", rule).group(1))
    panel_layer = int(re.search(r"z-index:\s*(\d+)", _rule(css, ".modal-panel")).group(1))
    assert scrim_layer < panel_layer, (scrim_layer, panel_layer)
    # And nothing in a place outranks the scrim: the Files menus were at 65.
    places = _read(CSS / "places.css")
    for selector in (".file-menu", ".file-new-list"):
        layer = int(re.search(r"z-index:\s*(\d+)", _rule(places, selector)).group(1))
        assert layer < scrim_layer, (selector, layer, scrim_layer)


# ─── Task 2: the primary action is pinned ───

# The order tests/js_context_harness.cjs evaluates its modules in. A fourth
# copy of the list, for the reason the earlier three carry: every file that
# drives the column reads it, and a shared constant would hide which of them a
# breakage belongs to.
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
    JS / "context" / "agent-template-picker.js",
    JS / "context" / "agent-quick-connection.js",
    JS / "context" / "agent-form-quick.js",
    JS / "context" / "agent-dialog-footer.js",
    JS / "context" / "agent-edit.js",
    JS / "context" / "desk-panel.js",
    JS / "context" / "context-column.js",
    JS / "places" / "chat" / "chat-place.js",
]


def _agent_dialog_payload() -> dict:
    return _run("js_context_harness.cjs", CONTEXT_MODULES)


def test_the_primary_action_is_pinned_beside_cancel() -> None:
    """The operator could not find `Create Agent`.

    It sat at the bottom of a scrolling form while `Cancel` was pinned and
    obvious. Round three called that a deliberate compromise, because a submit
    button moved out of its form stops submitting it; the `form` attribute is
    the standard answer and this is it.
    """
    payload = _agent_dialog_payload()
    # The create dialog carries a footer per step now. Step one has no form,
    # so it has no primary either; step two pins Back, Cancel and the primary.
    assert payload["stepOnePinned"] == ["Browse marketplace", "Cancel"]
    assert payload["stepTwoPinned"] == ["Back", "Cancel", "Create Agent"]
    assert payload["editPinnedActions"] == ["Cancel", "Save Changes"]
    # It submits the form it is no longer inside.
    assert payload["primaryCarriesFormAttribute"] == "agent-form"
    assert payload["primaryIsSubmitType"] is True
    # Exactly one element owns the id the suite pins.
    assert payload["submitIdCount"] == 1
    # Destructive stays away from the primary: Delete is in the form body.
    assert payload["deleteIsNotPinned"] is True


def test_the_form_still_submits_from_the_pinned_button() -> None:
    """The property, not the markup: clicking it runs the form's submit path.

    And it does NOT close the dialog on its own. The form's handler owns the
    outcome — including the seed-legibility colour clamp, which refuses a save
    — so a dialog that closed on the click would discard a draft the form had
    just refused to store.
    """
    payload = _agent_dialog_payload()
    assert payload["pinnedPrimarySubmitsTheForm"] is True
    assert payload["pinnedPrimaryDoesNotCloseTheDialog"] is True


# ─── Task 3: rename gets a control pair, not a word ───

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


def test_rename_offers_confirm_and_cancel_as_icons() -> None:
    """A green check and a red cross, and a cancel that is actually visible.

    Esc cancelled and nothing said so. Both join the chrome's action row
    through the same `{id, label, icon, onSelect}` descriptor every other
    action uses, rather than as bespoke buttons beside the title.
    """
    payload = _conversation_payload()
    assert payload["renameActions"] == ["Cancel rename", "Save name"]
    assert payload["renameActionIcons"] == ["x", "check"]
    # Icon-only controls carry their own names; the tone is not the message
    # (SC 1.4.1) — the shapes differ as well as the hue.
    assert payload["renameActionsAreIconOnly"] is True
    # Cancel and Esc are the same path, so they cannot drift.
    assert payload["cancelActionRestoresLikeEsc"] is True
    # Neither appears when not editing.
    assert payload["renameActionsAbsentAtRest"] is True


def test_the_rename_tones_come_from_tokens() -> None:
    """Structural: --ok and --alert, not two hand-mixed colours.

    Both are icon-only, so the 3:1 non-text floor applies (SC 1.4.11) and both
    tokens clear it — tests/test_ui_tokens.py owns the measurement.
    """
    css = _read(CSS / "conversation.css")
    assert "var(--ok)" in _rule(css, "#conversation-title-save")
    assert "var(--alert)" in _rule(css, "#conversation-title-cancel")


# ─── Task 4: the edit state stops shouting ───


def test_the_edit_state_is_a_tint_not_a_fill() -> None:
    """"Sleek, not jarring" — the operator's words.

    Two rules were stacked: a filled `--accent-bg` ground, and base.css's 2px
    accent focus outline on top of it. The text goes soft blue and the border
    is a hairline instead.
    """
    css = _read(CSS / "conversation.css")
    editing = _rule(css, '.conversation-title-edit[data-editing="true"]')
    assert "background" not in editing, "the ground does not change"
    assert "color: var(--accent)" in editing
    assert "dotted" in editing


def test_focus_stays_visible_after_the_outline_is_replaced() -> None:
    """Replacing a focus indicator is fine; removing one is not (SC 2.4.7)."""
    css = _read(CSS / "conversation.css")
    override = _rule(css, ".conversation-title-edit:focus-visible")
    assert "outline: none" in override
    assert "dotted" in override or "border-color" in override
    # The border is RESERVED at rest, so showing it moves no text.
    assert "dotted transparent" in _rule(css, ".conversation-title-edit")
    # And only this control is exempted: base.css's global rule is intact.
    #
    # The plan asked for `"outline: none" not in base.css`, which was already
    # false — base.css has suppressed the ring on programmatically focused
    # place headings since spec 3.2, and this round may not touch base.css.
    # The property that assertion was reaching for is that the GLOBAL rule
    # still paints a ring and that this round added no second suppression.
    base = _read(CSS / "base.css")
    assert "outline: 2px solid var(--accent)" in _rule(base, ":focus-visible")
    assert base.count("outline: none") == 1
    assert 'h1[tabindex="-1"]:focus' in base


# ─── Task 5: the idle middle slot is empty ───

# The order tests/js_roster_harness.cjs evaluates its modules in.
ROSTER_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "agent-status.js",
    JS / "shell" / "roster-row-meta.js",
    JS / "shell" / "roster-people.js",
    JS / "shell" / "thread-create.js",
    JS / "shell" / "roster-threads.js",
    JS / "shell" / "roster.js",
]


def _roster_payload() -> dict:
    return _run("js_roster_harness.cjs", ROSTER_MODULES)


def test_the_middle_slot_is_empty_until_something_is_selected() -> None:
    """The invitation truncated to `Select teammat...` at rail width.

    That reads as broken, and it explained a mode nobody was in. Round two
    moved it off a standalone row on the grounds that it "never costs a row";
    it still cost a row's worth of attention. Round two's and round three's
    assertions about it are re-pointed at the count, which is the thing the
    slot now carries.
    """
    payload = _roster_payload()
    assert payload["idleMiddleSlot"] == ""
    assert payload["selectingMiddleSlotAtZero"] == "0 selected"
    assert payload["selectingMiddleSlotAtOne"] == "1 selected"
    # The invitation copy is gone from the tree, not merely hidden.
    assert "Select teammates" not in _read(JS / "shell/thread-create.js")


# ─── Task 6: decorative avatars stop showing a text cursor ───


def test_a_decorative_avatar_is_an_icon_not_selectable_text() -> None:
    """Nothing set `cursor: text`; the browser's default over text content IS
    an I-beam, and a decorative avatar is a <span> holding a glyph.

    People rows escaped it only because their avatars are <button>s inheriting
    base.css's pointer, which is why this is scoped to the non-button form.
    """
    css = _read(CSS / "controls.css")
    rule = _rule(css, "span.avatar")
    assert "cursor: inherit" in rule
    assert "user-select: none" in rule
    # The interactive form is untouched and still reads as clickable.
    assert "cursor: inherit" not in _rule(css, "button.avatar")
    assert "cursor: pointer" in _rule(css, "button.avatar")
