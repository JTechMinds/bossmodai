"""BossMod AI — Markdown document helpers for targeted CLI edits."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ATX_HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$")
_SETEXT_UNDERLINE_RE = re.compile(r"^[ \t]{0,3}(=+|-+)[ \t]*$")
_SECTION_SELECTOR_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_WHITESPACE_RE = re.compile(r"\s+")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    """One markdown section with line/index boundaries."""

    title: str
    level: int
    heading_start_index: int
    body_start_index: int
    end_index: int

    @property
    def heading_line(self) -> int:
        """Return the 1-based line number where the heading starts."""
        return self.heading_start_index + 1

    @property
    def display_heading(self) -> str:
        """Return the canonical markdown heading label."""
        return f"{'#' * self.level} {self.title}"


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    """Parsed markdown document with addressable sections."""

    lines: tuple[str, ...]
    sections: tuple[MarkdownSection, ...]


def parse_markdown_document(text: str) -> MarkdownDocument:
    """Parse markdown headings into addressable sections."""
    lines = tuple(text.replace("\r\n", "\n").splitlines())
    headings: list[tuple[int, int, int, str]] = []

    open_fence: tuple[str, int] | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        fence = _parse_fence(line)
        if fence is not None:
            if open_fence is None:
                open_fence = fence
            elif fence[0] == open_fence[0] and fence[1] >= open_fence[1]:
                open_fence = None
            index += 1
            continue
        if open_fence is not None:
            index += 1
            continue

        atx_match = _ATX_HEADING_RE.match(line)
        if atx_match is not None:
            title = _clean_heading_title(atx_match.group(2))
            if title:
                level = len(atx_match.group(1))
                headings.append((index, index + 1, level, title))
            index += 1
            continue

        if index + 1 < len(lines):
            underline_match = _SETEXT_UNDERLINE_RE.match(lines[index + 1])
            title = _clean_heading_title(line)
            if underline_match is not None and title:
                level = 1 if underline_match.group(1).startswith("=") else 2
                headings.append((index, index + 2, level, title))
                index += 2
                continue

        index += 1

    sections: list[MarkdownSection] = []
    for position, (heading_start_index, body_start_index, level, title) in enumerate(headings):
        next_start = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        sections.append(
            MarkdownSection(
                title=title,
                level=level,
                heading_start_index=heading_start_index,
                body_start_index=body_start_index,
                end_index=next_start,
            )
        )
    return MarkdownDocument(lines=lines, sections=tuple(sections))


def render_markdown_outline_entries(document: MarkdownDocument) -> list[str]:
    """Render the parsed markdown outline as prompt-friendly lines."""
    return [
        f"line {section.heading_line}: {'  ' * (section.level - 1)}{section.display_heading}"
        for section in document.sections
    ]


def find_markdown_section(document: MarkdownDocument, selector: str) -> MarkdownSection:
    """Resolve one unique markdown section from a quoted heading selector."""
    selector_level, selector_title = _parse_section_selector(selector)
    matches = [
        section
        for section in document.sections
        if _normalize_heading(section.title) == selector_title
        and (selector_level is None or section.level == selector_level)
    ]
    if not matches:
        raise ValueError(f'No markdown section matches "{selector.strip()}".')
    if len(matches) > 1:
        options = ", ".join(describe_markdown_section(item) for item in matches)
        raise ValueError(
            f'Multiple markdown sections match "{selector.strip()}": {options}. '
            "Use a more specific heading such as \"## Heading\"."
        )
    return matches[0]


def get_markdown_section_body(document: MarkdownDocument, section: MarkdownSection) -> str:
    """Return the current body text for one markdown section."""
    return "\n".join(document.lines[section.body_start_index:section.end_index]).strip()


def replace_markdown_section_body(
    document: MarkdownDocument,
    section: MarkdownSection,
    new_body: str,
) -> str:
    """Replace only one markdown section body and return the updated file text."""
    prefix = list(document.lines[:section.body_start_index])
    suffix = list(document.lines[section.end_index:])
    updated_lines = prefix + _render_section_body_lines(new_body) + suffix
    return "\n".join(updated_lines)


def describe_markdown_section(section: MarkdownSection) -> str:
    """Return a human-readable section reference."""
    return f'{section.display_heading} (line {section.heading_line})'


def _parse_fence(line: str) -> tuple[str, int] | None:
    """Return the code-fence marker for one line, if present."""
    match = _FENCE_RE.match(line)
    if match is None:
        return None
    token = match.group(1)
    return token[0], len(token)


def _clean_heading_title(raw: str) -> str:
    """Normalize one markdown heading title."""
    return raw.strip().strip("#").strip()


def _parse_section_selector(selector: str) -> tuple[int | None, str]:
    """Parse an optional heading-level selector like '## Heading'."""
    cleaned = selector.strip()
    if not cleaned:
        raise ValueError("Section heading must be non-empty.")
    match = _SECTION_SELECTOR_RE.match(cleaned)
    if match is not None:
        return len(match.group(1)), _normalize_heading(match.group(2))
    return None, _normalize_heading(cleaned)


def _normalize_heading(value: str) -> str:
    """Normalize a heading for case-insensitive matching."""
    return _WHITESPACE_RE.sub(" ", value.strip()).casefold()


def _render_section_body_lines(new_body: str) -> list[str]:
    """Render a replacement section body with stable markdown spacing."""
    if not new_body.strip():
        return [""]
    body_lines = new_body.strip("\n").split("\n")
    return ["", *body_lines, ""]
