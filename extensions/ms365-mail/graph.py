"""Microsoft 365 Mailbox — the fixed-path Microsoft Graph client for one mailbox.

Scope is enforced by construction (plan D3): there is no generic request
method. Every public method passes a literal suffix to ``_call``, the URL is
``graph_base + /users/{mailbox} + suffix``, message ids are quoted with no
safe characters, and ``_call`` refuses any URL outside this mailbox's root.
Message paths come only from ``_messages_path`` over a closed folder set
(``ids.Folder``): ``/mailFolders/{inbox,archive,sentitems}/messages``. The
other endpoints are the folder counts read ``/mailFolders/{folder}``, the
move ``/mailFolders/inbox/messages/{id}/move`` (inbox → archive only) and
``/sendMail``. ``@odata.nextLink`` is never followed: paging is ``$skip``,
and search does not page at all.

BossMod limits itself to this mailbox; the client secret itself is
tenant-wide and can reach other mailboxes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlencode

import httpx

from .auth import AccessToken, GraphAuthError, GraphUnreachable, TokenProvider
from .ids import FOLDERS, Folder

# Folder names are Graph's well-known names (mailFolder resource docs,
# "Well-known folder names"); they work whatever the mailbox's locale.
_SENT_SUMMARY_FIELDS = "id,subject,toRecipients,sentDateTime,bodyPreview,hasAttachments"
_SENT_MESSAGE_FIELDS = "id,subject,from,toRecipients,ccRecipients,sentDateTime,hasAttachments,body"
_SUMMARY_FIELDS = "id,subject,from,receivedDateTime,isRead,bodyPreview,hasAttachments"
_MESSAGE_FIELDS = "id,subject,from,toRecipients,ccRecipients,receivedDateTime,isRead,hasAttachments,body"
# Graph refuses $filter + $orderby unless the $orderby property also leads
# the $filter (InefficientFilter). This bound is always true, so it only
# satisfies that rule; see the List messages docs, "Using filter and orderby".
_UNREAD_FILTER = "receivedDateTime ge 1900-01-01T00:00:00Z and isRead eq false"
_TEXT_BODY = 'outlook.body-content-type="text"'

__all__ = [
    "Address",
    "FolderCounts",
    "GraphAuthError",
    "GraphHttpError",
    "GraphMailbox",
    "GraphScopeError",
    "GraphUnreachable",
    "InboxPage",
    "Message",
    "MessageSummary",
    "SentMessage",
    "SentPage",
    "SentSummary",
    "describe_graph_error",
    "friendly_config_error",
]


class GraphHttpError(Exception):
    """Graph answered with an error status, or with a body this client cannot read.

    Attributes:
        status: The HTTP status.
        code: Graph's ``error.code`` (or a local code for an unreadable body).
        message: Graph's ``error.message``.
        retry_after: Seconds from ``Retry-After`` on 429/503, else ``None``.
    """

    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None) -> None:
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


class GraphScopeError(Exception):
    """A request URL left this mailbox's root. A bug in this module, never user input."""


@dataclass(frozen=True)
class Address:
    """One mail address with its display name (may be empty)."""

    name: str
    address: str

    def display(self) -> str:
        """``Name <address>``, or the bare address when there is no name."""
        return f"{self.name} <{self.address}>" if self.name and self.name != self.address else self.address


@dataclass(frozen=True)
class FolderCounts:
    """A folder's message totals, as Graph reports them."""

    total: int
    unread: int


@dataclass(frozen=True)
class MessageSummary:
    """One inbox or archive row."""

    id: str
    subject: str
    sender: Address | None
    received: datetime
    is_read: bool
    preview: str
    has_attachments: bool


@dataclass(frozen=True)
class InboxPage:
    """One page of the inbox, newest first; ``has_more`` when Graph offers a next page."""

    messages: list[MessageSummary]
    has_more: bool


@dataclass(frozen=True)
class SentSummary:
    """One Sent Items row."""

    id: str
    subject: str
    to: list[Address]
    sent: datetime
    preview: str
    has_attachments: bool


@dataclass(frozen=True)
class SentPage:
    """One page of Sent Items, newest first; ``has_more`` when Graph offers a next page."""

    messages: list[SentSummary]
    has_more: bool


