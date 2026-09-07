"""Meeting UI: routine updates must not remount the session shell."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_meeting_session_harness.cjs"


def test_conversation_never_remounts_on_world_update() -> None:
    """A routine tick must not remount the transcript.

    The old guard was a predicate — reload only when the activity kind
    changed. The conversation surface does not subscribe to `world_update` at
    all, so the property is now structural rather than conditional. That is a
    strengthening: there is no branch left to get wrong.
    """
    for name in ("conversation.js", "transcript.js", "sources/agent-source.js"):
        source = (JS / "conversation" / name).read_text(encoding="utf-8")
        assert "world_update" not in source


def test_meeting_message_appends_into_agent_conversation() -> None:
    """A meeting turn appends; it never reloads the conversation.

    Unconditional remount on every WS message was the P0. In the merged
    surface there is no reload path left to take at all.
    """
    source = (JS / "conversation" / "sources" / "agent-source.js").read_text(encoding="utf-8")
    handler = source.split("bus.subscribe('meeting_message', (data) => {", 1)[1].split(
        "];", 1
    )[0]
    assert "on.message(" in handler
    assert "showAuthor: true" in handler
    for reload in ("load()", "reload", "remount", "renderMeeting"):
        assert reload not in handler, f"the meeting handler must not {reload}"


def test_open_reuses_cached_transcript_without_loading_flash() -> None:
    """Re-opening a conversation paints the cache before any loading state."""
    source = (JS / "conversation" / "conversation.js").read_text(encoding="utf-8")
    body = source.split("async function open(conversationId, kind) {", 1)[1]

    assert "cache.recall(" in body
    assert "const loadId = generation.next()" in body
    assert body.index("const loadId = generation.next()") < body.index("await source.load()")
    # The cached paint precedes the loading state, so a re-click never flashes.
    assert body.index("if (cached) paint(cached);") < body.index("transcript.setStatus('loading')")


def test_meeting_session_dom_harness_preserves_draft_and_dedups() -> None:
    result = subprocess.run(
        [
            "node",
            str(HARNESS),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "gates.js"),
            str(JS / "conversation" / "transcript.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "mounted": True,
        "preservedDraft": True,
        "messageCount": 2,
    }
