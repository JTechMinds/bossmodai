"""Microsoft 365 Mailbox — agents send and read email through one M365 mailbox each.

Loaded by the BossMod extension host (``core.extensions``) only when the
extension is enabled. Each agent's mailbox (tenant, app, secret, address) is
per-agent config the host stores wrapped at rest and verifies here on save;
this module reads it only through ``ctx.read_agent_config``. New unread mail
wakes the agent through the host's wake service (``wake.py``).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Mapping

import httpx

from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.extensions.contract import (
    AgentConfigError,
    AgentViewColumn,
    AgentViewError,
    AgentViewItem,
    AgentViewPage,
    AgentViewRow,
    ExtensionContext,
    SetupError,
    SetupStatus,
    WakeBatch,
)
from core.models import Agent

from .auth import RestTokenProvider, TokenProvider
from .commands import NOT_CONFIGURED, MailCommands, Ms365MailDefaults, format_utc
from .contacts import ContactBook
from .graph import (
    CONFIG_FAILED,
    GraphAuthError,
    GraphHttpError,
    GraphMailbox,
    GraphUnreachable,
    Message,
    SentMessage,
    describe_graph_error,
    friendly_config_error,
)
from .ids import IdMap, IdMapError, UnknownMessageId
from .wake import MailWake, WakeStore

logger = logging.getLogger(__name__)

# The id map format changed to [short, folder, id]; old ids/ files are not read.
_IDS_DIRNAME = "message_ids"
_CONTACTS_DIRNAME = "contacts"
_WAKE_DIRNAME = "wake"
# Agent ids name files under the data dir, so they must stay plain.
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_VIEW_INBOX = "inbox"
_VIEW_SENT = "sent"
# The id map folder each operator view lists and opens.
_VIEW_FOLDERS = {_VIEW_INBOX: "inbox", _VIEW_SENT: "sentitems"}
_INBOX_COLUMNS = [
    AgentViewColumn(key="subject", label="Subject"),
    AgentViewColumn(key="from", label="From"),
    AgentViewColumn(key="received", label="Received (UTC)"),
]
_SENT_COLUMNS = [
    AgentViewColumn(key="subject", label="Subject"),
    AgentViewColumn(key="to", label="To"),
    AgentViewColumn(key="sent", label="Sent (UTC)"),
]
# The operator viewer's sentences (plan E3); the code stays technical.
VIEW_UNKNOWN_MESSAGE = "That message is no longer listed. Refresh the inbox."
VIEW_ID_MAP_UNREADABLE = "Couldn't read the saved message list. Refresh the inbox."


def create(ctx: ExtensionContext) -> "Ms365MailExtension":
    """The host's entry point: build the extension for this process.

    Raises:
        pydantic.ValidationError: The manifest's ``defaults`` are invalid.
    """
    return Ms365MailExtension(ctx)


class Ms365MailExtension:
    """The ``mail`` command namespace, per-agent config verification, the inbox/sent view and wake.

    Args:
        ctx: Manifest, data dir and the per-agent config reader from the host.
        transport: The HTTP transport; tests inject ``httpx.MockTransport``.
        tokens: Where tokens come from; defaults to ``RestTokenProvider``
            over the same client.

    Raises:
        pydantic.ValidationError: The manifest's ``defaults`` are invalid.
    """

    def __init__(
        self,
        ctx: ExtensionContext,
        *,
        transport: httpx.BaseTransport | None = None,
        tokens: TokenProvider | None = None,
    ) -> None:
        self._defaults = Ms365MailDefaults.model_validate(ctx.manifest.defaults)
        self._data_dir = ctx.data_dir
        self._read_config = ctx.read_agent_config
        # One client for the life of the instance, closed in shutdown().
        self._client = httpx.Client(timeout=self._defaults.request_timeout_s, transport=transport)
        self._tokens: TokenProvider = tokens if tokens is not None else RestTokenProvider(
            self._client,
            authority=self._defaults.authority,
            scope=self._defaults.graph_scope,
            refresh_margin_s=self._defaults.token_refresh_margin_s,
        )
        self._commands = MailCommands(
            mailbox_for=self._mailbox_for,
            id_map_for=self._id_map_for,
            contacts_for=self._contacts_for,
            defaults=self._defaults,
        )
        self._wake = MailWake(
            mailbox_for=self._mailbox_for,
            id_map_for=self._id_map_for,
            store_for=lambda agent_id: WakeStore(self._agent_file(_WAKE_DIRNAME, agent_id)),
            batch_max=self._defaults.wake_batch_max,
            preview_chars=self._defaults.preview_chars,
        )

    # ── host contract ─────────────────────────────────────────────────────

    def handle(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, body: str | None) -> BossModCliResult:
        """Run one ``mail`` command (see ``commands.MailCommands.handle``)."""
        return self._commands.handle(ctx, parsed, body)

    def setup_status(self) -> SetupStatus:
        """There is nothing to download."""
        return SetupStatus(state="not_required")

    def run_setup(self, log_path: Path) -> None:
        """Never called: the manifest declares no setup.

        Raises:
            SetupError: Always.
        """
        raise SetupError("no setup")

    def shutdown(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def prompt_state(self, agent: Agent) -> str | None:
        """``Your mailbox: <address>``, read from config (no network call).

        Raises:
            ValueError: The agent's stored config is corrupt.
        """
        config = self._read_config(agent.id)
        if config is None:
            return None
        return f"Your mailbox: {config['mailbox']}"

    def verify_agent_config(self, values: Mapping[str, str]) -> str:
        """Fetch a token, then read the inbox once.

        The technical detail of a failure (AADSTS text and codes, HTTP status,
        Graph code) is logged at warning level; the secret never appears in it.

        Returns:
            ``Connected to <mailbox>``.

        Raises:
            AgentConfigError: The token request or the inbox read failed; the
                message is a plain sentence from ``friendly_config_error``,
                shown to the operator as it is.
        """
        mailbox = self._mailbox(values)
        try:
            mailbox.list_inbox(top=1, skip=0, unread_only=False)
        except (GraphAuthError, GraphUnreachable, GraphHttpError) as exc:
            codes = f" (error_codes {list(exc.codes)})" if isinstance(exc, GraphAuthError) and exc.codes else ""
            logger.warning("Verifying mailbox %s failed: %s: %s%s", mailbox.mailbox, type(exc).__name__, exc, codes)
            raise AgentConfigError(friendly_config_error(exc)) from exc
        return f"Connected to {mailbox.mailbox}"

    def agent_view(self, agent_id: str, *, view: str, skip: int, top: int) -> AgentViewPage:
        """One page of the agent's inbox or Sent Items for the operator (read-only; nothing is marked read).

        Raises:
            AgentViewError: No mailbox, a Graph failure (a plain sentence), or
                an unreadable id map.
            ValueError: ``view`` is not ``inbox`` or ``sent`` (the host
                refuses any other first).
        """
        mailbox = self._view_mailbox(agent_id)
        try:
            if view == _VIEW_INBOX:
                return self._inbox_page(agent_id, mailbox, skip=skip, top=top)
            if view == _VIEW_SENT:
                return self._sent_page(agent_id, mailbox, skip=skip, top=top)
        except (GraphAuthError, GraphUnreachable, GraphHttpError) as exc:
            raise _view_graph_error(mailbox.mailbox, exc) from exc
        except IdMapError as exc:
            logger.warning("Mailbox %s view: id map unreadable: %s", mailbox.mailbox, exc)
            raise AgentViewError("ID_MAP_UNREADABLE", VIEW_ID_MAP_UNREADABLE) from exc
        raise ValueError(f"unknown view {view!r}")

    def _inbox_page(self, agent_id: str, mailbox: GraphMailbox, *, skip: int, top: int) -> AgentViewPage:
        page = mailbox.list_inbox(top=top, skip=skip, unread_only=False)
        shorts = self._id_map_for(agent_id).remember("inbox", (message.id for message in page.messages))
        rows = [
            AgentViewRow(
                id=short,
                cells={
                    "subject": message.subject or "(no subject)",
                    "from": message.sender.display() if message.sender else "(no sender)",
                    "received": format_utc(message.received),
                },
                emphasis=not message.is_read,
            )
            for short, message in zip(shorts, page.messages)
        ]
        return AgentViewPage(columns=_INBOX_COLUMNS, rows=rows, has_more=page.has_more, caption=f"Inbox of {mailbox.mailbox}")

    def _sent_page(self, agent_id: str, mailbox: GraphMailbox, *, skip: int, top: int) -> AgentViewPage:
        page = mailbox.list_sent(top=top, skip=skip)
        shorts = self._id_map_for(agent_id).remember("sentitems", (message.id for message in page.messages))
        rows = [
            AgentViewRow(
                id=short,
                cells={
                    "subject": message.subject or "(no subject)",
                    "to": ", ".join(a.display() for a in message.to) or "(none)",
                    "sent": format_utc(message.sent),
                },
            )
            for short, message in zip(shorts, page.messages)
        ]
        return AgentViewPage(columns=_SENT_COLUMNS, rows=rows, has_more=page.has_more, caption=f"Sent from {mailbox.mailbox}")

    def agent_view_item(self, agent_id: str, item_id: str, *, view: str) -> AgentViewItem:
        """One inbox or sent message in full for the operator (read-only; it is NOT
        marked read, so the agent's ``--unread`` queue is unchanged by the operator looking).

        Raises:
            AgentViewError: No mailbox, an unknown id (including an id the
                agent listed in another folder than ``view``), or a Graph
                failure (a plain sentence).
            ValueError: ``view`` is not ``inbox`` or ``sent``.
        """
        if view not in _VIEW_FOLDERS:
            raise ValueError(f"unknown view {view!r}")
        mailbox = self._view_mailbox(agent_id)
        try:
            ref = self._id_map_for(agent_id).resolve(item_id)
            if ref.folder != _VIEW_FOLDERS[view]:
                # e.g. an id the agent archived or found by search: not in this view.
                raise AgentViewError("UNKNOWN_MESSAGE_ID", VIEW_UNKNOWN_MESSAGE)
            if view == _VIEW_SENT:
                return _sent_item(mailbox.get_sent_message(ref.graph_id))
            return _inbox_item(mailbox.get_message(ref.folder, ref.graph_id))
        except UnknownMessageId as exc:
            raise AgentViewError("UNKNOWN_MESSAGE_ID", VIEW_UNKNOWN_MESSAGE) from exc
        except IdMapError as exc:
            logger.warning("Mailbox %s view: id map unreadable: %s", mailbox.mailbox, exc)
            raise AgentViewError("ID_MAP_UNREADABLE", VIEW_ID_MAP_UNREADABLE) from exc
        except (GraphAuthError, GraphUnreachable, GraphHttpError) as exc:
            raise _view_graph_error(mailbox.mailbox, exc) from exc

    # ── wake (host SupportsWake) ──────────────────────────────────────────

    def poll_wake(self, agent_id: str) -> WakeBatch | None:
        """New unread inbox mail since the agent's committed watermark (see ``wake.MailWake.poll``).

        Raises:
            GraphAuthError, GraphUnreachable, GraphHttpError, WakeNotConfigured,
            WakeStateError, IdMapError: the host records ``describe_wake_error``.
        """
        return self._wake.poll(agent_id)

    def commit_wake(self, batch: WakeBatch) -> None:
        """Persist the batch's watermark (see ``wake.MailWake.commit``)."""
        self._wake.commit(batch)

    def skip_wake(self, agent_id: str) -> None:
        """Start watching from now, with no Graph call (vacation)."""
        self._wake.skip(agent_id)

    def describe_wake_error(self, exc: Exception) -> str:
        """The desk's sentence: a Graph failure through ``friendly_config_error``, anything else ``CONFIG_FAILED``."""
        if isinstance(exc, (GraphAuthError, GraphUnreachable, GraphHttpError)):
            return friendly_config_error(exc)
        return CONFIG_FAILED

    # ── per-agent state ───────────────────────────────────────────────────

    def _mailbox(self, config: Mapping[str, str]) -> GraphMailbox:
        return GraphMailbox(config, graph_base=self._defaults.graph_base, tokens=self._tokens, client=self._client)

    def _mailbox_for(self, agent_id: str) -> GraphMailbox | None:
        config = self._read_config(agent_id)
        return None if config is None else self._mailbox(config)

    def _view_mailbox(self, agent_id: str) -> GraphMailbox:
        mailbox = self._mailbox_for(agent_id)
        if mailbox is None:
            code, message = NOT_CONFIGURED.split(": ", 1)
            raise AgentViewError(code, message)
        return mailbox

    def _id_map_for(self, agent_id: str) -> IdMap:
        return IdMap(self._agent_file(_IDS_DIRNAME, agent_id), self._defaults.id_map_keep)

    def _contacts_for(self, agent_id: str) -> ContactBook:
        return ContactBook(self._agent_file(_CONTACTS_DIRNAME, agent_id))

    def _agent_file(self, dirname: str, agent_id: str) -> Path:
        if not _AGENT_ID_RE.match(agent_id):
            raise ValueError(f"agent id {agent_id!r} cannot name a file")
        return self._data_dir / dirname / f"{agent_id}.json"


def _view_graph_error(mailbox: str, exc: GraphAuthError | GraphUnreachable | GraphHttpError) -> AgentViewError:
    """The operator's plain sentence; the technical detail goes to the log only."""
    code, detail = describe_graph_error(exc)
    logger.warning("Mailbox %s view failed: %s: %s", mailbox, code, detail)
    return AgentViewError(code, friendly_config_error(exc))


def _inbox_item(message: Message) -> AgentViewItem:
    facts = [
        ("From", message.sender.display() if message.sender else "(no sender)"),
        ("To", ", ".join(a.display() for a in message.to) or "(none)"),
    ]
    if message.cc:
        facts.append(("Cc", ", ".join(a.display() for a in message.cc)))
    facts += [
        ("Received", f"{format_utc(message.received)} UTC"),
        ("Status", "Read" if message.is_read else "Unread"),
        ("Attachments", "Yes" if message.has_attachments else "None"),
    ]
    return AgentViewItem(title=message.subject or "(no subject)", facts=facts, body_text=message.body)


def _sent_item(message: SentMessage) -> AgentViewItem:
    facts = [("To", ", ".join(a.display() for a in message.to) or "(none)")]
    if message.cc:
        facts.append(("Cc", ", ".join(a.display() for a in message.cc)))
    facts += [
        ("Sent", f"{format_utc(message.sent)} UTC"),
        ("Attachments", "Yes" if message.has_attachments else "None"),
    ]
    return AgentViewItem(title=message.subject or "(no subject)", facts=facts, body_text=message.body)