@dataclass(frozen=True)
class SentMessage:
    """One sent message with its plain-text body."""

    id: str
    subject: str
    sender: Address | None
    to: list[Address]
    cc: list[Address]
    sent: datetime
    has_attachments: bool
    body: str


@dataclass(frozen=True)
class Message:
    """One message with its plain-text body."""

    id: str
    subject: str
    sender: Address | None
    to: list[Address]
    cc: list[Address]
    received: datetime
    is_read: bool
    has_attachments: bool
    body: str


class GraphMailbox:
    """Inbox read and send for exactly one mailbox.

    Args:
        config: The agent's stored values: ``tenant_id``, ``client_id``,
            ``client_secret``, ``mailbox``.
        graph_base: e.g. ``https://graph.microsoft.com/v1.0`` (manifest default).
        tokens: Where tokens come from.
        client: The shared HTTP client (timeout, transport).

    Raises:
        KeyError: A config key is missing (the host validates before storing).
    """

    def __init__(
        self,
        config: Mapping[str, str],
        *,
        graph_base: str,
        tokens: TokenProvider,
        client: httpx.Client,
    ) -> None:
        self.mailbox = config["mailbox"]
        self._tenant_id = config["tenant_id"]
        self._client_id = config["client_id"]
        self._secret = config["client_secret"]
        self._tokens = tokens
        self._client = client
        self._root = f"{graph_base.rstrip('/')}/users/{quote(self.mailbox, safe='@')}"
        self._root_sent = str(httpx.URL(self._root))

    def token(self) -> AccessToken:
        """Return a token for this mailbox's app registration.

        Raises:
            GraphAuthError: Azure refused the credentials.
            GraphUnreachable: The token endpoint could not be reached.
        """
        return self._tokens.acquire(self._tenant_id, self._client_id, self._secret)

    def list_inbox(self, top: int, skip: int, unread_only: bool) -> InboxPage:
        """Return one page of the inbox, newest first.

        Args:
            top: Page size (the caller bounds it).
            skip: Messages to skip.
            unread_only: Only unread messages.

        Raises:
            GraphAuthError, GraphUnreachable, GraphHttpError.
        """
        params: dict[str, str] = {
            "$select": _SUMMARY_FIELDS,
            "$orderby": "receivedDateTime desc",
            "$top": str(top),
            "$skip": str(skip),
        }
        if unread_only:
            params["$filter"] = _UNREAD_FILTER
        payload = self._json(self._call("GET", _messages_path("inbox"), params=params))
        items = payload.get("value")
        if not isinstance(items, list):
            raise _unreadable("the inbox listing has no value array")
        # Read to learn whether a next page exists; never followed (D3).
        has_more = isinstance(payload.get("@odata.nextLink"), str)
        return InboxPage(messages=[_summary(item) for item in items], has_more=has_more)

    def list_new_unread(self, since: datetime, top: int) -> InboxPage:
        """Return unread inbox messages received at or after ``since``, oldest first.

        ``receivedDateTime`` leads the filter, which also satisfies Graph's
        rule that an ``$orderby`` property lead the ``$filter``. Graph does not
        document the precision of ``ge`` against its stored timestamps, so the
        caller keeps the ids seen at ``since`` and drops them itself.

        Args:
            since: The earliest ``receivedDateTime`` (timezone-aware).
            top: Page size (the caller bounds it).

        Raises:
            ValueError: ``since`` is naive.
            GraphAuthError, GraphUnreachable, GraphHttpError.
        """
        if since.tzinfo is None:
            raise ValueError("since must be timezone-aware")
        stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$select": _SUMMARY_FIELDS,
            "$filter": f"receivedDateTime ge {stamp} and isRead eq false",
            "$orderby": "receivedDateTime asc",
            "$top": str(top),
        }
        payload = self._json(self._call("GET", _messages_path("inbox"), params=params))
        items = payload.get("value")
        if not isinstance(items, list):
            raise _unreadable("the inbox listing has no value array")
        has_more = isinstance(payload.get("@odata.nextLink"), str)
        return InboxPage(messages=[_summary(item) for item in items], has_more=has_more)

    def folder_counts(self, folder: Folder) -> FolderCounts:
        """Return a folder's total and unread message counts.

        ``GET /mailFolders/{folder}`` with ``totalItemCount`` and
        ``unreadItemCount``, Graph's documented way to count a folder.

        Raises:
            GraphScopeError: ``folder`` is not in the closed set.
            GraphHttpError: Graph refused, or a count is missing or not an
                integer (``UnreadableResponse``).
            GraphAuthError, GraphUnreachable.
        """
        _check_folder(folder)
        payload = self._json(self._call(
            "GET", f"/mailFolders/{folder}", params={"$select": "totalItemCount,unreadItemCount"},
        ))
        return FolderCounts(
            total=_required_count(payload, "totalItemCount", folder),
            unread=_required_count(payload, "unreadItemCount", folder),
        )

    def search(self, folder: Folder, query: str, top: int) -> list[MessageSummary]:
        """Return up to ``top`` messages in ``folder`` matching ``query``.

        Graph's ``$search`` matches from, subject and body by default and
        accepts KQL properties (``from:``, ``subject:``…) inside the quoted
        string. Results come in Graph's documented order (by sent date, newest
        first). ``$skip`` and ``$orderby`` are not sent: their support together
        with ``$search`` on messages is not documented, so search does not page.

        Args:
            folder: ``inbox`` or ``archive``.
            query: The search text as the agent wrote it.
            top: Most results (the caller bounds it).

        Raises:
            GraphScopeError: ``folder`` is not ``inbox`` or ``archive``.
            GraphAuthError, GraphUnreachable, GraphHttpError (404 when the
            mailbox has no such folder).
        """
        if folder not in ("inbox", "archive"):
            raise GraphScopeError(f"search covers inbox and archive only, not {folder!r}")
        params = {
            "$search": _search_clause(query),
            "$top": str(top),
            "$select": _SUMMARY_FIELDS,
        }
        payload = self._json(self._call("GET", _messages_path(folder), params=params))
        items = payload.get("value")
        if not isinstance(items, list):
            raise _unreadable(f"the {folder} search has no value array")
        return [_summary(item) for item in items]

    def move_to_archive(self, message_id: str) -> str:
        """Move one inbox message to the Archive folder and return its new Graph id.

        Graph's move creates a copy in the destination and removes the
        original, so the old id stops working.

        Raises:
            GraphHttpError: Graph refused (404 when the message or the
                Archive folder is missing), or the response has no id
                (``UnreadableResponse``).
            GraphAuthError, GraphUnreachable.
        """
        response = self._call(
            "POST",
            f"{_messages_path('inbox')}/{_quoted_id(message_id)}/move",
            json={"destinationId": "archive"},
        )
        payload = self._json(response)
        new_id = payload.get("id")
        if not isinstance(new_id, str) or not new_id:
            raise _unreadable("the moved message has no id")
        return new_id

    def list_sent(self, top: int, skip: int) -> SentPage:
        """Return one page of Sent Items, newest first.

        Raises:
            GraphAuthError, GraphUnreachable, GraphHttpError.
        """
        params = {
            "$select": _SENT_SUMMARY_FIELDS,
            "$orderby": "sentDateTime desc",
            "$top": str(top),
            "$skip": str(skip),
        }
        payload = self._json(self._call("GET", _messages_path("sentitems"), params=params))
        items = payload.get("value")
        if not isinstance(items, list):
            raise _unreadable("the sent listing has no value array")
        has_more = isinstance(payload.get("@odata.nextLink"), str)
        return SentPage(messages=[_sent_summary(item) for item in items], has_more=has_more)

    def get_sent_message(self, message_id: str) -> SentMessage:
        """Return one Sent Items message with its body as plain text.

        Raises:
            GraphAuthError, GraphUnreachable, GraphHttpError (404 when it is
            not in Sent Items).
        """
        response = self._call(
            "GET",
            f"{_messages_path('sentitems')}/{_quoted_id(message_id)}",
            params={"$select": _SENT_MESSAGE_FIELDS},
            headers={"Prefer": _TEXT_BODY},
        )
        return _sent_message(self._json(response))

    def get_message(self, folder: Folder, message_id: str) -> Message:
        """Return one inbox or archive message with its body as plain text.

        Raises:
            GraphScopeError: ``folder`` is not ``inbox`` or ``archive``.
            GraphAuthError, GraphUnreachable, GraphHttpError (404 when it is
            not in that folder).
        """
        response = self._call(
            "GET",
            f"{_received_path(folder)}/{_quoted_id(message_id)}",
            params={"$select": _MESSAGE_FIELDS},
            headers={"Prefer": _TEXT_BODY},
        )
        return _message(self._json(response))

    def mark_read(self, folder: Folder, message_id: str) -> None:
        """Mark one inbox or archive message read.

        Raises:
            GraphScopeError: ``folder`` is not ``inbox`` or ``archive``.
            GraphAuthError, GraphUnreachable, GraphHttpError.
        """
        self._call("PATCH", f"{_received_path(folder)}/{_quoted_id(message_id)}", json={"isRead": True})

    def send(self, to: Sequence[Address], cc: Sequence[Address], subject: str, body_html: str) -> None:
        """Send a new HTML message from this mailbox (saved to Sent Items).

        Args:
            body_html: The body, already rendered (``formatting.render_body``).

        Raises:
            GraphAuthError, GraphUnreachable, GraphHttpError.
        """
        self._call("POST", "/sendMail", json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body_html},
                "toRecipients": [_recipient(item) for item in to],
                "ccRecipients": [_recipient(item) for item in cc],
            },
            "saveToSentItems": True,
        })

    def reply(self, folder: Folder, message_id: str, body_html: str, reply_all: bool) -> None:
        """Reply to the sender (or everyone) of one inbox or archive message; ``body_html`` is the comment.

        ``body_html`` is the rendered HTML from ``formatting.render_body``.
        Graph places the comment above the quoted original and renders it as
        HTML (as Outlook shows it). ``message.body`` is not used: Graph
        documents ``comment`` or ``message.body`` (not both), and a
        ``message.body`` replaces the whole body, dropping the quoted thread.

        Raises:
            GraphScopeError: ``folder`` is not ``inbox`` or ``archive``.
            GraphAuthError, GraphUnreachable, GraphHttpError.
        """
        action = "replyAll" if reply_all else "reply"
        self._call(
            "POST", f"{_received_path(folder)}/{_quoted_id(message_id)}/{action}", json={"comment": body_html}
        )

    def _call(
        self,
        method: str,
        suffix: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = self._root + suffix
        # Checked on the URL as httpx will send it (dot segments resolved).
        target = str(httpx.URL(url))
        if not target.startswith(self._root_sent + "/"):
            raise GraphScopeError(f"refusing a request outside {self._root}: {suffix!r}")
        if params:
            # Spaces as %20 rather than httpx's "+": the OData $filter form
            # Graph documents.
            url = f"{url}?{urlencode(params, quote_via=quote)}"
        token = self.token()
        request_headers = {"Authorization": f"Bearer {token.token}", **(headers or {})}
        try:
            response = self._client.request(method, url, json=json, headers=request_headers)
        except httpx.HTTPError as exc:
            raise GraphUnreachable(str(exc) or type(exc).__name__) from exc
        if response.is_success:
            return response
        raise _http_error(response)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise _unreadable("the response is not JSON") from exc
        if not isinstance(payload, dict):
            raise _unreadable("the response is not a JSON object")
        return payload


def describe_graph_error(exc: GraphAuthError | GraphUnreachable | GraphHttpError) -> tuple[str, str]:
    """Map a Graph failure to the agent-facing code and message (plan §4.2.5).

    Returns:
        ``(code, message)``, e.g. ``("GRAPH_THROTTLED", "retry after 30s")``.
    """
    if isinstance(exc, GraphAuthError):
        return "MAILBOX_AUTH_FAILED", str(exc)
    if isinstance(exc, GraphUnreachable):
        return "GRAPH_UNREACHABLE", str(exc)
    if exc.status in (401, 403):
        return "MAILBOX_ACCESS_DENIED", exc.message
    if exc.status == 404:
        return "MESSAGE_NOT_FOUND", 'that message is not in the inbox any more — run "mail inbox"'
    if exc.status in (429, 503):
        wait = f"retry after {exc.retry_after}s" if exc.retry_after is not None else "retry later (no Retry-After given)"
        return "GRAPH_THROTTLED", wait
    return "GRAPH_ERROR", f"HTTP {exc.status} {exc.code}: {exc.message}"


# AADSTS numbers from Microsoft's Entra error-code reference
# (learn.microsoft.com/entra/identity-platform/reference-error-codes) and, for
# 900023, Microsoft's error lookup (login.microsoftonline.com/error?code=900023).
_TENANT_CODES = frozenset({90002, 900023})  # InvalidTenantName; tenant identifier neither DNS name nor domain
_APP_CODES = frozenset({700016})  # UnauthorizedClient_DoesNotMatchRequest: app not found in the tenant
_SECRET_CODES = frozenset({7000215, 7000222})  # invalid client secret; expired client secret keys

CONFIG_TENANT_NOT_FOUND = "We couldn't find that Microsoft 365 tenant. Check the Tenant ID."
CONFIG_APP_NOT_FOUND = "We couldn't find that app in your tenant. Check the Client ID."
CONFIG_BAD_SECRET = "The client secret is wrong or has expired. Create a new secret and try again."
CONFIG_ACCESS_DENIED = "The app doesn't have permission to open this mailbox."
CONFIG_MAILBOX_NOT_FOUND = "We couldn't find that mailbox. Check the address."
CONFIG_UNREACHABLE = "Couldn't reach Microsoft 365. Check your internet connection and try again."
CONFIG_FAILED = "Access to the mailbox failed. Check the details and try again."


def friendly_config_error(exc: GraphAuthError | GraphUnreachable | GraphHttpError) -> str:
    """Map a failure while verifying a mailbox on save to one plain sentence for the operator.

    Pure: the sentence never contains Azure's or Graph's text, so it cannot
    carry the secret or a raw error. Token failures are matched on Azure's
    numeric ``error_codes``, never on the description; the inbox read is
    matched on the HTTP status (Graph's documented 401/403/404 meanings).
    Graph's mailbox codes (``ErrorInvalidUser``, ``MailboxNotEnabledForRESTAPI``)
    are not in Graph's error documentation, so they are not matched.

    Args:
        exc: The token or inbox-read failure.

    Returns:
        One of the ``CONFIG_*`` sentences; ``CONFIG_FAILED`` for anything
        without a specific sentence (other AADSTS codes, 429/5xx, an
        unreadable response).
    """
    if isinstance(exc, GraphUnreachable):
        return CONFIG_UNREACHABLE
    if isinstance(exc, GraphAuthError):
        codes = set(exc.codes)
        if codes & _TENANT_CODES:
            return CONFIG_TENANT_NOT_FOUND
        if codes & _APP_CODES:
            return CONFIG_APP_NOT_FOUND
        if codes & _SECRET_CODES:
            return CONFIG_BAD_SECRET
        return CONFIG_FAILED
    if exc.status in (401, 403):
        return CONFIG_ACCESS_DENIED
    if exc.status == 404:
        return CONFIG_MAILBOX_NOT_FOUND
    return CONFIG_FAILED


def _check_folder(folder: str) -> None:
    """Raises ``GraphScopeError`` unless ``folder`` is in the closed set (a caller bug, never input)."""
    if folder not in FOLDERS:
        raise GraphScopeError(f"refusing folder {folder!r}")


def _messages_path(folder: Folder) -> str:
    """``/mailFolders/{folder}/messages`` for a folder in the closed set (D3).

    Raises:
        GraphScopeError: Any other folder.
    """
    _check_folder(folder)
    return f"/mailFolders/{folder}/messages"


def _received_path(folder: Folder) -> str:
    """The messages path of a folder holding received mail (``inbox`` or ``archive``).

    Raises:
        GraphScopeError: Any other folder; sent mail has its own methods.
    """
    if folder not in ("inbox", "archive"):
        raise GraphScopeError(f"refusing folder {folder!r} for a received message")
    return _messages_path(folder)


def _search_clause(query: str) -> str:
    """The ``$search`` value: the query in double quotes, ``\\`` and ``"`` backslash-escaped (pure)."""
    escaped = query.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _required_count(payload: dict[str, Any], key: str, folder: str) -> int:
    value = payload.get(key)
    # bool is an int subclass; a JSON true is not a count.
    if not isinstance(value, int) or isinstance(value, bool):
        raise _unreadable(f"the {folder} folder has no {key}")
    return value


def _quoted_id(message_id: str) -> str:
    """A message id as one path segment: no separator survives quoting, and
    ``.``/``..`` (which a URL resolves as dot segments) are refused.

    Raises:
        GraphScopeError: The id is empty, ``.`` or ``..``.
    """
    if message_id in {"", ".", ".."}:
        raise GraphScopeError(f"refusing message id {message_id!r}")
    return quote(message_id, safe="")


def _http_error(response: httpx.Response) -> GraphHttpError:
    code, message = "UnknownError", f"HTTP {response.status_code} with no error body"
    try:
        payload = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or code)
        message = str(error.get("message") or message)
    retry_after = None
    raw = response.headers.get("Retry-After")
    if response.status_code in (429, 503) and raw is not None and raw.strip().isdigit():
        retry_after = int(raw.strip())
    return GraphHttpError(response.status_code, code, message, retry_after)


