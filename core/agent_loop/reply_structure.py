"""Harness for the *emitted* shape of a chat reply.

The UI can render a one-line markdown string as pretty HTML. This module
scores the raw text the model emitted — real newlines, short paragraphs,
markdown list lines — not what a renderer would turn it into.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LIST_LINE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S")
_RENDERED_HTML = re.compile(r"</?(?:p|ul|ol|li|br|div)\b", re.I)
_STEP_CUE = re.compile(
    r"\b(?:step|steps|plan|then|next|first|second|third|finally)\b",
    re.I,
)

# A paragraph longer than this without a break is a wall, not a short note.
_MAX_PARAGRAPH_CHARS = 420
_MAX_SENTENCES_PER_PARAGRAPH = 3


@dataclass(frozen=True)
class EmittedShape:
    """Whether one emitted reply has the locked chat shape."""

    has_real_newlines: bool
    short_paragraphs: bool
    not_a_wall: bool
    list_lines_emitted: bool
    not_rendered_html: bool
    chat_text: str

    @property
    def passes(self) -> bool:
        """True only when the raw emission — not rendered HTML — has the shape."""
        return (
            self.has_real_newlines
            and self.short_paragraphs
            and self.not_a_wall
            and self.not_rendered_html
            and self.list_lines_emitted
        )


def extract_emitted_chat(raw: str) -> str:
    """Return the chat text a decision completion actually emitted.

    Decision turns wrap the operator-visible reply in ``msg``. Scoring the
    wrapper JSON would pass on punctuation the model did not emit as chat.
    A bare string is treated as the reply itself.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return raw
    if not isinstance(payload, dict):
        return raw
    for key in ("msg", "reply"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("msg", "reply", "sum"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return raw


def assess_emitted_shape(raw: str) -> EmittedShape:
    """Score one model emission for short paragraphs, newlines, and list lines.

    Rendered HTML and a single dense brick fail even when a markdown renderer
    would pretty-print them.
    """
    chat = extract_emitted_chat(raw)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", chat) if part.strip()]
    if not paragraphs:
        paragraphs = [chat.strip()] if chat.strip() else []
    lines = [line for line in chat.splitlines() if line.strip()]
    has_real_newlines = "\n" in chat
    longest = max((len(part) for part in paragraphs), default=0)
    sentence_ok = all(
        len(_SENTENCE_SPLIT.split(part)) <= _MAX_SENTENCES_PER_PARAGRAPH
        for part in paragraphs
    ) if paragraphs else False
    short_paragraphs = bool(paragraphs) and longest <= _MAX_PARAGRAPH_CHARS and sentence_ok
    not_a_wall = has_real_newlines and len(lines) >= 2 and longest <= _MAX_PARAGRAPH_CHARS
    list_lines = [line for line in chat.splitlines() if _LIST_LINE.match(line)]
    needs_list = _needs_markdown_list(chat)
    list_lines_emitted = (not needs_list) or len(list_lines) >= 2
    not_rendered_html = _RENDERED_HTML.search(chat) is None
    return EmittedShape(
        has_real_newlines=has_real_newlines,
        short_paragraphs=short_paragraphs,
        not_a_wall=not_a_wall,
        list_lines_emitted=list_lines_emitted,
        not_rendered_html=not_rendered_html,
        chat_text=chat,
    )


def _needs_markdown_list(text: str) -> bool:
    """True when the reply is talking through steps and must emit a list."""
    if _STEP_CUE.search(text) is None:
        return False
    # A one-word mention of "plan" in a status note is not a step list.
    return len(text.split()) >= 12


def assess_decision_reply(raw: str) -> EmittedShape:
    """Alias for :func:`assess_emitted_shape` kept for harness call sites."""
    return assess_emitted_shape(raw)
