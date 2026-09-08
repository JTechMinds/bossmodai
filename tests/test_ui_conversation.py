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
    JS / "core" / "avatar.js",
    JS / "core" / "switch.js",
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    CONVERSATION / "empty-state.js",
    CONVERSATION / "transcript.js",
    CONVERSATION / "transcript-cache.js",
    CONVERSATION / "message.js",
    CONVERSATION / "event-cards.js",
    CONVERSATION / "chrome.js",
    CONVERSATION / "composer.js",
    CONVERSATION / "system-receipts.js",
    JS / "needs" / "needs-bar.js",
    SOURCES / "thread-archive.js",
    SOURCES / "thread-source.js",
    SOURCES / "agent-source.js",
    CONVERSATION / "conversation.js",
]


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
        # actions carry glyphs, the receipts preference lives in the action row,
        # and an empty conversation offers the two things you can do in it.
        "chromeShowsIdentityAvatar": True,
        "chromeAvatarNodeIsStable": True,
        "chromeActionCarriesItsIcon": True,
        "chromeGroupGlyphForThreads": True,
        "receiptsLiveInTheActionRow": True,
        "emptyConversationOffersActions": True,
        "greetingWentThroughTheComposer": True,
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


def test_composer_opens_the_one_assign_form() -> None:
    """Spec 4.4's deferred clipboard button, finished in the phase that owns
    the form.

    Phase 2 deliberately did NOT build an assign form, because deduplicating a
    second one against the Board's in Phase 3 would have been exactly the
    transitional scaffolding this refactor exists to avoid. So the property
    that matters is not "the composer has a button" — it is that the button
    reaches the one form there is, and that the composer cannot name it.
    """
    composer = _read(CONVERSATION / "composer.js")
    # Injected, and required: a clipboard that rendered and did nothing would
    # be worse than one that is absent.
    assert "deps.onAssign" in composer
    assert "throw new Error('[composer] deps.onAssign is required');" in composer
    assert "onclick: onAssignClick" in composer
    assert "function onAssignClick()" in composer
    # Icon-only, so it carries its own accessible name (spec 8.4).
    assert "'aria-label': ASSIGN_TITLE" in composer
    assert "const ASSIGN_TITLE = 'Assign a task';" in composer
    # Disabled by the same gate as Send, in the same place, so the two can
    # never disagree about whether this conversation can be acted on.
    apply_state = composer.split("function applyState() {", 1)[1].split("\n        }", 1)[0]
    assert "assignBtn.disabled = !enabled;" in apply_state
    assert "sendBtn.disabled = !enabled;" in apply_state
    # And it lets its listener go, like every other control here.
    assert "assignBtn.removeEventListener('click', onAssignClick);" in composer
    # The composer names no form module of its own.
    assert "AssignForm" not in composer
    assert "openAssignForm" not in composer

    controller = _read(CONVERSATION / "conversation.js")
    # Hoisted to a named function in the visual-parity pass, because the empty
    # state opens the same form. Still exactly one caller of exactly one form,
    # which is the property this guards.
    assert "function openAssign() {" in controller
    assert "return BossModAssignForm.openAssignForm({" in controller
    assert controller.count("BossModAssignForm.openAssignForm(") == 1
    assert "onAssign: openAssign," in controller

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


def test_conversation_css_uses_tokens_only() -> None:
    css = _read(CSS / "conversation.css")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "colour comes from tokens.css"
    # The visual half of the consent-collapse behaviour.
    assert ".host-path-consent-card.is-resolved" in css


def test_conversation_modules_stay_focused() -> None:
    for path in sorted(CONVERSATION.rglob("*.js")):
        lines = len(_read(path).splitlines())
        assert lines < 300, f"{path.relative_to(JS)} is {lines} lines"
    place = JS / "places" / "chat" / "chat-place.js"
    assert len(_read(place).splitlines()) < 150