def _unreadable(detail: str) -> GraphHttpError:
    return GraphHttpError(200, "UnreadableResponse", detail)


def _recipient(address: Address) -> dict[str, Any]:
    email: dict[str, str] = {"address": address.address}
    if address.name:
        email["name"] = address.name
    return {"emailAddress": email}


def _address(raw: Any) -> Address | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("emailAddress"), dict):
        return None
    email = raw["emailAddress"]
    address = email.get("address")
    if not isinstance(address, str) or not address:
        return None
    name = email.get("name")
    return Address(name=name if isinstance(name, str) else "", address=address)


def _addresses(raw: Any) -> list[Address]:
    if not isinstance(raw, list):
        return []
    return [item for item in (_address(entry) for entry in raw) if item is not None]


def _required_str(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise _unreadable(f"a message has no {key}")
    return value


def _received(item: dict[str, Any]) -> datetime:
    raw = _required_str(item, "receivedDateTime")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _unreadable(f"a message has an unreadable receivedDateTime {raw!r}") from exc


def _summary(item: Any) -> MessageSummary:
    if not isinstance(item, dict):
        raise _unreadable("an inbox entry is not an object")
    return MessageSummary(
        id=_required_str(item, "id"),
        # A message can have no subject or no preview; those are real values.
        subject=item.get("subject") or "",
        sender=_address(item.get("from")),
        received=_received(item),
        is_read=bool(item.get("isRead")),
        preview=item.get("bodyPreview") or "",
        has_attachments=bool(item.get("hasAttachments")),
    )


def _sent_at(item: dict[str, Any]) -> datetime:
    raw = _required_str(item, "sentDateTime")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _unreadable(f"a message has an unreadable sentDateTime {raw!r}") from exc


def _sent_summary(item: Any) -> SentSummary:
    if not isinstance(item, dict):
        raise _unreadable("a sent entry is not an object")
    return SentSummary(
        id=_required_str(item, "id"),
        subject=item.get("subject") or "",
        to=_addresses(item.get("toRecipients")),
        sent=_sent_at(item),
        preview=item.get("bodyPreview") or "",
        has_attachments=bool(item.get("hasAttachments")),
    )


def _sent_message(item: dict[str, Any]) -> SentMessage:
    body = item.get("body")
    content = body.get("content") if isinstance(body, dict) else None
    return SentMessage(
        id=_required_str(item, "id"),
        subject=item.get("subject") or "",
        sender=_address(item.get("from")),
        to=_addresses(item.get("toRecipients")),
        cc=_addresses(item.get("ccRecipients")),
        sent=_sent_at(item),
        has_attachments=bool(item.get("hasAttachments")),
        body=content if isinstance(content, str) else "",
    )


def _message(item: dict[str, Any]) -> Message:
    body = item.get("body")
    content = body.get("content") if isinstance(body, dict) else None
    return Message(
        id=_required_str(item, "id"),
        subject=item.get("subject") or "",
        sender=_address(item.get("from")),
        to=_addresses(item.get("toRecipients")),
        cc=_addresses(item.get("ccRecipients")),
        received=_received(item),
        is_read=bool(item.get("isRead")),
        has_attachments=bool(item.get("hasAttachments")),
        body=content if isinstance(content, str) else "",
    )
