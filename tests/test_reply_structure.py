"""Harness: the model must *emit* short paragraphs, newlines, and list lines.

UI markdown rendering is not the proof. A single-line string or already-rendered
HTML fails even when marked.js would pretty-print it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.agent_loop.reply_structure import assess_emitted_shape, extract_emitted_chat
from core.agent_loop.runtime_core import CHAT_FORMATTING, format_runtime_core_block
from core.models.agent import Agent


SHAPED_REPLY = """The claim does not hold.

- Tests never ran.
- The named path is missing.

CLEAR is denied until those two exist."""

WALL_OF_TEXT = (
    "The claim does not hold because the tests never ran and the named path is "
    "missing so CLEAR is denied until those two exist and also the write-up is "
    "one dense brick with no breaks at all which is exactly the wall of text "
    "the runtime core told the model not to emit when answering in chat today."
)

RENDERED_HTML = (
    "<p>The claim does not hold.</p><ul><li>Tests never ran.</li>"
    "<li>The named path is missing.</li></ul>"
)

SINGLE_LINE_MARKDOWN = (
    "The claim does not hold. - Tests never ran. - The named path is missing. "
    "CLEAR is denied until those two exist."
)

SHAPED_DECISION = json.dumps({
    "act": "reply",
    "intent": "question",
    "msg": SHAPED_REPLY,
    "commit": "none",
})


def test_harness_accepts_emitted_short_paragraphs_and_list_lines() -> None:
    shape = assess_emitted_shape(SHAPED_REPLY)
    assert shape.chat_text == SHAPED_REPLY
    assert shape.has_real_newlines
    assert shape.short_paragraphs
    assert shape.not_a_wall
    assert shape.list_lines_emitted
    assert shape.not_rendered_html
    assert shape.passes


def test_harness_rejects_a_wall_of_text() -> None:
    shape = assess_emitted_shape(WALL_OF_TEXT)
    assert not shape.has_real_newlines
    assert not shape.not_a_wall
    assert not shape.passes


def test_harness_rejects_rendered_html_even_if_ui_could_pretty_print() -> None:
    shape = assess_emitted_shape(RENDERED_HTML)
    assert not shape.not_rendered_html
    assert not shape.passes


def test_harness_rejects_single_line_markdown() -> None:
    shape = assess_emitted_shape(SINGLE_LINE_MARKDOWN)
    assert not shape.has_real_newlines
    assert not shape.not_a_wall
    assert not shape.passes


def test_harness_scores_the_emitted_msg_not_the_json_wrapper() -> None:
    assert extract_emitted_chat(SHAPED_DECISION) == SHAPED_REPLY
    assert assess_emitted_shape(SHAPED_DECISION).passes
    say_envelope = json.dumps({"say": SHAPED_REPLY, "actions": []})
    assert extract_emitted_chat(say_envelope) == SHAPED_REPLY
    assert assess_emitted_shape(say_envelope).passes
    walled = json.dumps({
        "act": "reply",
        "intent": "question",
        "msg": WALL_OF_TEXT,
        "commit": "none",
    })
    assert not assess_emitted_shape(walled).passes


def test_runtime_core_steer_itself_emits_the_locked_shape() -> None:
    """The steer is stronger than a one-line reminder: it models the shape."""
    assert "\n" in CHAT_FORMATTING
    assert "- Use markdown lists" in CHAT_FORMATTING
    assert "must emit" in CHAT_FORMATTING
    assert assess_emitted_shape(CHAT_FORMATTING).passes
    agent = Agent(
        id="preview",
        storage_key="preview",
        name="Remy",
        role="Writer",
        created_at=datetime.now(timezone.utc),
    )
    core = format_runtime_core_block(agent)
    assert CHAT_FORMATTING in core
    assert assess_emitted_shape(CHAT_FORMATTING).has_real_newlines
