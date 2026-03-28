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

    text: str
    lines: tuple[str, ...]
    line_start_offsets: tuple[int, ...]
    sections: tuple[MarkdownSection, ...]


def parse_markdown_document(text: str) -> MarkdownDocument:
    """Parse markdown headings into addressable sections."""
    normalized_text = text.replace("\r\n", "\n")
    lines = tuple(normalized_text.splitlines())
    line_start_offsets: list[int] = []
    offset = 0
    for raw_line in normalized_text.splitlines(keepends=True):
        line_start_offsets.append(offset)
        offset += len(raw_line)
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
    return MarkdownDocument(
        text=normalized_text,
        lines=lines,
        line_start_offsets=tuple(line_start_offsets),
        sections=tuple(sections),
    )


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
    body_start, body_end = _section_body_offsets(document, section)
    existing_body = document.text[body_start:body_end]
    leading_padding, trailing_padding = _preserve_section_padding(
        existing_body,
        has_following_section=section.end_index < len(document.lines),
    )
    replacement_body = new_body.strip("\n")
    replacement = f"{leading_padding}{replacement_body}{trailing_padding}"
    return f"{document.text[:body_start]}{replacement}{document.text[body_end:]}"


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


def _line_start_offset(document: MarkdownDocument, line_index: int) -> int:
    """Return the character offset where one logical line starts."""
    if line_index < len(document.line_start_offsets):
        return document.line_start_offsets[line_index]
    return len(document.text)


def _section_body_offsets(document: MarkdownDocument, section: MarkdownSection) -> tuple[int, int]:
    """Return the character offsets for one section body slice."""
    return (
        _line_start_offset(document, section.body_start_index),
        _line_start_offset(document, section.end_index),
    )


def _preserve_section_padding(existing_body: str, *, has_following_section: bool) -> tuple[str, str]:
    """Preserve the section's boundary newlines without reformatting the file."""
    if not existing_body:
        trailing = "\n" if has_following_section else ""
        return "", trailing

    if existing_body.strip("\n"):
        leading_count = 0
        while leading_count < len(existing_body) and existing_body[leading_count] == "\n":
            leading_count += 1

        trailing_count = 0
        while len(existing_body) - trailing_count > leading_count and existing_body[-(trailing_count + 1)] == "\n":
            trailing_count += 1

        leading = existing_body[:leading_count]
        trailing = existing_body[len(existing_body) - trailing_count:] if trailing_count else ""
        return leading, trailing

    leading = "\n"
    trailing_count = max(len(existing_body) - 1, 0)
    if has_following_section:
        trailing_count = max(trailing_count, 1)
    return leading, "\n" * trailing_count
