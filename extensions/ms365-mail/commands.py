"""Microsoft 365 Mailbox — the ``mail`` subcommands.

``mail <verb> [args]``, free text in the body (the ``pref``/``write``
convention): ``inbox``, ``read``, ``send``, ``reply``, ``search``, ``sent``,
``archive`` and ``contacts``. Argument parsing lives in ``args.py``.
Every failure is an explicit error result naming a code; nothing is retried
or guessed. Recipients are read and resolved (``recipients.py``) before any
Graph call, and nothing is sent unless every one resolves.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from itertools import groupby
from typing import Callable, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand

from .args import CommandError, int_flag, parse_args
from .contacts import EMAIL_RE, ContactBook, ContactBookError, ContactError, InvalidAddress
from .formatting import EmailStyles, render_body
from .graph import (
    Address,
    FolderCounts,
    GraphAuthError,
    GraphHttpError,
    GraphUnreachable,
    InboxPage,
    Message,
    MessageSummary,
    SentMessage,
    SentPage,
    SentSummary,
    describe_graph_error,
)
from .ids import Folder, IdMap, IdMapError, MessageRef, UnknownMessageId
from .recipients import RecipientError, parse_entry, resolve_list, split_entries

KIND = "mail"
_USAGE = 'USAGE: mail inbox|read|send|reply|search|sent|archive|contacts — run "learn mail" for details'
_INBOX_USAGE = "mail inbox [--unread] [--limit N] [--skip N]"
_SEARCH_USAGE = "mail search <text> [--limit N]"
_SENT_USAGE = "mail sent [--limit N] [--skip N]"
_ARCHIVE_USAGE = "mail archive <id>[,<id>…]"
_SEND_USAGE = "mail send <to…> [--to <…>] [--cc <…>] --subject <text>   (message in the body)"
_REPLY_USAGE = "mail reply <id> [--all]   (reply in the body)"
_CONTACTS_USAGE = "mail contacts [list] | mail contacts add <addr>[,<addr>…] [--name <text>] | mail contacts remove <addr>[,<addr>…]"
# How a folder is named to the agent (the Folder: line, NOT_IN_INBOX).
_FOLDER_LABELS: dict[str, str] = {"inbox": "Inbox", "archive": "Archive", "sentitems": "Sent"}
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
    # Inline CSS for tables in sent mail; clients ignore <style> blocks.
    html_table_style: str = Field(min_length=1)
    html_header_cell_style: str = Field(min_length=1)
    html_cell_style: str = Field(min_length=1)

    @model_validator(mode="after")
    def _default_within_max(self) -> "Ms365MailDefaults":
        if self.inbox_default_limit > self.inbox_max_limit:
            raise ValueError("inbox_default_limit must not exceed inbox_max_limit")
        return self


class MailboxLike(Protocol):
    """What the commands need from ``graph.GraphMailbox`` (a fake in tests)."""

    mailbox: str

    def list_inbox(self, top: int, skip: int, unread_only: bool) -> InboxPage: ...
    def folder_counts(self, folder: Folder) -> FolderCounts: ...
    def search(self, folder: Folder, query: str, top: int) -> list[MessageSummary]: ...
    def list_sent(self, top: int, skip: int) -> SentPage: ...
    def get_sent_message(self, message_id: str) -> SentMessage: ...
    def get_message(self, folder: Folder, message_id: str) -> Message: ...
    def mark_read(self, folder: Folder, message_id: str) -> None: ...
    def move_to_archive(self, message_id: str) -> str: ...
    def send(self, to: Sequence[Address], cc: Sequence[Address], subject: str, body_html: str) -> None: ...
    def reply(self, folder: Folder, message_id: str, body_html: str, reply_all: bool) -> None: ...


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
        self._email_styles = EmailStyles(
            table=defaults.html_table_style,
            header_cell=defaults.html_header_cell_style,
            cell=defaults.html_cell_style,
        )

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
                "search": self._search,
                "sent": self._sent,
                "archive": self._archive,
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
        opts = parse_args(args, valued={"--limit", "--skip"}, switches={"--unread"}, usage=_INBOX_USAGE)
        if opts.positional:
            raise CommandError(f"USAGE: {_INBOX_USAGE} (unexpected {opts.positional[0]!r})")
        limit, skip = self._page_flags(opts)
        unread = "--unread" in opts.switches
        page = mailbox.list_inbox(top=limit, skip=skip, unread_only=unread)
        counts = mailbox.folder_counts("inbox")
        shorts = self._id_map_for(ctx.agent.id).remember("inbox", (message.id for message in page.messages))
        scope = "unread messages" if unread else "messages"
        totals = f"{counts.unread:,} unread" if unread else f"{counts.total:,} messages, {counts.unread:,} unread"
        if not page.messages:
            header = f"Inbox of {mailbox.mailbox} — {totals}. No {scope}" + (f" after the first {skip}" if skip else "") + "."
            lines = [header]
        else:
            first, last = skip + 1, skip + len(page.messages)
            lines = [f"Inbox of {mailbox.mailbox} — {totals}. Showing {first}–{last}, newest first (times UTC; ● unread)"]
            lines += [self._inbox_line(short, message) for short, message in zip(shorts, page.messages)]
            if page.has_more:
                more = f"mail inbox{' --unread' if unread else ''} --limit {limit} --skip {last}"
                lines.append(f"More: {more}")
        return success_result(
            command=parsed.raw,
            detail=f"mail: {len(page.messages)} {scope}",
            kind=KIND,
            data={
                "mailbox": mailbox.mailbox,
                "count": len(page.messages),
                "has_more": page.has_more,
                "total": counts.total,
                "unread": counts.unread,
            },
            sections=[("INBOX", lines)],
            cwd=ctx.cwd,
        )

    def _page_flags(self, opts) -> tuple[int, int]:
        """``(limit, skip)`` from ``--limit``/``--skip``, bounded like ``mail inbox``.

        Raises:
            CommandError: ``LIMIT_OUT_OF_RANGE``, ``SKIP_OUT_OF_RANGE``, or
                ``USAGE`` for a value that is not a whole number.
        """
        limit = self._limit_flag(opts)
        skip = int_flag(opts.one("--skip"), "--skip", 0)
        if skip < 0:
            raise CommandError(f"SKIP_OUT_OF_RANGE: --skip must be 0 or more, got {skip}")
        return limit, skip

    def _limit_flag(self, opts) -> int:
        limit = int_flag(opts.one("--limit"), "--limit", self._defaults.inbox_default_limit)
        if not 1 <= limit <= self._defaults.inbox_max_limit:
            raise CommandError(f"LIMIT_OUT_OF_RANGE: --limit must be 1–{self._defaults.inbox_max_limit}, got {limit}")
        return limit

    def _inbox_line(self, short: str, message) -> str:
        mark = "○" if message.is_read else "●"
        sender = message.sender.display() if message.sender else "(no sender)"
        subject = message.subject or "(no subject)"
        preview = _clip(" ".join(message.preview.split()), self._defaults.preview_chars)
        return f"[{short}] {mark} {format_utc(message.received)}  {sender}  {subject} — \"{preview}\""

    def _read(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        if len(args) != 1:
            raise CommandError("USAGE: mail read <id>")
        ref = self._id_map_for(ctx.agent.id).resolve(args[0])
        short = args[0].strip().lower()
        if ref.folder == "sentitems":
            sections = _sent_message_sections(mailbox.get_sent_message(ref.graph_id))
        else:
            message = mailbox.get_message(ref.folder, ref.graph_id)
            if not message.is_read:
                # Opening an email marks it read (D7), so --unread is a work queue.
                mailbox.mark_read(ref.folder, ref.graph_id)
            sections = self._received_sections(short, ref, message, mailbox.mailbox)
        return success_result(
            command=parsed.raw,
            detail=f"mail: read {args[0]}",
            kind=KIND,
            data={"mailbox": mailbox.mailbox, "id": short, "folder": ref.folder},
            sections=sections,
            cwd=ctx.cwd,
        )

    @staticmethod
    def _received_sections(short: str, ref: MessageRef, message: Message, own: str) -> list[tuple[str, list[str]]]:
        """MESSAGE, BODY and REPLY sections for an inbox or archive message (pure)."""
        header = [
            f"Folder: {_FOLDER_LABELS[ref.folder]}",
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
        sender_only, everyone = _reply_audience(message, own)
        # Who each reply form reaches, shown where the agent decides (not left
        # to an implicit default).
        reply_lines = [f"mail reply {short}        → {_join(sender_only)}"]
        if len(everyone) > len(sender_only):
            reply_lines.append(f"mail reply {short} --all  → {_join(everyone)}")
        return [("MESSAGE", header), ("BODY", _body_lines(message.body)), ("REPLY", reply_lines)]

    def _send(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        recipient_flags = {"--to", "--cc"}
        opts = parse_args(
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
            render_body(text, self._email_styles),
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
        opts = parse_args(args, valued=set(), switches={"--all"}, usage=_REPLY_USAGE)
        if len(opts.positional) != 1:
            raise CommandError(f"USAGE: {_REPLY_USAGE}")
        text = (body or "").strip()
        if not text:
            raise CommandError("NO_BODY: put the reply in the body")
        ref = self._id_map_for(ctx.agent.id).resolve(opts.positional[0])
        if ref.folder == "sentitems":
            raise CommandError("CANNOT_REPLY_TO_SENT: use mail send to write to them")
        reply_all = "--all" in opts.switches
        # Read first: the result names who the reply went to, and an id that
        # left its folder fails here rather than after a send attempt.
        original = mailbox.get_message(ref.folder, ref.graph_id)
        # Agents write Markdown; replies go out formatted like sends.
        mailbox.reply(ref.folder, ref.graph_id, render_body(text, self._email_styles), reply_all)
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

    def _search(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        """``mail search <text> [--limit N]`` over the inbox and the archive together.

        Two Graph searches (one per folder, ``$top`` = the limit, no paging),
        merged newest first by ``received`` (ties: inbox first) and cut to the
        limit. A failure of either search is the command's error; it never
        narrows to the inbox alone (``ARCHIVE_NOT_FOUND`` when Graph answers
        404 for the archive).
        """
        opts = parse_args(args, valued={"--limit"}, switches=set(), usage=_SEARCH_USAGE)
        query = _search_query(_without_flag(args, "--limit"))
        if not query:
            raise CommandError(f"NO_QUERY: {_SEARCH_USAGE}")
        limit = self._limit_flag(opts)
        inbox = [("inbox", message) for message in mailbox.search("inbox", query, limit)]
        try:
            archive = [("archive", message) for message in mailbox.search("archive", query, limit)]
        except GraphHttpError as exc:
            if exc.status != 404:
                raise
            # describe_graph_error's 404 sentence is about a message; here the folder is missing.
            raise CommandError(
                f"ARCHIVE_NOT_FOUND: the archive folder could not be searched (HTTP 404 {exc.code}: {exc.message})"
            ) from exc
        # sorted() is stable with reverse=True, so equal times keep inbox first.
        found = sorted(inbox + archive, key=lambda pair: pair[1].received, reverse=True)[:limit]
        id_map = self._id_map_for(ctx.agent.id)
        shorts: list[str] = []
        # One write per run of same-folder results keeps the map's recency in result order.
        for folder, run in groupby(found, key=lambda pair: pair[0]):
            shorts += id_map.remember(folder, (message.id for _, message in run))
        if not found:
            lines = [f'Search "{query}" in inbox and archive — no matches.']
        else:
            lines = [
                f'Search "{query}" in inbox and archive — {len(found)} matches shown, newest first '
                f"(up to --limit {limit}; narrow the search to see others; times UTC; ● unread)"
            ]
            lines += [
                self._inbox_line(short, message) + (" (archive)" if folder == "archive" else "")
                for short, (folder, message) in zip(shorts, found)
            ]
        return success_result(
            command=parsed.raw,
            detail=f"mail: {len(found)} matches",
            kind=KIND,
            data={"mailbox": mailbox.mailbox, "query": query, "count": len(found)},
            sections=[("SEARCH", lines)],
            cwd=ctx.cwd,
        )

    def _sent(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        opts = parse_args(args, valued={"--limit", "--skip"}, switches=set(), usage=_SENT_USAGE)
        if opts.positional:
            raise CommandError(f"USAGE: {_SENT_USAGE} (unexpected {opts.positional[0]!r})")
        limit, skip = self._page_flags(opts)
        page = mailbox.list_sent(top=limit, skip=skip)
        counts = mailbox.folder_counts("sentitems")
        shorts = self._id_map_for(ctx.agent.id).remember("sentitems", (message.id for message in page.messages))
        totals = f"{counts.total:,} messages"
        if not page.messages:
            lines = [f"Sent from {mailbox.mailbox} — {totals}. No messages" + (f" after the first {skip}" if skip else "") + "."]
        else:
            first, last = skip + 1, skip + len(page.messages)
            lines = [f"Sent from {mailbox.mailbox} — {totals}. Showing {first}–{last}, newest first (times UTC)"]
            lines += [self._sent_line(short, message) for short, message in zip(shorts, page.messages)]
            if page.has_more:
                lines.append(f"More: mail sent --limit {limit} --skip {last}")
        return success_result(
            command=parsed.raw,
            detail=f"mail: {len(page.messages)} sent messages",
            kind=KIND,
            data={
                "mailbox": mailbox.mailbox,
                "count": len(page.messages),
                "has_more": page.has_more,
                "total": counts.total,
            },
            sections=[("SENT", lines)],
            cwd=ctx.cwd,
        )

    def _sent_line(self, short: str, message: SentSummary) -> str:
        to = ", ".join(address.display() for address in message.to) or "(no one)"
        subject = message.subject or "(no subject)"
        preview = _clip(" ".join(message.preview.split()), self._defaults.preview_chars)
        return f"[{short}] {format_utc(message.sent)}  to {to}  {subject} — \"{preview}\""

    def _archive(self, ctx, parsed, args, body, mailbox: MailboxLike) -> BossModCliResult:
        """``mail archive <id>[,<id>…]``: move inbox messages to the Archive folder.

        Every id is resolved, and must be an inbox message, before the first
        move (``UNKNOWN_MESSAGE_ID`` / ``NOT_IN_INBOX``: nothing moves). Graph
        gives a moved message a new id, which is remembered under ``archive``
        and reported (``archived m1 → now m9 (archive)``).

        Failure: moves are not transactional. If Graph refuses a move, the
        result is an error whose first line is the code, the id that failed
        and Graph's HTTP status and code, followed by the moves that did
        happen before it (``archived … → now …``) and ``Not attempted: …``
        for the ids after it. Nothing is retried.
        """
        opts = parse_args(args, valued=set(), switches=set(), usage=_ARCHIVE_USAGE)
        if not opts.positional:
            raise CommandError(f"USAGE: {_ARCHIVE_USAGE}")
        shorts: list[str] = []
        for entry in split_entries(" ".join(opts.positional)):
            short = entry.lower()
            # The same id twice is one request; a second move would find the old id gone.
            if short not in shorts:
                shorts.append(short)
        id_map = self._id_map_for(ctx.agent.id)
        refs = [id_map.resolve(short) for short in shorts]
        for short, ref in zip(shorts, refs):
            if ref.folder != "inbox":
                raise CommandError(
                    f"NOT_IN_INBOX: {short} is in {_FOLDER_LABELS[ref.folder].lower()} — only inbox messages can be "
                    "archived; nothing was moved"
                )
        done: list[str] = []
        for index, (short, ref) in enumerate(zip(shorts, refs)):
            try:
                new_id = mailbox.move_to_archive(ref.graph_id)
            except (GraphAuthError, GraphUnreachable, GraphHttpError) as exc:
                # Graph's own code is kept: a 404 here can mean the message
                # left the inbox or the mailbox has no Archive folder.
                code, message = describe_graph_error(exc)
                graph_code = f" (HTTP {exc.status} {exc.code})" if isinstance(exc, GraphHttpError) else ""
                report = [f"{code}: {short} was not archived — {message}{graph_code}", *done]
                if shorts[index + 1:]:
                    report.append(f"Not attempted: {', '.join(shorts[index + 1:])}")
                return self._error(ctx, parsed, "\n".join(report))
            new_short = id_map.remember("archive", [new_id])[0]
            done.append(f"archived {short} → now {new_short} (archive)")
        return success_result(
            command=parsed.raw,
            detail=f"mail: archived {len(done)}",
            kind=KIND,
            data={"mailbox": mailbox.mailbox, "archived": len(done)},
            sections=[("ARCHIVED", done)],
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
            opts = parse_args(rest, valued={"--name"}, switches=set(), usage=_CONTACTS_USAGE)
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
            opts = parse_args(rest, valued=set(), switches=set(), usage=_CONTACTS_USAGE)
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


def _without_flag(args: Sequence[str], flag: str) -> list[str]:
    """``args`` without ``flag`` and the value after it (``parse_args`` has already validated them)."""
    kept: list[str] = []
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
        elif token == flag:
            skip_next = True
        else:
            kept.append(token)
    return kept


_KQL_PROPERTY_RE = re.compile(r"^([A-Za-z]+):(.+)$", re.DOTALL)
# Searchable message properties per Graph's "Use the $search query parameter"
# docs; any other "word:" (e.g. "Re:") is ordinary text.
_KQL_PROPERTIES = frozenset({
    "attachment", "bcc", "body", "cc", "from", "hasattachment", "importance", "kind",
    "participants", "received", "recipients", "sent", "size", "subject", "to",
})


def _search_query(words: Sequence[str]) -> str:
    """The search text as the agent wrote it, from the shell-split words (pure).

    The CLI tokenizer drops quotes, so a word holding whitespace was a quoted
    phrase: it is quoted again, after the property name for KQL
    (``subject:"weekly report"``), so the phrase is not split into words.
    """
    rebuilt: list[str] = []
    for word in words:
        if not any(char.isspace() for char in word):
            rebuilt.append(word)
            continue
        match = _KQL_PROPERTY_RE.match(word)
        if match and match.group(1).lower() in _KQL_PROPERTIES:
            rebuilt.append(f'{match.group(1)}:"{match.group(2)}"')
        else:
            rebuilt.append(f'"{word}"')
    return " ".join(rebuilt).strip()


def _body_lines(body: str) -> list[str]:
    return body.replace("\r\n", "\n").split("\n") if body.strip() else ["(empty)"]


def _sent_message_sections(message: SentMessage) -> list[tuple[str, list[str]]]:
    """MESSAGE and BODY sections for a Sent Items message; no REPLY section (pure)."""
    header = [f"Folder: {_FOLDER_LABELS['sentitems']}"]
    if message.sender:
        header.append(f"From: {message.sender.display()}")
    header.append(f"To: {', '.join(a.display() for a in message.to) or '(none)'}")
    if message.cc:
        header.append(f"Cc: {', '.join(a.display() for a in message.cc)}")
    header += [
        f"Date: {format_utc(message.sent)} UTC",
        f"Subject: {message.subject or '(no subject)'}",
        f"Attachments: {'yes (attachments cannot be opened yet)' if message.has_attachments else 'none'}",
    ]
    return [("MESSAGE", header), ("BODY", _body_lines(message.body))]


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
