"""Labeled pack sections for hire-compatible description / done text.

Senior packs can declare Mission, In scope, Out of scope, Handoff, and
Fail examples either as headings inside ``description`` /
``what_done_looks_like`` or as additive YAML fields that fold into those
two hire strings. Canonical form is labeled text so hydrate stays on
``role`` / ``description`` / ``done_fail_bar``.
"""

from __future__ import annotations

import re

DESCRIPTION_SECTION_KEYS = ("mission", "in_scope", "out_of_scope", "handoff")
DONE_SECTION_KEYS = ("fail_examples",)

_SECTION_TITLES: dict[str, str] = {
    "mission": "Mission",
    "in_scope": "In scope",
    "out_of_scope": "Out of scope",
    "handoff": "Handoff",
    "fail_examples": "Fail examples",
    "done": "What done looks like",
}

_LABEL_ALIASES: dict[str, str] = {
    "mission": "mission",
    "in scope": "in_scope",
    "in-scope": "in_scope",
    "in_scope": "in_scope",
    "out of scope": "out_of_scope",
    "out-of-scope": "out_of_scope",
    "out_of_scope": "out_of_scope",
    "handoff": "handoff",
    "hand off": "handoff",
    "hand-off": "handoff",
    "fail examples": "fail_examples",
    "fail example": "fail_examples",
    "failure examples": "fail_examples",
    "failure example": "fail_examples",
    "fail_examples": "fail_examples",
    "what done looks like": "done",
    "what-done-looks-like": "done",
    "what_done_looks_like": "done",
}

_HASH_PREFIX_RE = re.compile(r"^#{1,6}\s+")
_SPACE_RE = re.compile(r"[\s_-]+")


def canonical_section_label(raw: str) -> str | None:
    """Map a heading to a section key, or None if it is not a known label."""
    folded = _SPACE_RE.sub(" ", (raw or "").strip().lower()).strip()
    if not folded:
        return None
    return _LABEL_ALIASES.get(folded)


def extract_labeled_sections(text: str) -> tuple[str, dict[str, str]]:
    """Split labeled headings out of description or done text.

    Returns ``(preamble, sections)``. Preamble is the unlabeled lead-in
    (the success bar in ``what_done_looks_like``).
    """
    if not (text or "").strip():
        return "", {}
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    preamble_lines: list[str] = []
    bodies: dict[str, list[str]] = {}
    current: str | None = None
    for line in normalized.split("\n"):
        key, inline = _match_heading(line)
        if key is not None:
            current = key
            bodies.setdefault(key, [])
            if inline:
                bodies[key].append(inline)
            continue
        if current is None:
            preamble_lines.append(line)
        else:
            bodies[current].append(line)
    preamble = "\n".join(preamble_lines).strip()
    sections = {
        key: "\n".join(lines).strip()
        for key, lines in bodies.items()
        if "\n".join(lines).strip()
    }
    return preamble, sections


def first_unlabeled_line(text: str) -> str | None:
    """First unlabeled preamble line — When-to-hire fallback for browse cards."""
    preamble, _ = extract_labeled_sections(text)
    if not preamble:
        return None
    for line in preamble.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            return cleaned
    return None


def compose_labeled_description(preamble: str, sections: dict[str, str]) -> str:
    """Render canonical Mission / scope / Handoff labeled description text."""
    parts: list[str] = []
    lead = (preamble or "").strip()
    if lead:
        parts.append(lead)
    for key in DESCRIPTION_SECTION_KEYS:
        body = (sections.get(key) or "").strip()
        if body:
            parts.append(_format_section(key, body))
    return "\n\n".join(parts).strip()


def compose_labeled_done(success: str, fail_examples: str | None) -> str:
    """Render canonical done text with a Fail examples section when present."""
    bar = (success or "").strip()
    fail = (fail_examples or "").strip()
    if not fail:
        return bar
    formatted = _format_section("fail_examples", fail)
    if not bar:
        return formatted
    return f"{bar}\n\n{formatted}"


def _format_section(key: str, body: str) -> str:
    title = _SECTION_TITLES[key]
    if "\n" in body:
        return f"{title}:\n{body}"
    return f"{title}: {body}"


def _match_heading(line: str) -> tuple[str | None, str | None]:
    stripped = line.strip()
    if not stripped:
        return None, None
    stripped = _HASH_PREFIX_RE.sub("", stripped, count=1)
    if ":" in stripped:
        label, rest = stripped.split(":", 1)
        key = canonical_section_label(label)
        if key is None:
            return None, None
        remainder = rest.strip()
        return key, remainder or None
    key = canonical_section_label(stripped)
    if key is None:
        return None, None
    return key, None
