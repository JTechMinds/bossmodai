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
    JS / "core" / "store.js",
    JS / "core" / "bus.js",
    JS / "core" / "gates.js",
    JS / "core" / "consent-card.js",
    CONVERSATION / "transcript.js",
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


def test_transcript_harness() -> None:
    result = subprocess.run(
        [
            "node",
            str(TRANSCRIPT_HARNESS),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "gates.js"),
            str(CONVERSATION / "transcript.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "dedupes": True,
        "keylessAlwaysAppends": True,
        "sticksWhenNearBottom": True,
        "keepsScrollWhenReading": True,
        "announcesNewCount": True,
        "presenceScoped": True,
        "cacheCopies": True,
    }


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
