"""Microsoft 365 Mailbox — the ``mail`` subcommands.

``mail <verb> [args]``, free text in the body (the ``pref``/``write``
convention): ``inbox``, ``read``, ``send``, ``reply`` and ``contacts``.
Every failure is an explicit error result naming a code; nothing is retried
or guessed. Recipients are resolved (addresses, or saved contact names)
before any Graph call, and nothing is sent unless every one resolves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand

from .contacts import Contact, ContactBook, ContactBookError, ContactError
from .formatting import render_body
from .graph import (
    Address,
    GraphAuthError,
    GraphHttpError,
    GraphUnreachable,
    InboxPage,
    Message,
    describe_graph_error,
)
from .ids import IdMap, IdMapError, UnknownMessageId

KIND = "mail"
_USAGE = 'USAGE: mail inbox|read|send|reply|contacts — run "learn mail" for details'
_INBOX_USAGE = "mail inbox [--unread] [--limit N] [--skip N]"
_SEND_USAGE = "mail send <to>[,<to>…] [--cc <a>[,<b>…]] --subject <text>   (message in the body)"
_REPLY_USAGE = "mail reply <id> [--all]   (reply in the body)"
_CONTACTS_USAGE = "mail contacts [list] | mail contacts add <addr>[,<addr>…] [--name <text>] | mail contacts remove <addr>[,<addr>…]"
NOT_CONFIGURED = "MAILBOX_NOT_CONFIGURED: no mailbox is set up for you; ask the operator to configure one at your desk"


class Ms365MailDefaults(BaseModel):
    """The manifest's ``defaults`` block, validated when the extension starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_base: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    graph_scope: str = Field(min_length=1)
    request_timeout_s: float = Field(gt=0)
    token_refresh_margin_s: int = Field(ge=0)
    inbox_default_limit: int = Field(ge=1)
    inbox_max_limit: int = Field(ge=1)
    id_map_keep: int = Field(ge=1)
    preview_chars: int = Field(ge=1)
    # Most new messages one wake check delivers; the rest follow next check.
    wake_batch_max: int = Field(ge=1)

    @model_validator(mode="after")
    def _default_within_max(self) -> "Ms365MailDefaults":
        if self.inbox_default_limit > self.inbox_max_limit:
            raise ValueError("inbox_default_limit must not exceed inbox_max_limit")
        return self


class MailboxLike(Protocol):
    """What the commands need from ``graph.GraphMailbox`` (a fake in tests)."""

    mailbox: str

    def list_inbox(self, top: int, skip: int, unread_only: bool) -> InboxPage: ...
    def get_message(self, message_id: str) -> Message: ...
    def mark_read(self, message_id: str) -> None: ...
    def send(self, to: Sequence[Address], cc: Sequence[Address], subject: str, body_html: str) -> None: ...
    def reply(self, message_id: str, body: str, reply_all: bool) -> None: ...


class CommandError(ValueError):
    """A usage or precondition error; the message goes to the agent as is."""


@dataclass(frozen=True)
class _Args:
    positional: list[str]
    values: dict[str, str]
    switches: set[str]


