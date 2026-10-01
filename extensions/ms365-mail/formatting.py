"""Microsoft 365 Mailbox — agents write mail in Markdown; sends and replies go out as HTML.

``html: False`` makes markdown-it escape any raw HTML in the agent's text, so
the only markup in the result is what the Markdown itself produced and no
sanitiser is needed. ``breaks: True`` keeps a single newline as a line break,
so a signature or an address block does not collapse onto one line. On top of
CommonMark, pipe tables and ``~~strikethrough~~`` are enabled.

Tables are styled with inline ``style`` attributes (borders, padding), because
mail clients drop or ignore ``<style>`` blocks unevenly and a bare ``<table>``
reads as loosely aligned text. The styles come from the manifest defaults
(``EmailStyles``), never from the agent's text. Everything else stays unstyled,
wrapped in one plain ``<div>``, so mail clients apply their own styles. Reading
and the operator's viewer stay plain text.
"""

from __future__ import annotations

from dataclasses import dataclass

from markdown_it import MarkdownIt

# Option names checked against markdown-it-py 4.0.0 (markdown_it.utils.OptionsType).
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": False}).enable(
    ["table", "strikethrough"]
)


@dataclass(frozen=True)
class EmailStyles:
    """Inline CSS applied to rendered tables (from the manifest ``defaults``).

    Attributes:
        table: ``style`` for every ``<table>``.
        header_cell: ``style`` for every ``<th>``.
        cell: ``style`` for every ``<td>``.
    """

    table: str
    header_cell: str
    cell: str


def render_body(markdown_text: str, styles: EmailStyles) -> str:
    """Render an agent's Markdown message body to an HTML email body (pure).

    Table, header-cell and cell tags get the matching inline style. A column
    alignment markdown-it already set (``style="text-align:…"``) is kept after
    the configured style, so it wins over any ``text-align`` in that style.

    Args:
        markdown_text: The message as the agent wrote it.
        styles: Inline styles for table markup.

    Returns:
        ``<div>…</div>`` holding the rendered HTML; raw HTML in the input is
        escaped, never passed through.
    """
    by_type = {"table_open": styles.table, "th_open": styles.header_cell, "td_open": styles.cell}
    tokens = _MARKDOWN.parse(markdown_text)
    for token in tokens:
        style = by_type.get(token.type)
        if style is None:
            continue
        existing = token.attrGet("style")
        if existing:
            separator = "" if style.endswith(";") else ";"
            style = f"{style}{separator}{existing}"
        token.attrSet("style", style)
    return f"<div>{_MARKDOWN.renderer.render(tokens, _MARKDOWN.options, {})}</div>"
