"""The conversation surface: one renderer, two sources, and the adapter boundary."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
CONVERSATION = JS / "conversation"
SOURCES = CONVERSATION / "sources"
TRANSCRIPT_HARNESS = Path(__file__).resolve().parent / "js_transcript_harness.cjs"
CONVERSATION_HARNESS = Path(__file__).resolve().parent / "js_conversation_harness.cjs"

# The order the conversation harness evaluates its modules in.
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
    # The chrome's overflow menu is an overlays.js panel, not a second popover.
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
    SOURCES / "thread-archive.js",
    SOURCES / "thread-source.js",
    SOURCES / "agent-source.js",
    CONVERSATION / "conversation.js",
]


def _code(source: str) -> str:
    """The source with its comments stripped.

    A "this control no longer exists" assertion has to read what the module
    DOES. A module that explains in prose which control it dropped, and why,
    would otherwise be read as still carrying it. Same helper, same reason, as
    tests/test_ui_visual_parity.py's.
    """
    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_blocks, flags=re.M)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _app_js() -> list[Path]:
    return [p for p in sorted(JS.rglob("*.js")) if "vendor" not in p.parts]


def test_one_renderer_two_sources() -> None:
    """Exactly two adapters, one per backend conversation kind.

    thread-archive.js also lives under sources/ but declares no `kind`: it is
    the archive dialog the thread adapter delegates to, not a third source.
    """
    adapters = {
        path.name: _read(path)
        for path in sorted(SOURCES.glob("*.js"))
        if "kind: '" in _read(path)
    }
    assert sorted(adapters) == ["agent-source.js", "thread-source.js"]
    assert "kind: 'agent'" in adapters["agent-source.js"]
    assert "kind: 'thread'" in adapters["thread-source.js"]

    # conversation.js is the only file that constructs them.
    for factory in ("BossModAgentSource.createAgentSource(",
                    "BossModThreadSource.createThreadSource("):
        callers = sorted(
            path.relative_to(JS).as_posix()
            for path in _app_js()
            if factory in _read(path)
        )
        assert callers == ["conversation/conversation.js"], f"{factory} -> {callers}"


def test_sources_never_touch_the_dom() -> None:
    """The adapter boundary, asserted rather than trusted.

    The three renderers this phase replaced each knew their own wire format,
    which is why they drifted. A source normalises payloads and nothing else.
    """
    for name in ("agent-source.js", "thread-source.js"):
        source = _read(SOURCES / name)
        for forbidden in ("document.", "innerHTML", "appendChild", "BossModDom"):
            assert forbidden not in source, f"{name} must not touch the DOM ({forbidden})"


def test_sources_map_queue_visibility_as_a_live_note() -> None:
    """Busy — N queued is always visible and replaceable, not a hidden receipt."""
    agent = _read(SOURCES / "agent-source.js")
    thread = _read(SOURCES / "thread-source.js")
    for source in (agent, thread):
        assert "notification_kind === 'queue_visibility'" in source
        assert "queue-visibility:" in source
        assert "live: isQueue" in source
        assert "cleared: isQueue && !String(text).trim()" in source
    assert "systemReceipt: isSystem && !isWalkReceipt && !consent && !isQueue" in agent
    transcript = _read(CONVERSATION / "transcript.js")
    assert "message.live" in transcript
    assert "message.cleared" in transcript
    assert "removeByKey" in transcript


def _transcript_payload() -> dict:
    result = subprocess.run(
        [
            "node",
            str(TRANSCRIPT_HARNESS),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "avatar.js"),
            str(JS / "core" / "gates.js"),
            str(JS / "core" / "format.js"),
            str(CONVERSATION / "empty-state.js"),
            str(CONVERSATION / "transcript.js"),
            str(CONVERSATION / "transcript-cache.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_transcript_harness() -> None:
    assert _transcript_payload() == {
        "ok": True,
        "dedupes": True,
        "keylessAlwaysAppends": True,
        "sticksWhenNearBottom": True,
        "keepsScrollWhenReading": True,
        "announcesNewCount": True,
        "presenceScoped": True,
        "presenceShowsDuration": True,
        "cacheCopies": True,
        "liveQueueLine": True,
    }


def test_presence_shows_duration_for_a_long_running_turn() -> None:
    """The turn duration lives in the presence row, and nowhere else.

    Spec 12, carried items: `progress` had a renderer and no producer, so the
    operator's answer was to feed `act.created_at` through the world snapshot
    and paint it where they are already looking. Two halves, both asserted
    here — the presence row switches copy past the threshold, and the card kind
    it replaces is gone from event-cards.js so nothing can render the same
    information a second way.
    """
    assert _transcript_payload()["presenceShowsDuration"] is True

    transcript = _read(CONVERSATION / "transcript.js")
    # The threshold is a named constant, not a magic number buried in a branch.
    assert "const LONG_TURN_MS" in transcript
    assert "is working · ${elapsed}" in transcript
    # Below the threshold the copy is untouched — test_ui_chat_typing.py owns
    # that property, and this is the line that keeps it reachable.
    assert "is thinking..." in transcript
    # The transcript asks for the start time; it never reads the roster itself.
    assert "deps.activitySince is required" in transcript
    assert "s.roster" not in transcript

    # The controller is what owns the store, and what keeps the number moving:
    # a duration painted once and never repainted is worse than no duration.
    controller = _read(CONVERSATION / "conversation.js")
    assert "function activitySince(agentId)" in controller
    assert "currentActivitySince" in controller
    assert "(s) => s.roster," in controller

    cards = _read(CONVERSATION / "event-cards.js")
    assert "message.kind === 'progress'" not in cards
    assert "event-progress" not in cards
    assert "event-progress" not in _read(CSS / "conversation.css")


def test_conversation_harness() -> None:
    result = subprocess.run(
        ["node", str(CONVERSATION_HARNESS)] + [str(path) for path in CONVERSATION_MODULES],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "staleLoadDropped": True,
        "cacheSkipsLoading": True,
        "composerSurvivesSwitch": True,
        "presenceSurvivesSwitch": True,
        "unsubscribesPreviousSource": True,
        "errorStateRetries": True,
        # The visual-parity pass: the chrome names who you are talking to, its
        # actions carry glyphs, and an empty conversation offers the two things
        # you can do in it.
        "chromeShowsIdentityAvatar": True,
        "chromeAvatarNodeIsStable": True,
        "chromeActionCarriesItsIcon": True,
        "chromeGroupGlyphForThreads": True,
        # Archive is rare and reads as irreversible, so it moved off the header
        # row and behind the `⋯` — where the source puts it with `slot: 'menu'`
        # rather than by the view learning what a thread is.
        "archiveLivesInTheMenu": True,
        # And the subtitle went to the end of the row with the actions, which
        # is what freed the space beside the name for the rename's own pair.
        "subtitleIsWithTheActions": True,
        "emptyConversationOffersActions": True,
        "greetingWentThroughTheComposer": True,
        # The polish round moved the receipts preference out of the action row
        # and behind the header's `⋯`, where later view options go. Its storage
        # key, its control, and the node that holds it are unchanged — which is
        # what these four say, and why "moved" cannot become "dropped".
        # tests/test_ui_polish_round_two.py reads the same four under the names
        # the plan gave them.
        "headerHasNoReceiptsSwitch": True,
        "receiptsToggleReachableFromMenu": True,
        "receiptsPreferencePersists": True,
        "menuReturnsFocusToItsButton": True,
        "receiptsNodeSurvivesReopen": True,
        # Round three made a thread's title renameable in place. It is one
        # control in two states, reachable by Tab and opened by Enter; the
        # rename is not optimistic, so a failure keeps the operator's text and
        # says what went wrong rather than reverting as if nothing happened.
        # Only threads: an agent conversation and a sealed room both get a
        # plain heading with no control in it.
        "titleOpensEditOnEnter": True,
        "saveActionAppearsBesideArchive": True,
        "escapeCancelsRenameWithoutSaving": True,
        "renamePatchesTheChannel": True,
        "renameShowsTheSavedName": True,
        "failedRenameReportsError": True,
        "failedRenameKeepsDraft": True,
        "failedRenameStaysInEditMode": True,
        "renameDoesNotFollowASwitch": True,
        "agentTitleIsNotEditable": True,
        "archivedThreadIsNotRenameable": True,
        # Round four turned the word `Save` into a green check and gave it the
        # red cross the mode never had — Esc cancelled and nothing said so.
        # Both are icon-only, so each carries its own accessible name and the
        # shapes differ as well as the hues; and Cancel calls the same function
        # Esc does, so the keystroke and the control cannot drift apart.
        # tests/test_ui_polish_round_four.py reads the same five.
        "renameActions": ["Cancel rename", "Save name"],
        "renameActionIcons": ["x", "check"],
        "renameActionsAreIconOnly": True,
        "renameActionsAbsentAtRest": True,
        "cancelActionRestoresLikeEsc": True,
    }


def test_event_cards_render_desk_action_only_when_injected() -> None:
    """A control that renders but does nothing is worse than one that is absent."""
    source = _read(CONVERSATION / "event-cards.js")
    assert "ctx.openDesk" in source
    assert "data-desk-path" in source
    assert "typeof ctx.openDesk === 'function'" in source
    assert "'Open in Desk'" in source
    # The path is recorded whether or not anything can act on it, so nothing
    # is lost between the phase that reads it and the phase that opens it.
    note = source.split("if (message.kind === 'note') {", 1)[1]
    assert note.index("'data-desk-path': deskPath") < note.index(
        "typeof ctx.openDesk === 'function'"
    )
    # Phase 2A did not inject it; Phase 2B does, with the context column. The
    # assertion is inverted rather than deleted: it was correct for 2A and is
    # now correct in the opposite direction, and the property it guards — the
    # affordance and its capability ship together — is the same either way.
    place = _read(JS / "places" / "chat" / "chat-place.js")
    assert "openDesk:" in place, "Phase 2B injects openDesk from the Chat place"
    assert "BossModContextColumn.openDeskFrom(" in place


def test_there_is_one_assign_form_and_the_composer_is_not_a_door_to_it() -> None:
    """Spec 4.4's clipboard button, retired by the round that quietened the
    composer.

    Phase 2 deliberately did NOT build an assign form, because deduplicating a
    second one against the Board's in Phase 3 would have been exactly the
    transitional scaffolding this refactor exists to avoid. THAT is the property
    that has always mattered here — one form, reached by whoever needs it — and
    it is the one that survives the button.

    The clipboard itself was a third front door beside the Board's `+ New task`
    and the empty conversation's `Assign a task`, and it was the only one parked
    in front of the operator for every second they were typing a message. So the
    composer is the field and Send now, and it no longer knows a form exists —
    which is a stronger version of the boundary the button was written to keep.
    """
    composer = _read(CONVERSATION / "composer.js")
    # The whole control is gone: no dependency, no button, no listener, no copy.
    # Read against the CODE — the module explains in prose which control was
    # removed and why, and a naive substring check reads that as the control.
    code = _code(composer)
    for gone in ("onAssign", "assignBtn", "ASSIGN_TITLE", "composer-assign", "clipboard"):
        assert gone not in code, gone
    # The composer names no form module of its own — it never did, and now it
    # has no route to one at all.
    assert "AssignForm" not in composer
    assert "openAssignForm" not in composer
    # What is left in the row is the field and Send, in that order.
    row = composer.split("class: 'composer-row' }", 1)[1].split(")", 1)[0]
    assert row.strip().startswith(", input, sendBtn"), row

    controller = _read(CONVERSATION / "conversation.js")
    # Still exactly one caller of exactly one form. The empty state is its only
    # caller here now, which is why the named function stayed rather than being
    # folded back into the composer's dependency.
    assert "function openAssign() {" in controller
    assert "return BossModAssignForm.openAssignForm({" in controller
    assert controller.count("BossModAssignForm.openAssignForm(") == 1
    assert "onAssign: openAssign," in controller
    # ...and the composer is not handed it any more.
    createComposer = controller.split("BossModComposer.createComposer({", 1)[1].split("});", 1)[0]
    assert "onAssign" not in createComposer, createComposer

    # There is still exactly one assign form in the codebase.
    definers = sorted(
        path.relative_to(JS).as_posix()
        for path in _app_js()
        if "function openAssignForm(" in _read(path)
    )
    assert definers == ["places/board/assign-form.js"], definers
    callers = sorted(
        path.relative_to(JS).as_posix()
        for path in _app_js()
        if "openAssignForm(" in _read(path) and "function openAssignForm(" not in _read(path)
    )
    assert callers == ["conversation/conversation.js", "places/board/board-place.js"], callers

    # It is defined before the two scripts that open it.
    html = (ROOT / "ui" / "templates" / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"static_url\('([^']+\.js)'\)", html)
    assert scripts.index("js/places/board/assign-outcomes.js") < scripts.index(
        "js/places/board/assign-form.js"
    )
    assert scripts.index("js/places/board/assign-form.js") < scripts.index(
        "js/conversation/conversation.js"
    )
    assert scripts.count("js/places/board/assign-form.js") == 1


def test_conversation_assign_stamps_thread_origin() -> None:
    """Hire → thread → Assign must bind Created/Accepted to that thread.

    POST /api/tasks defaults source_channel to `api`, which origin_thread_target
    treats as Focus. Conversation assign therefore stamps the open room. Board
    assign must not inherit a leftover conversationId from the store.
    """
    controller = _read(CONVERSATION / "conversation.js")
    assign = controller.split("function openAssign() {", 1)[1].split(
        "const composer = BossModComposer.createComposer({", 1
    )[0]
    assert "bindOrigin: true" in assign

    form = _read(JS / "places" / "board" / "assign-form.js")
    assert "bindOrigin && state.conversationKind === 'thread'" in form
    assert "payload.source_channel = 'channel'" in form
    assert "payload.notification_channel_id = state.conversationId" in form
    assert "payload.source_channel = 'chat'" in form

    board = _read(JS / "places" / "board" / "board-place.js")
    board_assign = board.split("function openAssign() {", 1)[1].split(
        "return {", 1
    )[0]
    assert "bindOrigin" not in board_assign


def test_conversation_css_uses_tokens_only() -> None:
    css = _read(CSS / "conversation.css")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "colour comes from tokens.css"
    # The visual half of the consent-collapse behaviour.
    assert ".host-path-consent-card.is-resolved" in css


def test_conversation_modules_stay_focused() -> None:
    for path in sorted(CONVERSATION.rglob("*.js")):
        lines = len(_read(path).splitlines())
        assert lines < 400, f"{path.relative_to(JS)} is {lines} lines"
    place = JS / "places" / "chat" / "chat-place.js"
    assert len(_read(place).splitlines()) < 150
