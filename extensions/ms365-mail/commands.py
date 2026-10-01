"""Microsoft 365 Mailbox — the ``mail`` subcommands.

``mail <verb> [args]``, free text in the body (the ``pref``/``write``
convention): ``inbox``, ``read``, ``send``, ``reply`` and ``contacts``.
Every failure is an explicit error result naming a code; nothing is retried
or guessed. Recipients are read and resolved (``recipients.py``) before any
Graph call, and nothing is sent unless every one resolves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AbstractSet, Callable, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand

from .contacts import EMAIL_RE, ContactBook, ContactBookError, ContactError, InvalidAddress
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
from .recipients import RecipientError, parse_entry, resolve_list, split_entries

KIND = "mail"
_USAGE = 'USAGE: mail inbox|read|send|reply|contacts — run "learn mail" for details'
_INBOX_USAGE = "mail inbox [--unread] [--limit N] [--skip N]"
_SEND_USAGE = "mail send <to…> [--to <…>] [--cc <…>] --subject <text>   (message in the body)"
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
    def reply(self, message_id: str, body_html: str, reply_all: bool) -> None: ...


class CommandError(ValueError):
    """A usage or precondition error; the message goes to the agent as is."""


@dataclass(frozen=True)
class _Args:
    positional: list[str]
    values: dict[str, list[str]]
    switches: set[str]

    def one(self, flag: str) -> str | None:
        """The value of a non-repeatable flag (``_parse`` refuses it twice), or ``None``."""
        given = self.values.get(flag)
        return given[0] if given else None


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
        limit = _int_flag(opts.one("--limit"), "--limit", self._defaults.inbox_default_limit)
        if not 1 <= limit <= self._defaults.inbox_max_limit:
            raise CommandError(f"LIMIT_OUT_OF_RANGE: --limit must be 1–{self._defaults.inbox_max_limit}, got {limit}")
        skip = _int_flag(opts.one("--skip"), "--skip", 0)
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
        short = args[0].strip().lower()
        sender_only, everyone = _reply_audience(message, mailbox.mailbox)
        # Who each reply form reaches, shown where the agent decides (not left
        # to an implicit default).
        reply_lines = [f"mail reply {short}        → {_join(sender_only)}"]
        if len(everyone) > len(sender_only):
            reply_lines.append(f"mail reply {short} --all  → {_join(everyone)}")
        return success_result(
            command=parsed.raw,
            detail=f"mail: read {args[0]}",
            kind=KIND,
            data={"mailbox": mailbox.mailbox, "id": short},
            sections=[("MESSAGE", header), ("BODY", body_lines), ("REPLY", reply_lines)],
            cwd=ctx.cwd,
        )

    def _send(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        recipient_flags = {"--to", "--cc"}
        opts = _parse(
            args,
            valued={"--to", "--cc", "--subject"},
            switches=set(),
            usage=_SEND_USAGE,
            repeatable=recipient_flags,
            continues=recipient_flags,
        )
        # Positional tokens rejoin with the space the tokenizer split them on,
        # so "Gene Whiddon <g@x.com>, alice" reads as written.
        to_values = ([" ".join(opts.positional)] if opts.positional else []) + opts.values.get("--to", [])
        if not to_values:
            raise CommandError("NO_RECIPIENT: name at least one address or saved contact")
        subject = (opts.one("--subject") or "").strip()
        if not subject:
            raise CommandError("NO_SUBJECT: add --subject <text>")
        text = (body or "").strip()
        if not text:
            raise CommandError("NO_BODY: put the message in the body")
        book = self._contacts_for(ctx.agent.id)
        to = resolve_list(book, _recipient_text(to_values))
        cc_values = opts.values.get("--cc", [])
        cc_all = resolve_list(book, _recipient_text(cc_values)) if cc_values else []
        # The same person in To and Cc is harmless: they get it once, in To.
        in_to = {contact.address.lower() for contact in to}
        cc = [contact for contact in cc_all if contact.address.lower() not in in_to]
        also_in_to = [contact for contact in cc_all if contact.address.lower() in in_to]
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
        lines = [f"To: {', '.join(c.display() for c in to)}"]
        if cc:
            lines.append(f"Cc: {', '.join(c.display() for c in cc)}")
        lines.append(f"Subject: {subject}")
        if also_in_to:
            lines.append(f"Also in To, so not repeated in Cc: {', '.join(c.display() for c in also_in_to)}")
        return success_result(
            command=parsed.raw,
            detail=f"mail: {sent}",
            kind=KIND,
            data={"mailbox": mailbox.mailbox, "to": [c.address for c in to], "cc": [c.address for c in cc]},
            sections=[("SENT", lines)],
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
        # Agents write Markdown; replies go out formatted like sends.
        mailbox.reply(graph_id, render_body(text), reply_all)
        sender_only, everyone = _reply_audience(original, mailbox.mailbox)
        recipients = everyone if reply_all else sender_only
        sent = f"sent reply to {_join(recipients)}"
        lines = [sent, f"Subject: Re: {original.subject or '(no subject)'}"]
        left_out = [address for address in everyone if address not in recipients]
        if left_out:
            # A fact only: an instruction to re-send would invite a duplicate.
            lines.append(f"Not included: {_join(left_out)}")
        return success_result(
            command=parsed.raw,
            detail=f"mail: {sent}",
            kind=KIND,
            data={
                "mailbox": mailbox.mailbox,
                "id": opts.positional[0].strip().lower(),
                "reply_all": reply_all,
                "to": [address.address for address in recipients],
            },
            sections=[("SENT", lines)],
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
            if not opts.positional:
                raise CommandError(f"USAGE: {_CONTACTS_USAGE}")
            entries = _entries(opts.positional)
            name = opts.one("--name")
            if name is not None:
                # --name is explicit and names exactly one address (enforced by add).
                results = [book.add([address for _, address in entries], name)]
            else:
                # Check every address first, so a bad one saves nothing even
                # though named entries are saved one call each.
                for _, address in entries:
                    if not EMAIL_RE.match(address):
                        raise InvalidAddress(address)
                results = [book.add([address], entry_name) for entry_name, address in entries]
            lines = [f"added {c.display()}" for r in results for c in r.added]
            lines += [f"updated {c.display()}" for r in results for c in r.updated]
            return self._contacts_result(ctx, parsed, "CONTACTS", lines, len(book.list()))
        if sub == "remove":
            opts = _parse(rest, valued=set(), switches=set(), usage=_CONTACTS_USAGE)
            if not opts.positional:
                raise CommandError(f"USAGE: {_CONTACTS_USAGE}")
            # A "Name <address>" entry removes by its address only.
            removed = book.remove([address for _, address in _entries(opts.positional)])
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


def _parse(
    args: Sequence[str],
    *,
    valued: AbstractSet[str],
    switches: AbstractSet[str],
    usage: str,
    repeatable: AbstractSet[str] = frozenset(),
    continues: AbstractSet[str] = frozenset(),
) -> _Args:
    """Split ``--flag value`` pairs, bare ``--switch`` flags and positionals.

    The CLI tokenizes like a shell, so ``a@x.com, b@x.com`` arrives as two
    tokens. A positional, and the value of a flag in ``continues``, therefore
    keeps absorbing following non-``--`` tokens (joined by a space) while the
    text so far ends with ``,`` or ``;`` or the next token starts with one.

    Args:
        args: The tokens after the verb.
        valued: Flags that take a value.
        switches: Bare flags.
        usage: The usage line quoted in errors.
        repeatable: Valued flags that may be given more than once; their
            values accumulate in order. Any other valued flag given twice is
            an error, never last-wins.
        continues: Valued flags whose value follows the continuation rule.

    Returns:
        The positionals, every valued flag's values (one item unless
        repeatable) and the switches found.

    Raises:
        CommandError: An unknown ``--`` argument, a valued flag with no value,
            a non-repeatable flag given twice (``USAGE``), or a bare token
            right after a ``continues`` flag's value (``AMBIGUOUS_RECIPIENTS``:
            it could belong to the flag or be a positional).
    """
    positional: list[str] = []
    values: dict[str, list[str]] = {}
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
            if token in values and token not in repeatable:
                raise CommandError(f"USAGE: {usage} ({token} given twice)")
            if token in continues:
                value, index = _continued(args, index + 1)
                if index < len(args) and not args[index].startswith("--"):
                    raise CommandError(
                        f"AMBIGUOUS_RECIPIENTS: {args[index]!r} follows {token} {value!r} — separate "
                        f"{token} recipients with commas, or quote a name that has spaces"
                    )
            else:
                value, index = args[index + 1], index + 2
            values.setdefault(token, []).append(value)
        elif token.startswith("--"):
            raise CommandError(f"USAGE: {usage} (unknown argument {token!r})")
        else:
            value, index = _continued(args, index)
            positional.append(value)
    return _Args(positional=positional, values=values, switches=found)


def _continued(args: Sequence[str], index: int) -> tuple[str, int]:
    """The token at ``index`` plus any it continues into; returns ``(text, next index)``."""
    text = args[index]
    index += 1
    while index < len(args) and not args[index].startswith("--") and (
        text.rstrip().endswith((",", ";")) or args[index].lstrip().startswith((",", ";"))
    ):
        text = f"{text} {args[index]}"
        index += 1
    return text, index


def _int_flag(raw: str | None, flag: str, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise CommandError(f"USAGE: {flag} takes a whole number, got {raw!r}") from exc


def _recipient_text(values: Sequence[str]) -> str:
    """Join one field's recipient values (positional runs, repeated flags) into one list.

    Raises:
        RecipientError: ``EMPTY_RECIPIENT`` for a blank value (``--cc ""``),
            which joining would otherwise hide.
    """
    if any(not value.strip() for value in values):
        raise RecipientError("EMPTY_RECIPIENT", "an empty recipient was given")
    return ", ".join(values)


def _entries(positional: Sequence[str]) -> list[tuple[str | None, str]]:
    """Every ``(display_name, token)`` pair written in the positionals (``contacts add/remove``)."""
    return [pair for entry in split_entries(" ".join(positional)) for pair in parse_entry(entry)]


def _reply_audience(message: Message, own: str) -> tuple[list[Address], list[Address]]:
    """Who ``mail reply`` and ``mail reply --all`` reach, for display (pure).

    Graph decides the real recipients; this only describes them, in Graph's
    order: the sender, then To, then Cc. Duplicates (case-insensitive by
    address) and the agent's own mailbox are left out.

    Args:
        message: The message being answered.
        own: The agent's mailbox address.

    Returns:
        ``(sender_only, everyone)``; ``sender_only`` is empty when the message
        has no sender (or the agent sent it), and is always a prefix of
        ``everyone``.
    """
    seen = {own.lower()}
    everyone: list[Address] = []
    for address in [*([message.sender] if message.sender else []), *message.to, *message.cc]:
        key = address.address.lower()
        if key not in seen:
            seen.add(key)
            everyone.append(address)
    sender_counted = message.sender is not None and message.sender.address.lower() != own.lower()
    return (everyone[:1] if sender_counted else []), everyone


def _join(addresses: Sequence[Address]) -> str:
    return ", ".join(address.display() for address in addresses) or "(no one)"


def format_utc(moment: datetime) -> str:
    """``YYYY-MM-DD HH:MM`` in UTC, the one timestamp shape the CLI and the viewer show."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
