"""Microsoft 365 Mailbox: agents write Markdown, mail goes out as HTML (formatting.py)."""

from __future__ import annotations

import importlib
import json

import httpx

from core.extensions.contract import ExtensionContext
from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_ENTRY = get_discovery().get("ms365-mail")
_PACKAGE = import_package(_ENTRY)
auth = importlib.import_module(f"{_PACKAGE.__name__}.auth")
formatting = importlib.import_module(f"{_PACKAGE.__name__}.formatting")

render = formatting.render_body


def test_headings_lists_bold_and_links_render() -> None:
    html = render("# Daily report\n\n- **Done:** login fix\n- [RCA](https://example.com/rca)\n\n1. one\n2. two")
    assert html.startswith("<div>") and html.endswith("</div>")
    assert "<h1>Daily report</h1>" in html
    assert "<li><strong>Done:</strong> login fix</li>" in html
    assert '<a href="https://example.com/rca">RCA</a>' in html
    assert "<ol>" in html and "<li>one</li>" in html


def test_raw_html_is_escaped_never_passed_through() -> None:
    html = render('Hi <script>alert(1)</script> and <b>bold</b>\n\n<div onclick="x">block</div>')
    assert "<script>" not in html and "<b>" not in html and "<div onclick" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html


def test_single_newlines_become_line_breaks() -> None:
    assert render("Thanks,\nIris\nReports team") == "<div><p>Thanks,<br />\nIris<br />\nReports team</p>\n</div>"


def test_bare_urls_are_not_linkified_and_no_style_is_added() -> None:
    html = render("see https://example.com")
    assert "<a " not in html and "style=" not in html


def test_mail_send_posts_the_rendered_body_as_html(tmp_path) -> None:
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(202)

    class Tokens:
        def acquire(self, tenant_id, client_id, secret):
            return auth.AccessToken(token="tok", expires_at=9e9)

    from types import SimpleNamespace

    from core.bm_cli.parser import parse_cli_command
    from core.bm_cli.types import CliExecutionContext

    config = {"tenant_id": "t", "client_id": "c", "client_secret": "s", "mailbox": "reports@contoso.com"}
    ctx = ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path, read_agent_config=lambda agent_id: config)
    extension = _PACKAGE.Ms365MailExtension(ctx, transport=httpx.MockTransport(handler), tokens=Tokens())
    cli = CliExecutionContext(agent=SimpleNamespace(id="agent-1"), state=None, cwd="/me")
    result = extension.handle(cli, parse_cli_command("mail send jordan@contoso.com --subject Report"), "**Done**\nIris")
    assert result.ok, result.prompt_content
    body = posted[0]["message"]["body"]
    assert body == {"contentType": "HTML", "content": "<div><p><strong>Done</strong><br />\nIris</p>\n</div>"}