class MailCommands:
    """Runs ``mail`` for any number of agents.

    Args:
        mailbox_for: The agent's mailbox, or ``None`` when it has no stored config.
        id_map_for: The agent's short-id map.
        contacts_for: The agent's contact book.
        defaults: Validated manifest defaults.
    """

    def __init__(
        self,
        *,
        mailbox_for: Callable[[str], MailboxLike | None],
        id_map_for: Callable[[str], IdMap],
        contacts_for: Callable[[str], ContactBook],
        defaults: Ms365MailDefaults,
    ) -> None:
        self._mailbox_for = mailbox_for
        self._id_map_for = id_map_for
        self._contacts_for = contacts_for
        self._defaults = defaults

    def handle(self, ctx: CliExecutionContext, parsed: ParsedCliCommand, body: str | None) -> BossModCliResult:
        """Run one ``mail`` command for ``ctx.agent``.

        ``contacts`` works without a mailbox (it is BossMod-side only); every
        other verb needs the agent's stored config (``MAILBOX_NOT_CONFIGURED``).

        Raises:
            graph.GraphScopeError: A bug in the Graph client, never input;
                it is not turned into an agent-facing result.
        """
        if not parsed.args:
            return self._error(ctx, parsed, _USAGE)
        verb, args = parsed.args[0].lower(), parsed.args[1:]
        agent_id = ctx.agent.id
        try:
            if verb == "contacts":
                return self._contacts(ctx, parsed, args, agent_id)
            runner = {
                "inbox": self._inbox,
                "read": self._read,
                "send": self._send,
                "reply": self._reply,
            }.get(verb)
            if runner is None:
                return self._error(ctx, parsed, _USAGE)
            mailbox = self._mailbox_for(agent_id)
            if mailbox is None:
                return self._error(ctx, parsed, NOT_CONFIGURED)
            return runner(ctx, parsed, args, body, mailbox)
        except CommandError as exc:
            return self._error(ctx, parsed, str(exc))
        except ContactError as exc:
            return self._error(ctx, parsed, str(exc))
        except UnknownMessageId as exc:
            return self._error(ctx, parsed, f'UNKNOWN_MESSAGE_ID: {exc} — run "mail inbox" to list current ids')
        except IdMapError as exc:
            return self._error(ctx, parsed, f"ID_MAP_UNREADABLE: {exc}")
        except ContactBookError as exc:
            return self._error(ctx, parsed, f"CONTACTS_FILE_UNREADABLE: {exc}")
        except (GraphAuthError, GraphUnreachable, GraphHttpError) as exc:
            code, message = describe_graph_error(exc)
            return self._error(ctx, parsed, f"{code}: {message}")

    # ── verbs ──────────────────────────────────────────────────────────────

    def _inbox(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        opts = _parse(args, valued={"--limit", "--skip"}, switches={"--unread"}, usage=_INBOX_USAGE)
        if opts.positional:
            raise CommandError(f"USAGE: {_INBOX_USAGE} (unexpected {opts.positional[0]!r})")
        limit = _int_flag(opts.values.get("--limit"), "--limit", self._defaults.inbox_default_limit)
        if not 1 <= limit <= self._defaults.inbox_max_limit:
            raise CommandError(f"LIMIT_OUT_OF_RANGE: --limit must be 1–{self._defaults.inbox_max_limit}, got {limit}")
        skip = _int_flag(opts.values.get("--skip"), "--skip", 0)
        if skip < 0:
            raise CommandError(f"SKIP_OUT_OF_RANGE: --skip must be 0 or more, got {skip}")
        unread = "--unread" in opts.switches
        page = mailbox.list_inbox(top=limit, skip=skip, unread_only=unread)
        shorts = self._id_map_for(ctx.agent.id).remember(message.id for message in page.messages)
        scope = "unread messages" if unread else "messages"
        if not page.messages:
            header = f"Inbox of {mailbox.mailbox}: no {scope}" + (f" after the first {skip}" if skip else "") + "."
            lines = [header]
        else:
            first, last = skip + 1, skip + len(page.messages)
            lines = [f"Inbox of {mailbox.mailbox} — {scope} {first}–{last}, newest first (times UTC; ● unread)"]
            lines += [self._inbox_line(short, message) for short, message in zip(shorts, page.messages)]
            if page.has_more:
                more = f"mail inbox{' --unread' if unread else ''} --limit {limit} --skip {last}"
                lines.append(f"More: {more}")
        return success_result(
            command=parsed.raw,
            detail=f"mail: {len(page.messages)} {scope}",
            kind=KIND,
            data={"mailbox": mailbox.mailbox, "count": len(page.messages), "has_more": page.has_more},
            sections=[("INBOX", lines)],
            cwd=ctx.cwd,
        )

    def _inbox_line(self, short: str, message) -> str:
        mark = "○" if message.is_read else "●"
        sender = message.sender.display() if message.sender else "(no sender)"
        subject = message.subject or "(no subject)"
        preview = _clip(" ".join(message.preview.split()), self._defaults.preview_chars)
        return f"[{short}] {mark} {format_utc(message.received)}  {sender}  {subject} — \"{preview}\""

    def _read(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        if len(args) != 1:
            raise CommandError("USAGE: mail read <id>")
        graph_id = self._id_map_for(ctx.agent.id).resolve(args[0])
        message = mailbox.get_message(graph_id)
        if not message.is_read:
            # Opening an email marks it read (D7), so --unread is a work queue.
            mailbox.mark_read(graph_id)
        header = [
            f"From: {message.sender.display() if message.sender else '(no sender)'}",
            f"To: {', '.join(a.display() for a in message.to) or '(none)'}",
        ]
        if message.cc:
            header.append(f"Cc: {', '.join(a.display() for a in message.cc)}")
        header += [
            f"Date: {format_utc(message.received)} UTC",
            f"Subject: {message.subject or '(no subject)'}",
            f"Attachments: {'yes (attachments cannot be opened yet)' if message.has_attachments else 'none'}",
        ]
        body_lines = message.body.replace("\r\n", "\n").split("\n") if message.body.strip() else ["(empty)"]
        return success_result(
            command=parsed.raw,
            detail=f"mail: read {args[0]}",
            kind=KIND,
            data={"mailbox": mailbox.mailbox, "id": args[0].strip().lower()},
            sections=[("MESSAGE", header), ("BODY", body_lines)],
            cwd=ctx.cwd,
        )

    def _send(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        opts = _parse(args, valued={"--cc", "--subject"}, switches=set(), usage=_SEND_USAGE)
        if len(opts.positional) != 1:
            raise CommandError(f"USAGE: {_SEND_USAGE}")
        subject = opts.values.get("--subject", "").strip()
        if not subject:
            raise CommandError("NO_SUBJECT: add --subject <text>")
        text = (body or "").strip()
        if not text:
            raise CommandError("NO_BODY: put the message in the body")
        book = self._contacts_for(ctx.agent.id)
        to = _resolve(book, opts.positional[0])
        cc = _resolve(book, opts.values["--cc"]) if "--cc" in opts.values else []
        if not to:
            raise CommandError("NO_RECIPIENT: name at least one address or saved contact")
        in_to = {contact.address.lower() for contact in to}
        twice = next((contact for contact in cc if contact.address.lower() in in_to), None)
        if twice is not None:
            raise CommandError(f"RECIPIENT_TWICE: {twice.address}")
        mailbox.send(
            [Address(name=c.name or "", address=c.address) for c in to],
            [Address(name=c.name or "", address=c.address) for c in cc],
            subject,
            # Agents write Markdown; it goes out formatted (plan E5).
            render_body(text),
        )
        sent = "sent to " + ", ".join(c.display() for c in to)
        if cc:
            sent += "; cc " + ", ".join(c.display() for c in cc)
        return success_result(
            command=parsed.raw,
            detail=f"mail: {sent}",
            kind=KIND,
            data={"mailbox": mailbox.mailbox, "to": [c.address for c in to], "cc": [c.address for c in cc]},
            sections=[("SENT", [sent, f"Subject: {subject}"])],
            cwd=ctx.cwd,
        )

    def _reply(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        opts = _parse(args, valued=set(), switches={"--all"}, usage=_REPLY_USAGE)
        if len(opts.positional) != 1:
            raise CommandError(f"USAGE: {_REPLY_USAGE}")
        text = (body or "").strip()
        if not text:
            raise CommandError("NO_BODY: put the reply in the body")
        graph_id = self._id_map_for(ctx.agent.id).resolve(opts.positional[0])
        reply_all = "--all" in opts.switches
        # Read first: the result names who the reply went to, and an id that
        # left the inbox fails here rather than after a send attempt.
        original = mailbox.get_message(graph_id)
        mailbox.reply(graph_id, text, reply_all)
        sender = original.sender.display() if original.sender else "the sender"
        sent = f"sent reply to {sender}" + (" and everyone on the message" if reply_all else "")
        return success_result(
            command=parsed.raw,
            detail=f"mail: {sent}",
            kind=KIND,
            data={"mailbox": mailbox.mailbox, "id": opts.positional[0].strip().lower(), "reply_all": reply_all},
            sections=[("SENT", [sent, f"Subject: Re: {original.subject or '(no subject)'}"])],
            cwd=ctx.cwd,
        )

    def _contacts(self, ctx, parsed, args, agent_id: str) -> BossModCliResult:
        book = self._contacts_for(agent_id)
        sub = args[0].lower() if args else "list"
        rest = args[1:]
        if sub == "list":
            if rest:
                raise CommandError(f"USAGE: {_CONTACTS_USAGE}")
            contacts = book.list()
            lines = [c.display() for c in contacts] or ["No saved contacts. Add one with: mail contacts add <addr>"]
            return self._contacts_result(ctx, parsed, "CONTACTS", lines, len(contacts))
        if sub == "add":
            opts = _parse(rest, valued={"--name"}, switches=set(), usage=_CONTACTS_USAGE)
            if len(opts.positional) != 1:
                raise CommandError(f"USAGE: {_CONTACTS_USAGE}")
            result = book.add(_split(opts.positional[0]), opts.values.get("--name"))
            lines = [f"added {c.display()}" for c in result.added] + [f"updated {c.display()}" for c in result.updated]
            return self._contacts_result(ctx, parsed, "CONTACTS", lines, len(book.list()))
        if sub == "remove":
            if len(rest) != 1:
                raise CommandError(f"USAGE: {_CONTACTS_USAGE}")
            removed = book.remove(_split(rest[0]))
            return self._contacts_result(ctx, parsed, "CONTACTS", [f"removed {a}" for a in removed], len(book.list()))
        raise CommandError(f"USAGE: {_CONTACTS_USAGE}")

    def _contacts_result(self, ctx, parsed, title: str, lines: list[str], count: int) -> BossModCliResult:
        return success_result(
            command=parsed.raw,
            detail=f"mail: {count} saved contacts",
            kind=KIND,
            data={"contacts": count},
            sections=[(title, lines)],
            cwd=ctx.cwd,
        )

    @staticmethod
    def _error(ctx: CliExecutionContext, parsed: ParsedCliCommand, message: str) -> BossModCliResult:
        return error_result(parsed.raw, message, cwd=ctx.cwd)


def _parse(args: Sequence[str], *, valued: set[str], switches: set[str], usage: str) -> _Args:
    """Split ``--flag value`` pairs, bare ``--switch`` flags and positionals.

    Raises:
        CommandError: An unknown ``--`` argument, or a valued flag with no value.
    """
    positional: list[str] = []
    values: dict[str, str] = {}
    found: set[str] = set()
    index = 0
    while index < len(args):
        token = args[index]
        if token in switches:
            found.add(token)
            index += 1
        elif token in valued:
            if index + 1 >= len(args):
                raise CommandError(f"USAGE: {usage} ({token} needs a value)")
            values[token] = args[index + 1]
            index += 2
        elif token.startswith("--"):
            raise CommandError(f"USAGE: {usage} (unknown argument {token!r})")
        else:
            positional.append(token)
            index += 1
    return _Args(positional=positional, values=values, switches=found)


def _int_flag(raw: str | None, flag: str, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise CommandError(f"USAGE: {flag} takes a whole number, got {raw!r}") from exc


def _split(raw: str) -> list[str]:
    """Comma-separated tokens; an empty token (``a,,b``) is refused rather than dropped."""
    parts = [part.strip() for part in raw.split(",")]
    if any(not part for part in parts):
        raise CommandError(f"EMPTY_RECIPIENT: {raw!r} has an empty entry between commas")
    return parts


def _resolve(book: ContactBook, raw: str) -> list[Contact]:
    """Resolve a comma list; a name and its own address count once."""
    resolved: list[Contact] = []
    seen: set[str] = set()
    for token in _split(raw):
        contact = book.resolve(token)
        key = contact.address.lower()
        if key not in seen:
            seen.add(key)
            resolved.append(contact)
    return resolved


def format_utc(moment: datetime) -> str:
    """``YYYY-MM-DD HH:MM`` in UTC, the one timestamp shape the CLI and the viewer show."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
