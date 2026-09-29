"""Microsoft 365 Mailbox: the fixed-path Graph client (graph.py) over httpx.MockTransport."""

from __future__ import annotations

import importlib
import json

import httpx
import pytest

from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_ENTRY = get_discovery().get("ms365-mail")
_PACKAGE = import_package(_ENTRY)
auth = importlib.import_module(f"{_PACKAGE.__name__}.auth")
graph = importlib.import_module(f"{_PACKAGE.__name__}.graph")

BASE = "https://graph.example.test/v1.0"
MAILBOX = "reports@contoso.com"
ROOT = f"{BASE}/users/{MAILBOX}"
SECRET = "the-client-secret"
CONFIG = {"tenant_id": "t", "client_id": "c", "client_secret": SECRET, "mailbox": MAILBOX}


class FakeTokens:
    """A TokenProvider that hands out one fixed token and records calls."""

    def __init__(self, error=None) -> None:
        self.calls = []
        self.error = error

    def acquire(self, tenant_id, client_id, secret):
        self.calls.append((tenant_id, client_id, secret))
        if self.error is not None:
            raise self.error
        return auth.AccessToken(token="tok", expires_at=9e9)


def _mailbox(handler, tokens=None, config=CONFIG):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return graph.GraphMailbox(config, graph_base=BASE, tokens=tokens or FakeTokens(), client=client)


def _message(graph_id="AAMk-1", **overrides):
    item = {
        "id": graph_id, "subject": "Hello", "from": {"emailAddress": {"name": "Alice Doe", "address": "alice@x.com"}},
        "receivedDateTime": "2026-09-29T09:14:00Z", "isRead": False, "bodyPreview": "Hi there", "hasAttachments": False,
    }
    item.update(overrides)
    return item


def test_every_request_stays_under_the_mailbox_root() -> None:
    urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer tok"
        if request.method == "GET" and request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"value": [_message()]})
        if request.method == "GET":
            return httpx.Response(200, json={**_message(), "toRecipients": [], "body": {"content": "text"}})
        return httpx.Response(202 if request.method == "POST" else 200, json={})

    mailbox = _mailbox(handler)
    mailbox.list_inbox(top=5, skip=0, unread_only=True)
    mailbox.get_message("AAMk-1")
    mailbox.mark_read("AAMk-1")
    mailbox.reply("AAMk-1", "ok", reply_all=False)
    mailbox.reply("AAMk-1", "ok", reply_all=True)
    mailbox.send([graph.Address("", "bob@x.com")], [], "s", "b")

    assert len(urls) == 6
    assert all(url.startswith(ROOT + "/") for url in urls), urls
    paths = [httpx.URL(url).path for url in urls]
    assert paths == [
        "/v1.0/users/reports@contoso.com/mailFolders/inbox/messages",
        "/v1.0/users/reports@contoso.com/mailFolders/inbox/messages/AAMk-1",
        "/v1.0/users/reports@contoso.com/mailFolders/inbox/messages/AAMk-1",
        "/v1.0/users/reports@contoso.com/mailFolders/inbox/messages/AAMk-1/reply",
        "/v1.0/users/reports@contoso.com/mailFolders/inbox/messages/AAMk-1/replyAll",
        "/v1.0/users/reports@contoso.com/sendMail",
    ]


@pytest.mark.parametrize("hostile", ["AA/../../users/ceo@contoso.com", "AA?$select=x", "AA#frag", "../sendMail"])
def test_a_message_id_with_path_characters_is_quoted_and_stays_in_scope(hostile: str) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    _mailbox(handler).mark_read(hostile)
    raw_path = seen[0].url.raw_path.decode()
    assert raw_path.startswith("/v1.0/users/reports@contoso.com/mailFolders/inbox/messages/")
    tail = raw_path.rsplit("/messages/", 1)[1]
    assert "/" not in tail and "?" not in tail and "#" not in tail
    assert seen[0].url.params.get("$select") is None


def test_the_scope_check_refuses_a_url_outside_the_root() -> None:
    mailbox = _mailbox(lambda request: httpx.Response(200, json={}))
    with pytest.raises(graph.GraphScopeError):
        mailbox._call("GET", "@evil.example/x")


