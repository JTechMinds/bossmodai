"""Microsoft 365 Mailbox — agents send and read email through one M365 mailbox each.

Loaded by the BossMod extension host (``core.extensions``) only when the
extension is enabled. Each agent's mailbox (tenant, app, secret, address) is
per-agent config the host stores wrapped at rest and verifies here on save;
this module reads it only through ``ctx.read_agent_config``.
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
)
from core.models import Agent

from .auth import RestTokenProvider, TokenProvider
from .commands import NOT_CONFIGURED, MailCommands, Ms365MailDefaults, format_utc
from .contacts import ContactBook
from .graph import (
    GraphAuthError,
    GraphHttpError,
    GraphMailbox,
    GraphUnreachable,
    describe_graph_error,
    friendly_config_error,
)
from .ids import IdMap, IdMapError, UnknownMessageId

logger = logging.getLogger(__name__)

_IDS_DIRNAME = "ids"
_CONTACTS_DIRNAME = "contacts"
# Agent ids name files under the data dir, so they must stay plain.
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_VIEW_COLUMNS = [
    AgentViewColumn(key="subject", label="Subject"),
    AgentViewColumn(key="from", label="From"),
    AgentViewColumn(key="received", label="Received (UTC)"),
]


def create(ctx: ExtensionContext) -> "Ms365MailExtension":
    """The host's entry point: build the extension for this process.

    Raises:
        pydantic.ValidationError: The manifest's ``defaults`` are invalid.
    """
    return Ms365MailExtension(ctx)


class Ms365MailExtension:
    """The ``mail`` command namespace, per-agent config verification and the inbox view.

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

    def agent_view(self, agent_id: str, *, skip: int, top: int) -> AgentViewPage:
        """One page of the agent's inbox for the operator (read-only; nothing is marked read).

        Raises:
            AgentViewError: No mailbox, a Graph failure, or an unreadable id map.
        """
        mailbox = self._view_mailbox(agent_id)
        try:
            page = mailbox.list_inbox(top=top, skip=skip, unread_only=False)
            shorts = self._id_map_for(agent_id).remember(message.id for message in page.messages)
        except (GraphAuthError, GraphUnreachable, GraphHttpError) as exc:
            raise AgentViewError(*describe_graph_error(exc)) from exc
        except IdMapError as exc:
            raise AgentViewError("ID_MAP_UNREADABLE", str(exc)) from exc
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
        return AgentViewPage(
            columns=_VIEW_COLUMNS,
            rows=rows,
            has_more=page.has_more,
            caption=f"Inbox of {mailbox.mailbox}",
        )

    def agent_view_item(self, agent_id: str, item_id: str) -> AgentViewItem:
        """One message in full for the operator (read-only; it is NOT marked read,
        so the agent's ``--unread`` queue is unchanged by the operator looking).

        Raises:
            AgentViewError: No mailbox, an unknown id, or a Graph failure.
        """
        mailbox = self._view_mailbox(agent_id)
        try:
            message = mailbox.get_message(self._id_map_for(agent_id).resolve(item_id))
        except UnknownMessageId as exc:
            raise AgentViewError("UNKNOWN_MESSAGE_ID", f"{exc} is not a listed message; refresh the inbox") from exc
        except IdMapError as exc:
            raise AgentViewError("ID_MAP_UNREADABLE", str(exc)) from exc
        except (GraphAuthError, GraphUnreachable, GraphHttpError) as exc:
            raise AgentViewError(*describe_graph_error(exc)) from exc
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
