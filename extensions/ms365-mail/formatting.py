"""Microsoft 365 Mailbox — agents write mail in Markdown; it is sent as HTML.

``html: False`` makes markdown-it escape any raw HTML in the agent's text, so
the only markup in the result is what the Markdown itself produced and no
sanitiser is needed. ``breaks: True`` keeps a single newline as a line break,
so a signature or an address block does not collapse onto one line. The
result is wrapped in one unstyled ``<div>``: mail clients apply their own
styles. Reading and the operator's viewer stay plain text.
"""

from __future__ import annotations

from markdown_it import MarkdownIt

# Option names checked against markdown-it-py 4.0.0 (markdown_it.utils.OptionsType).
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": False})


def render_body(markdown_text: str) -> str:
    """Render an agent's Markdown message body to an HTML email body (pure).

    Args:
        markdown_text: The message as the agent wrote it.

    Returns:
        ``<div>…</div>`` holding the rendered HTML; raw HTML in the input is
        escaped, never passed through.
    """
    return f"<div>{_MARKDOWN.render(markdown_text)}</div>"