def test_the_inbox_query_selects_orders_pages_and_filters_unread() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"value": [_message()], "@odata.nextLink": f"{ROOT}/mailFolders/inbox/messages?$skip=20"})

    mailbox = _mailbox(handler)
    page = mailbox.list_inbox(top=10, skip=10, unread_only=True)
    params = seen[0].url.params
    assert params["$top"] == "10" and params["$skip"] == "10"
    assert params["$orderby"] == "receivedDateTime desc"
    # The $orderby property leads the $filter (Graph's InefficientFilter rule).
    assert params["$filter"].startswith("receivedDateTime ") and params["$filter"].endswith("isRead eq false")
    assert "bodyPreview" in params["$select"]
    assert b"+" not in seen[0].url.query and b"%20and%20isRead%20eq%20false" in seen[0].url.query
    assert page.has_more is True and len(seen) == 1  # nextLink read, never followed
    assert page.messages[0].sender.display() == "Alice Doe <alice@x.com>"
    mailbox.list_inbox(top=10, skip=0, unread_only=False)
    assert "$filter" not in seen[1].url.params


def test_get_message_asks_for_a_text_body() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={**_message(), "toRecipients": [{"emailAddress": {"address": "reports@contoso.com"}}],
                                         "ccRecipients": [], "body": {"contentType": "text", "content": "Line 1\r\nLine 2"}})

    message = _mailbox(handler).get_message("AAMk-1")
    assert seen[0].headers["Prefer"] == 'outlook.body-content-type="text"'
    assert message.body == "Line 1\r\nLine 2" and message.to[0].address == "reports@contoso.com"


def test_send_posts_plain_text_with_recipients() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(202)

    _mailbox(handler).send([graph.Address("Alice Doe", "alice@x.com")], [graph.Address("", "ops@x.com")], "Subj", "Body text")
    message = seen[0]["message"]
    assert message["body"] == {"contentType": "Text", "content": "Body text"}
    assert message["toRecipients"] == [{"emailAddress": {"address": "alice@x.com", "name": "Alice Doe"}}]
    assert message["ccRecipients"] == [{"emailAddress": {"address": "ops@x.com"}}]
    assert seen[0]["saveToSentItems"] is True


@pytest.mark.parametrize("status, headers, code, fragment", [
    (401, {}, "MAILBOX_ACCESS_DENIED", "Access is denied"),
    (403, {}, "MAILBOX_ACCESS_DENIED", "Access is denied"),
    (404, {}, "MESSAGE_NOT_FOUND", "mail inbox"),
    (429, {"Retry-After": "30"}, "GRAPH_THROTTLED", "retry after 30s"),
    (503, {"Retry-After": "7"}, "GRAPH_THROTTLED", "retry after 7s"),
    (429, {}, "GRAPH_THROTTLED", "no Retry-After"),
    (500, {}, "GRAPH_ERROR", "HTTP 500 ErrorCode: Access is denied"),
])
def test_http_errors_map_to_agent_facing_codes(status, headers, code, fragment) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, json={"error": {"code": "ErrorCode", "message": "Access is denied"}})

    with pytest.raises(graph.GraphHttpError) as caught:
        _mailbox(handler).mark_read("AAMk-1")
    mapped_code, message = graph.describe_graph_error(caught.value)
    assert mapped_code == code and fragment in message
    assert SECRET not in str(caught.value) and SECRET not in message


@pytest.mark.parametrize("error", [httpx.ConnectTimeout("timed out"), httpx.ConnectError("refused")])
def test_transport_errors_are_unreachable(error) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(graph.GraphUnreachable) as caught:
        _mailbox(handler).list_inbox(top=1, skip=0, unread_only=False)
    assert graph.describe_graph_error(caught.value)[0] == "GRAPH_UNREACHABLE"
    assert SECRET not in str(caught.value)


def test_a_token_failure_is_auth_failed_and_nothing_reaches_graph() -> None:
    calls = []
    tokens = FakeTokens(error=auth.GraphAuthError("AADSTS700016: Application not found"))
    mailbox = _mailbox(lambda request: calls.append(request) or httpx.Response(200, json={}), tokens=tokens)
    with pytest.raises(auth.GraphAuthError) as caught:
        mailbox.list_inbox(top=1, skip=0, unread_only=False)
    assert graph.describe_graph_error(caught.value) == ("MAILBOX_AUTH_FAILED", "AADSTS700016: Application not found")
    assert calls == []
    assert tokens.calls == [("t", "c", SECRET)]


def test_an_unreadable_listing_is_an_error_not_an_empty_inbox() -> None:
    with pytest.raises(graph.GraphHttpError, match="no value array"):
        _mailbox(lambda request: httpx.Response(200, json={"odd": 1})).list_inbox(top=1, skip=0, unread_only=False)


@pytest.mark.parametrize("dots", ["", ".", ".."])
def test_a_dot_segment_id_is_refused_before_any_request(dots: str) -> None:
    calls = []
    mailbox = _mailbox(lambda request: calls.append(request) or httpx.Response(200, json={}))
    with pytest.raises(graph.GraphScopeError):
        mailbox.mark_read(dots)
    assert calls == []


def test_the_scope_check_sees_the_url_after_dot_segments_resolve() -> None:
    mailbox = _mailbox(lambda request: httpx.Response(200, json={}))
    with pytest.raises(graph.GraphScopeError):
        mailbox._call("GET", "/../ceo@contoso.com/messages")


# ─── the extension's verify and view over this client ───


def _extension(tmp_path, handler, tokens, config=CONFIG):
    from core.extensions.contract import ExtensionContext

    ctx = ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path, read_agent_config=lambda agent_id: config)
    return _PACKAGE.Ms365MailExtension(ctx, transport=httpx.MockTransport(handler), tokens=tokens)


def test_verify_gives_a_plain_sentence_and_succeeds_with_a_readable_inbox(tmp_path) -> None:
    from core.extensions.contract import AgentConfigError

    failing = _extension(tmp_path, lambda r: httpx.Response(200, json={"value": []}),
                         FakeTokens(error=auth.GraphAuthError("AADSTS7000215: Invalid client secret provided.", (7000215,))))
    with pytest.raises(AgentConfigError, match="^The client secret is wrong or has expired. Create a new secret and try again.$"):
        failing.verify_agent_config(CONFIG)
    seen = []
    ok = _extension(tmp_path, lambda r: seen.append(r) or httpx.Response(200, json={"value": []}), FakeTokens())
    assert ok.verify_agent_config(CONFIG) == f"Connected to {MAILBOX}"
    assert seen[0].url.params["$top"] == "1"
    denied = _extension(tmp_path, lambda r: httpx.Response(403, json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}}), FakeTokens())
    with pytest.raises(AgentConfigError, match="^The app doesn't have permission to open this mailbox.$"):
        denied.verify_agent_config(CONFIG)


def test_the_operator_view_lists_short_ids_and_never_marks_read(tmp_path) -> None:
    from core.extensions.contract import AgentViewError

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"value": [_message("GRAPH-1")]})
        return httpx.Response(200, json={**_message("GRAPH-1"), "toRecipients": [], "body": {"content": "Body"}})

    extension = _extension(tmp_path, handler, FakeTokens())
    page = extension.agent_view("agent-1", skip=0, top=25)
    short = page.rows[0].id
    assert page.caption == f"Inbox of {MAILBOX}" and page.rows[0].emphasis is True
    assert page.rows[0].cells == {"subject": "Hello", "from": "Alice Doe <alice@x.com>", "received": "2026-09-29 09:14"}
    item = extension.agent_view_item("agent-1", short)
    assert item.title == "Hello" and item.body_text == "Body" and ("Status", "Unread") in item.facts
    assert all(request.method == "GET" for request in requests)
    with pytest.raises(AgentViewError) as caught:
        extension.agent_view_item("agent-1", "m00000000")
    assert caught.value.code == "UNKNOWN_MESSAGE_ID"
    unconfigured = _extension(tmp_path, handler, FakeTokens(), config=None)
    with pytest.raises(AgentViewError) as caught:
        unconfigured.agent_view("agent-1", skip=0, top=25)
    assert caught.value.code == "MAILBOX_NOT_CONFIGURED"
