"""Microsoft 365 Mailbox: the ``mail`` subcommands, ids and contacts, over a fake mailbox."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.bm_cli.parser import parse_cli_command
from core.bm_cli.types import CliExecutionContext
from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_ENTRY = get_discovery().get("ms365-mail")
_PACKAGE = import_package(_ENTRY)
commands = importlib.import_module(f"{_PACKAGE.__name__}.commands")
contacts_mod = importlib.import_module(f"{_PACKAGE.__name__}.contacts")
graph = importlib.import_module(f"{_PACKAGE.__name__}.graph")
ids = importlib.import_module(f"{_PACKAGE.__name__}.ids")

DEFAULTS = commands.Ms365MailDefaults.model_validate(_ENTRY.manifest.defaults)


def _summary(n: int, *, read: bool = False, preview: str = "Hello there", received=None, prefix="GRAPH-ID") -> object:
    return graph.MessageSummary(
        id=f"{prefix}-{n}", subject=f"Subject {n}", sender=graph.Address("Alice Doe", "alice@x.com"),
        received=received or datetime(2026, 9, 29, 9, 14, tzinfo=timezone.utc), is_read=read, preview=preview,
        has_attachments=False,
    )


def _sent_summary(n: int) -> object:
    return graph.SentSummary(
        id=f"SENT-{n}", subject=f"Report {n}", to=[graph.Address("Jordan", "jordan@contoso.com")],
        sent=datetime(2026, 9, 28, 16, 5, tzinfo=timezone.utc), preview="Numbers attached", has_attachments=False,
    )


class FakeMailbox:
    """A MailboxLike that records every call."""

    mailbox = "reports@contoso.com"

    def __init__(self, messages=None, has_more=False, to=None, cc=None) -> None:
        self.messages = messages if messages is not None else [_summary(1), _summary(2, read=True)]
        self.archive: list = []
        self.sent: list = [_sent_summary(1), _sent_summary(2)]
        self.has_more = has_more
        self.to = to if to is not None else [graph.Address("", "reports@contoso.com")]
        self.cc = cc if cc is not None else []
        self.counts = {
            "inbox": graph.FolderCounts(total=1284, unread=7),
            "archive": graph.FolderCounts(total=40, unread=0),
            "sentitems": graph.FolderCounts(total=312, unread=0),
        }
        self.hits: dict[str, list] = {"inbox": [], "archive": []}
        self.move_failures: dict[str, Exception] = {}
        self.calls: list[tuple] = []

    def _folder(self, folder):
        return {"inbox": self.messages, "archive": self.archive}[folder]

    def list_inbox(self, top, skip, unread_only):
        self.calls.append(("list", top, skip, unread_only))
        found = [m for m in self.messages if not (unread_only and m.is_read)]
        return graph.InboxPage(messages=found[:top], has_more=self.has_more)

    def folder_counts(self, folder):
        self.calls.append(("counts", folder))
        return self.counts[folder]

    def search(self, folder, query, top):
        self.calls.append(("search", folder, query, top))
        return self.hits[folder][:top]

    def list_sent(self, top, skip):
        self.calls.append(("list_sent", top, skip))
        return graph.SentPage(messages=self.sent[skip:skip + top], has_more=self.has_more)

    def get_sent_message(self, message_id):
        self.calls.append(("get_sent", message_id))
        summary = next(m for m in self.sent if m.id == message_id)
        return graph.SentMessage(
            id=summary.id, subject=summary.subject, sender=graph.Address("", self.mailbox), to=summary.to,
            cc=[graph.Address("Gene", "gene@x.com")], sent=summary.sent, has_attachments=False, body="Sent text",
        )

    def get_message(self, folder, message_id):
        self.calls.append(("get", folder, message_id))
        summary = next(m for m in self._folder(folder) + self.hits.get(folder, []) if m.id == message_id)
        return graph.Message(
            id=summary.id, subject=summary.subject, sender=summary.sender,
            to=self.to, cc=self.cc, received=summary.received,
            is_read=summary.is_read, has_attachments=False, body="Line one\r\nLine two",
        )

    def mark_read(self, folder, message_id):
        self.calls.append(("mark_read", folder, message_id))

    def move_to_archive(self, message_id):
        self.calls.append(("move", message_id))
        if message_id in self.move_failures:
            raise self.move_failures[message_id]
        moved = next(m for m in self.messages if m.id == message_id)
        self.messages.remove(moved)
        copy = graph.MessageSummary(**{**moved.__dict__, "id": f"ARCH-{message_id}"})
        self.archive.append(copy)
        return copy.id

    def send(self, to, cc, subject, body_html):
        self.calls.append(("send", [a.address for a in to], [a.address for a in cc], subject, body_html))

    def reply(self, folder, message_id, body_html, reply_all):
        self.calls.append(("reply", folder, message_id, body_html, reply_all))


@pytest.fixture()
def env(tmp_path: Path):
    state = {"mailbox": FakeMailbox()}
    runner = commands.MailCommands(
        mailbox_for=lambda agent_id: state["mailbox"],
        id_map_for=lambda agent_id: ids.IdMap(tmp_path / "message_ids" / f"{agent_id}.json", DEFAULTS.id_map_keep),
        contacts_for=lambda agent_id: contacts_mod.ContactBook(tmp_path / "contacts" / f"{agent_id}.json"),
        defaults=DEFAULTS,
    )
    ctx = CliExecutionContext(agent=SimpleNamespace(id="agent-1"), state=None, cwd="/me")

    def run(raw: str, body: str | None = None):
        return runner.handle(ctx, parse_cli_command(raw), body)

    return SimpleNamespace(run=run, state=state, tmp=tmp_path)


def _book(env) -> object:
    return contacts_mod.ContactBook(env.tmp / "contacts" / "agent-1.json")


# ─── inbox / read ───


def test_inbox_lists_short_ids_unread_marks_and_previews(env) -> None:
    result = env.run("mail inbox")
    assert result.ok, result.prompt_content
    short = ids.short_id("GRAPH-ID-1")
    assert f'[{short}] ● 2026-09-29 09:14  Alice Doe <alice@x.com>  Subject 1 — "Hello there"' in result.prompt_content
    assert f"[{ids.short_id('GRAPH-ID-2')}] ○ " in result.prompt_content
    assert ("Inbox of reports@contoso.com — 1,284 messages, 7 unread. Showing 1–2, newest first "
            "(times UTC; ● unread)") in result.prompt_content
    assert env.state["mailbox"].calls == [("list", DEFAULTS.inbox_default_limit, 0, False), ("counts", "inbox")]
    assert (result.data["total"], result.data["unread"]) == (1284, 7)


def test_inbox_unread_limit_and_skip(env) -> None:
    env.state["mailbox"].has_more = True
    result = env.run("mail inbox --unread --limit 1 --skip 3")
    assert result.ok
    assert env.state["mailbox"].calls == [("list", 1, 3, True), ("counts", "inbox")]
    assert "Inbox of reports@contoso.com — 7 unread. Showing 4–4, newest first" in result.prompt_content
    assert "More: mail inbox --unread --limit 1 --skip 4" in result.prompt_content


def test_an_empty_inbox_still_shows_the_counts(env) -> None:
    env.state["mailbox"] = FakeMailbox([])
    env.state["mailbox"].counts["inbox"] = graph.FolderCounts(total=0, unread=0)
    assert "Inbox of reports@contoso.com — 0 messages, 0 unread. No messages." in env.run("mail inbox").prompt_content
    after = env.run("mail inbox --unread --skip 20").prompt_content
    assert "Inbox of reports@contoso.com — 0 unread. No unread messages after the first 20." in after


@pytest.mark.parametrize("raw, code", [
    ("mail inbox --limit 0", "LIMIT_OUT_OF_RANGE"),
    (f"mail inbox --limit {DEFAULTS.inbox_max_limit + 1}", "LIMIT_OUT_OF_RANGE"),
    ("mail inbox --limit ten", "USAGE"),
    ("mail inbox --skip -1", "SKIP_OUT_OF_RANGE"),
    ("mail inbox --unknown", "USAGE"),
])
def test_inbox_bounds_are_explicit_errors_never_clamped(env, raw: str, code: str) -> None:
    result = env.run(raw)
    assert not result.ok and code in result.prompt_content
    assert env.state["mailbox"].calls == []


def test_the_preview_is_clipped_to_preview_chars(env) -> None:
    env.state["mailbox"] = FakeMailbox([_summary(1, preview="word " * 100)])
    result = env.run("mail inbox")
    line = next(line for line in result.prompt_content.splitlines() if "Subject 1" in line)
    preview = line.split('— "', 1)[1].rstrip('"')
    assert len(preview) <= DEFAULTS.preview_chars and preview.endswith("…")


def test_read_shows_headers_and_body_and_marks_it_read(env) -> None:
    env.run("mail inbox")
    short = ids.short_id("GRAPH-ID-1")
    result = env.run(f"mail read {short}")
    assert result.ok, result.prompt_content
    for line in ("Folder: Inbox", "From: Alice Doe <alice@x.com>", "To: reports@contoso.com", "Date: 2026-09-29 09:14 UTC",
                 "Subject: Subject 1", "Attachments: none", "Line one", "Line two"):
        assert line in result.prompt_content
    assert ("mark_read", "inbox", "GRAPH-ID-1") in env.state["mailbox"].calls


def test_reading_a_read_message_does_not_mark_it_again(env) -> None:
    env.run("mail inbox")
    env.run(f"mail read {ids.short_id('GRAPH-ID-2')}")
    assert not any(call[0] == "mark_read" for call in env.state["mailbox"].calls)


def test_an_unknown_short_id_names_the_way_out(env) -> None:
    result = env.run("mail read m00000000")
    assert not result.ok
    assert 'UNKNOWN_MESSAGE_ID: m00000000 — run "mail inbox" to list current ids' in result.prompt_content


def test_every_mail_verb_needs_a_mailbox(env) -> None:
    env.state["mailbox"] = None
    for raw in ("mail inbox", "mail read m1", "mail send a@x.com --subject s", "mail reply m1",
                "mail search x", "mail sent", "mail archive m1"):
        result = env.run(raw, "body")
        assert not result.ok and commands.NOT_CONFIGURED in result.prompt_content, raw
    assert env.run("mail contacts").ok  # contacts are BossMod-side only


def test_a_graph_failure_is_an_explicit_code(env) -> None:
    class Throttled(FakeMailbox):
        def list_inbox(self, top, skip, unread_only):
            raise graph.GraphHttpError(429, "TooManyRequests", "slow down", retry_after=12)

    env.state["mailbox"] = Throttled()
    result = env.run("mail inbox")
    assert not result.ok and "GRAPH_THROTTLED: retry after 12s" in result.prompt_content


# ─── send / reply ───


@pytest.mark.parametrize("raw, body, code", [
    ("mail send a@x.com --subject Hi", None, "NO_BODY"),
    ("mail send a@x.com --subject Hi", "   ", "NO_BODY"),
    ("mail send --subject Hi", "text", "NO_RECIPIENT"),
    ("mail send --cc c@x.com --subject Hi", "text", "NO_RECIPIENT"),
    ("mail send a@x.com --cc '' --subject Hi", "text", "EMPTY_RECIPIENT"),
    ("mail send alice bob@x.com --subject Hi", "text", "AMBIGUOUS_RECIPIENTS"),
    ("mail send a@x.com --cc c@x.com d@x.com --subject Hi", "text", "AMBIGUOUS_RECIPIENTS"),
    ("mail send a@x.com --subject Hi --subject Again", "text", "USAGE"),
    ("mail send a@x.com", "text", "NO_SUBJECT"),
    ("mail send not-an-address@ --subject Hi", "text", "INVALID_ADDRESS"),
    ("mail send a@x.com,,b@x.com --subject Hi", "text", "EMPTY_RECIPIENT"),
])
def test_send_validation_sends_nothing(env, raw, body, code) -> None:
    result = env.run(raw, body)
    assert not result.ok and code in result.prompt_content, result.prompt_content
    assert env.state["mailbox"].calls == []


def test_send_to_addresses_echoes_who_it_went_to(env) -> None:
    result = env.run("mail send a@x.com,b@x.com --cc c@x.com --subject 'Daily report'", "The report.")
    assert result.ok, result.prompt_content
    assert env.state["mailbox"].calls == [("send", ["a@x.com", "b@x.com"], ["c@x.com"], "Daily report", "<div><p>The report.</p>\n</div>")]
    assert "To: a@x.com, b@x.com\nCc: c@x.com\nSubject: Daily report" in result.prompt_content


def test_a_sent_table_carries_the_configured_inline_styles(env) -> None:
    result = env.run("mail send a@x.com --subject Roster", "| Name | Role |\n|---|---|\n| Gene | CEO |")
    assert result.ok, result.prompt_content
    body_html = env.state["mailbox"].calls[0][4]
    assert f'<table style="{DEFAULTS.html_table_style}">' in body_html
    assert f'<th style="{DEFAULTS.html_header_cell_style}">Name</th>' in body_html
    assert f'<td style="{DEFAULTS.html_cell_style}">Gene</td>' in body_html


def _with_others() -> FakeMailbox:
    """A message to the agent and Gene, cc Kseniia (and the sender again, in another case)."""
    return FakeMailbox(
        to=[graph.Address("Agent", "REPORTS@contoso.com"), graph.Address("Gene", "gene@x.com")],
        cc=[graph.Address("Kseniia", "kseniia@x.com"), graph.Address("", "ALICE@x.com")],
    )


def test_reply_and_reply_all(env) -> None:
    env.state["mailbox"] = _with_others()
    env.run("mail inbox")
    short = ids.short_id("GRAPH-ID-1")
    assert not env.run(f"mail reply {short}").ok  # no body
    one = env.run(f"mail reply {short}", "**Thanks**\n- one")
    everyone = env.run(f"mail reply {short} --all", "Thanks all")
    assert one.ok and everyone.ok
    replies = [call for call in env.state["mailbox"].calls if call[0] == "reply"]
    assert replies == [
        ("reply", "inbox", "GRAPH-ID-1", "<div><p><strong>Thanks</strong></p>\n<ul>\n<li>one</li>\n</ul>\n</div>", False),
        ("reply", "inbox", "GRAPH-ID-1", "<div><p>Thanks all</p>\n</div>", True),
    ]
    assert "sent reply to Alice Doe <alice@x.com>\n" in one.prompt_content
    assert "Not included: Gene <gene@x.com>, Kseniia <kseniia@x.com>" in one.prompt_content
    assert one.data["to"] == ["alice@x.com"]
    assert "sent reply to Alice Doe <alice@x.com>, Gene <gene@x.com>, Kseniia <kseniia@x.com>" in everyone.prompt_content
    assert "Not included" not in everyone.prompt_content
    assert everyone.data["to"] == ["alice@x.com", "gene@x.com", "kseniia@x.com"]


def test_a_sender_only_reply_with_no_one_else_has_no_not_included_line(env) -> None:
    env.run("mail inbox")
    result = env.run(f"mail reply {ids.short_id('GRAPH-ID-1')}", "Thanks")
    assert result.ok and "sent reply to Alice Doe <alice@x.com>" in result.prompt_content
    assert "Not included" not in result.prompt_content


def test_read_shows_who_each_reply_form_reaches(env) -> None:
    env.state["mailbox"] = _with_others()
    env.run("mail inbox")
    short = ids.short_id("GRAPH-ID-1")
    content = env.run(f"mail read {short}").prompt_content
    assert f"mail reply {short}        → Alice Doe <alice@x.com>" in content
    assert f"mail reply {short} --all  → Alice Doe <alice@x.com>, Gene <gene@x.com>, Kseniia <kseniia@x.com>" in content
    reply_section = content.split("REPLY", 1)[1]
    assert "contoso.com" not in reply_section.lower()


def test_read_shows_only_the_sender_form_when_no_one_else_is_on_it(env) -> None:
    env.run("mail inbox")
    short = ids.short_id("GRAPH-ID-1")
    content = env.run(f"mail read {short}").prompt_content
    assert f"mail reply {short}        → Alice Doe <alice@x.com>" in content
    assert "--all" not in content


# ─── ids ───


def test_a_corrupt_id_map_is_an_error_not_an_empty_map(env) -> None:
    path = env.tmp / "message_ids" / "agent-1.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    result = env.run("mail read m12345678")
    assert not result.ok and "ID_MAP_UNREADABLE" in result.prompt_content


# ─── contacts (revision 3) ───


def test_add_one_with_a_name_and_several_without(env) -> None:
    assert env.run('mail contacts add alice@contoso.com --name "Alice Doe"').ok
    result = env.run("mail contacts add bob@x.com,carol@x.com")
    assert result.ok and "added bob@x.com" in result.prompt_content and "added carol@x.com" in result.prompt_content
    listed = env.run("mail contacts")
    assert "Alice Doe <alice@contoso.com>" in listed.prompt_content


def test_a_case_insensitive_duplicate_updates_rather_than_duplicates(env) -> None:
    env.run("mail contacts add Alice@Contoso.com")
    result = env.run('mail contacts add alice@contoso.com --name "Alice Doe"')
    assert "updated Alice Doe <Alice@Contoso.com>" in result.prompt_content
    assert [c.address for c in _book(env).list()] == ["Alice@Contoso.com"]


def test_an_invalid_address_saves_nothing(env) -> None:
    result = env.run("mail contacts add good@x.com,bad-address")
    assert not result.ok and "INVALID_ADDRESS: bad-address" in result.prompt_content
    assert _book(env).list() == []


def test_name_with_two_addresses_is_an_error(env) -> None:
    result = env.run("mail contacts add a@x.com,b@x.com --name Team")
    assert not result.ok and "NAME_NEEDS_ONE_ADDRESS" in result.prompt_content
    assert _book(env).list() == []


def test_removing_an_unknown_address_removes_nothing(env) -> None:
    env.run("mail contacts add a@x.com")
    result = env.run("mail contacts remove a@x.com,zed@x.com")
    assert not result.ok and "NOT_IN_CONTACTS: zed@x.com" in result.prompt_content
    assert [c.address for c in _book(env).list()] == ["a@x.com"]
    assert env.run("mail contacts remove A@X.com").ok
    assert _book(env).list() == []


def test_list_sorting_and_the_empty_message(env) -> None:
    assert "No saved contacts. Add one with: mail contacts add <addr>" in env.run("mail contacts list").prompt_content
    env.run("mail contacts add zed@x.com --name Zed")
    env.run("mail contacts add amy@x.com")
    env.run("mail contacts add bob@x.com --name 'Bob Ray'")
    lines = env.run("mail contacts").prompt_content
    order = [lines.index(text) for text in ("amy@x.com", "Bob Ray <bob@x.com>", "Zed <zed@x.com>")]
    assert order == sorted(order)


def test_the_store_survives_a_new_contact_book(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    contacts_mod.ContactBook(path).add(["a@x.com"], "Amy")
    assert contacts_mod.ContactBook(path).list() == [contacts_mod.Contact(address="a@x.com", name="Amy")]


# ─── send to contacts by name (revision 4) ───


def _seed(env) -> None:
    for raw in ('mail contacts add alice@contoso.com --name "Alice Doe"',
                'mail contacts add ops@contoso.com --name "Ops Team"',
                'mail contacts add bob@x.com --name "Bob Alice"'):
        assert env.run(raw).ok, raw


def test_resolution_by_full_name_and_first_name_ignores_case(env) -> None:
    _seed(env)
    book = _book(env)
    assert book.resolve("alice doe").address == "alice@contoso.com"
    assert book.resolve("OPS").address == "ops@contoso.com"


def test_a_full_name_match_wins_over_another_contacts_first_name(env) -> None:
    _seed(env)
    env.run('mail contacts add al@x.com --name "Bob"')
    # "bob" is Bob's full name and Bob Alice's first word: the full name wins.
    assert _book(env).resolve("bob").address == "al@x.com"


def test_an_ambiguous_first_name_lists_both_matches(env) -> None:
    _seed(env)
    env.run('mail contacts add asmith@x.com --name "Alice Smith"')
    result = env.run("mail send alice --subject Hi", "text")
    assert not result.ok
    assert ('AMBIGUOUS_CONTACT: "alice" matches Alice Doe <alice@contoso.com>, Alice Smith <asmith@x.com>'
            in result.prompt_content)
    assert env.state["mailbox"].calls == []


def test_an_unknown_name_sends_nothing(env) -> None:
    _seed(env)
    result = env.run("mail send alice@contoso.com,nobody --subject Hi", "text")
    assert not result.ok
    assert 'UNKNOWN_CONTACT: "nobody" — run "mail contacts" to see saved names, or use the address' in result.prompt_content
    assert env.state["mailbox"].calls == []


def test_a_quoted_multi_word_name_in_a_comma_list_with_mixed_cc(env) -> None:
    _seed(env)
    result = env.run('mail send "Alice Doe",new@x.com --cc ops --subject "RCA: login outage"', "RCA text")
    assert result.ok, result.prompt_content
    assert env.state["mailbox"].calls == [("send", ["alice@contoso.com", "new@x.com"], ["ops@contoso.com"], "RCA: login outage", "<div><p>RCA text</p>\n</div>")]
    assert "To: Alice Doe <alice@contoso.com>, new@x.com\nCc: Ops Team <ops@contoso.com>" in result.prompt_content


def test_an_address_not_in_contacts_still_sends(env) -> None:
    assert env.run("mail send stranger@x.com --subject Hi", "text").ok
    assert env.state["mailbox"].calls[0][1] == ["stranger@x.com"]


def test_a_name_and_its_own_address_are_sent_once(env) -> None:
    _seed(env)
    assert env.run('mail send "Alice Doe",ALICE@contoso.com --subject Hi', "text").ok
    assert env.state["mailbox"].calls[0][1] == ["alice@contoso.com"]


def test_the_same_person_in_to_and_cc_is_dropped_from_cc_and_reported(env) -> None:
    _seed(env)
    result = env.run("mail send alice --cc alice@contoso.com,ops --subject Hi", "text")
    assert result.ok, result.prompt_content
    assert env.state["mailbox"].calls[0][1:3] == (["alice@contoso.com"], ["ops@contoso.com"])
    assert "Also in To, so not repeated in Cc: Alice Doe <alice@contoso.com>" in result.prompt_content
    assert result.data["cc"] == ["ops@contoso.com"]


# ─── frictionless recipient entry (revision R2), through real shell tokenization ───


def _sent(env) -> tuple[list[str], list[str]]:
    calls = [call for call in env.state["mailbox"].calls if call[0] == "send"]
    assert len(calls) == 1, env.state["mailbox"].calls
    return calls[0][1], calls[0][2]


@pytest.mark.parametrize("raw", [
    "mail send a@x.com, b@x.com --subject s",
    "mail send a@x.com b@x.com --subject s",
    "mail send a@x.com;b@x.com --subject s",
    "mail send a@x.com ; b@x.com, --subject s",
    "mail send a@x.com --to b@x.com --subject s",
    "mail send --to a@x.com --to b@x.com --subject s",
    "mail send --subject s a@x.com ,b@x.com",
])
def test_every_unambiguous_to_list_reaches_both(env, raw: str) -> None:
    result = env.run(raw, "text")
    assert result.ok, result.prompt_content
    assert _sent(env) == (["a@x.com", "b@x.com"], [])


def test_name_and_address_exactly_as_mail_read_shows_them(env) -> None:
    _seed(env)
    result = env.run("mail send Gene Whiddon <g@x.com>, alice --subject s", "text")
    assert result.ok, result.prompt_content
    assert _sent(env) == (["g@x.com", "alice@contoso.com"], [])
    assert "To: Gene Whiddon <g@x.com>, Alice Doe <alice@contoso.com>" in result.prompt_content


def test_an_unquoted_multi_word_contact_name_in_a_comma_list(env) -> None:
    _seed(env)
    result = env.run("mail send Alice Doe, Ops Team --subject s", "text")
    assert result.ok, result.prompt_content
    assert _sent(env) == (["alice@contoso.com", "ops@contoso.com"], [])


def test_a_comma_continued_cc_lands_entirely_in_cc(env) -> None:
    result = env.run("mail send t@x.com --cc a@x.com, b@x.com --subject s", "text")
    assert result.ok, result.prompt_content
    assert _sent(env) == (["t@x.com"], ["a@x.com", "b@x.com"])


def test_repeated_cc_and_to_accumulate(env) -> None:
    result = env.run(
        "mail send t@x.com --to u@x.com --cc a@x.com --cc 'Bea <b@x.com>' --subject s", "text"
    )
    assert result.ok, result.prompt_content
    assert _sent(env) == (["t@x.com", "u@x.com"], ["a@x.com", "b@x.com"])
    assert "Cc: a@x.com, Bea <b@x.com>" in result.prompt_content


def test_an_unknown_name_in_a_list_sends_nothing(env) -> None:
    result = env.run("mail send a@x.com, Nobody Known --subject s", "text")
    assert not result.ok and 'UNKNOWN_CONTACT: "Nobody Known"' in result.prompt_content
    assert env.state["mailbox"].calls == []


def test_an_unquoted_cc_name_with_spaces_is_ambiguous_not_misrouted(env) -> None:
    result = env.run("mail send t@x.com --cc Gene Whiddon <g@x.com> --subject s", "text")
    assert not result.ok and "AMBIGUOUS_RECIPIENTS" in result.prompt_content
    assert env.state["mailbox"].calls == []


def test_a_repeated_non_repeatable_flag_is_a_usage_error(env) -> None:
    result = env.run("mail inbox --limit 1 --limit 2")
    assert not result.ok and "USAGE" in result.prompt_content and "(--limit given twice)" in result.prompt_content
    assert env.state["mailbox"].calls == []


def test_contacts_add_takes_a_comma_list_with_spaces(env) -> None:
    result = env.run("mail contacts add a@x.com, b@x.com; c@x.com")
    assert result.ok, result.prompt_content
    assert [c.address for c in _book(env).list()] == ["a@x.com", "b@x.com", "c@x.com"]


def test_contacts_add_saves_the_name_written_with_the_address(env) -> None:
    result = env.run("mail contacts add Gene Whiddon <g@x.com>, plain@x.com")
    assert result.ok, result.prompt_content
    assert _book(env).list() == [
        contacts_mod.Contact(address="g@x.com", name="Gene Whiddon"),
        contacts_mod.Contact(address="plain@x.com", name=None),
    ]


def test_contacts_add_with_one_bad_address_saves_nothing(env) -> None:
    result = env.run("mail contacts add Gene <g@x.com>, bad-address")
    assert not result.ok and "INVALID_ADDRESS: bad-address" in result.prompt_content
    assert _book(env).list() == []


def test_contacts_remove_by_name_and_address_form(env) -> None:
    env.run("mail contacts add a@x.com, g@x.com")
    result = env.run("mail contacts remove Gene Whiddon <g@x.com>; a@x.com")
    assert result.ok, result.prompt_content
    assert _book(env).list() == []


@pytest.mark.parametrize("name", ["a@b", "Doe, Alice", "   "])
def test_a_name_with_at_or_comma_or_blank_is_invalid(env, name: str) -> None:
    result = env.run(f"mail contacts add x@y.com --name '{name}'")
    assert not result.ok and "INVALID_NAME" in result.prompt_content
    assert _book(env).list() == []


# ─── inbox management (revision R4): search, sent, archive ───


def _at(hour: int) -> datetime:
    return datetime(2026, 9, 28, hour, 0, tzinfo=timezone.utc)


def _with_hits(env) -> object:
    mailbox = env.state["mailbox"]
    mailbox.hits = {
        "inbox": [_summary(1, received=_at(14), prefix="IN"), _summary(2, received=_at(9), prefix="IN")],
        "archive": [_summary(1, received=_at(14), prefix="AR", read=True), _summary(2, received=_at(12), prefix="AR")],
    }
    return mailbox


def test_search_merges_inbox_and_archive_newest_first_and_cuts_to_the_limit(env) -> None:
    mailbox = _with_hits(env)
    result = env.run("mail search leads update --limit 3")
    assert result.ok, result.prompt_content
    assert mailbox.calls == [("search", "inbox", "leads update", 3), ("search", "archive", "leads update", 3)]
    lines = result.prompt_content.splitlines()
    header = next(line for line in lines if line.startswith("Search "))
    assert header == ('Search "leads update" in inbox and archive — 3 matches shown, newest first '
                      "(up to --limit 3; narrow the search to see others; times UTC; ● unread)")
    shown = [line for line in lines if line.startswith("[m")]
    # Equal times: inbox first. IN-2 (09:00) is cut by the limit.
    assert shown == [
        f'[{ids.short_id("IN-1")}] ● 2026-09-28 14:00  Alice Doe <alice@x.com>  Subject 1 — "Hello there"',
        f'[{ids.short_id("AR-1")}] ○ 2026-09-28 14:00  Alice Doe <alice@x.com>  Subject 1 — "Hello there" (archive)',
        f'[{ids.short_id("AR-2")}] ● 2026-09-28 12:00  Alice Doe <alice@x.com>  Subject 2 — "Hello there" (archive)',
    ]


def test_search_ids_read_and_reply_in_their_own_folder(env) -> None:
    mailbox = _with_hits(env)
    env.run("mail search leads")
    archived = ids.short_id("AR-2")
    read = env.run(f"mail read {archived}")
    assert read.ok and "Folder: Archive" in read.prompt_content and "REPLY" in read.prompt_content
    assert ("get", "archive", "AR-2") in mailbox.calls and ("mark_read", "archive", "AR-2") in mailbox.calls
    assert env.run(f"mail reply {archived}", "Thanks").ok
    assert mailbox.calls[-1] == ("reply", "archive", "AR-2", "<div><p>Thanks</p>\n</div>", False)
    assert env.run(f"mail read {ids.short_id('IN-1')}").data["folder"] == "inbox"


def test_search_keeps_a_quoted_phrase_and_kql_properties(env) -> None:
    mailbox = _with_hits(env)
    result = env.run('mail search from:gene subject:"weekly report" "Re: notes"')
    assert result.ok, result.prompt_content
    assert mailbox.calls[0] == ("search", "inbox", 'from:gene subject:"weekly report" "Re: notes"', DEFAULTS.inbox_default_limit)


@pytest.mark.parametrize("raw, code", [
    ("mail search", "NO_QUERY"),
    ("mail search --limit 5", "NO_QUERY"),
    ("mail search x --limit 0", "LIMIT_OUT_OF_RANGE"),
    ("mail search x --skip 5", "USAGE"),
])
def test_search_refusals_search_nothing(env, raw: str, code: str) -> None:
    result = env.run(raw)
    assert not result.ok and code in result.prompt_content, result.prompt_content
    assert env.state["mailbox"].calls == []


def test_search_with_no_matches_says_so(env) -> None:
    result = env.run("mail search nothing")
    assert result.ok and 'Search "nothing" in inbox and archive — no matches.' in result.prompt_content


def test_a_missing_archive_fails_the_search_rather_than_narrowing_it(env) -> None:
    class NoArchive(FakeMailbox):
        def search(self, folder, query, top):
            if folder == "archive":
                raise graph.GraphHttpError(404, "ErrorFolderNotFound", "The folder was not found.")
            return super().search(folder, query, top)

    env.state["mailbox"] = NoArchive()
    result = env.run("mail search leads")
    assert not result.ok
    assert ("ARCHIVE_NOT_FOUND: the archive folder could not be searched "
            "(HTTP 404 ErrorFolderNotFound: The folder was not found.)") in result.prompt_content


def test_sent_lists_with_counts_and_more(env) -> None:
    env.state["mailbox"].has_more = True
    result = env.run("mail sent --limit 2")
    assert result.ok, result.prompt_content
    assert env.state["mailbox"].calls == [("list_sent", 2, 0), ("counts", "sentitems")]
    assert ("Sent from reports@contoso.com — 312 messages. Showing 1–2, newest first (times UTC)"
            in result.prompt_content)
    assert (f'[{ids.short_id("SENT-1")}] 2026-09-28 16:05  to Jordan <jordan@contoso.com>  Report 1 — "Numbers attached"'
            in result.prompt_content)
    assert "More: mail sent --limit 2 --skip 2" in result.prompt_content


def test_reading_a_sent_id_does_not_mark_it_and_has_no_reply_section(env) -> None:
    env.run("mail sent")
    result = env.run(f"mail read {ids.short_id('SENT-1')}")
    assert result.ok, result.prompt_content
    for line in ("Folder: Sent", "From: reports@contoso.com", "To: Jordan <jordan@contoso.com>", "Cc: Gene <gene@x.com>",
                 "Date: 2026-09-28 16:05 UTC", "Subject: Report 1", "Sent text"):
        assert line in result.prompt_content
    assert "REPLY" not in result.prompt_content
    calls = env.state["mailbox"].calls
    assert ("get_sent", "SENT-1") in calls and not any(call[0] in ("mark_read", "get") for call in calls)


def test_replying_to_a_sent_id_sends_nothing(env) -> None:
    env.run("mail sent")
    result = env.run(f"mail reply {ids.short_id('SENT-1')}", "Hello again")
    assert not result.ok and "CANNOT_REPLY_TO_SENT: use mail send to write to them" in result.prompt_content
    assert not any(call[0] in ("reply", "send") for call in env.state["mailbox"].calls)


def test_archive_moves_several_and_reports_new_readable_ids(env) -> None:
    mailbox = env.state["mailbox"]
    env.run("mail inbox")
    one, two = ids.short_id("GRAPH-ID-1"), ids.short_id("GRAPH-ID-2")
    result = env.run(f"mail archive {one}, {two}")
    assert result.ok, result.prompt_content
    new_one, new_two = ids.short_id("ARCH-GRAPH-ID-1"), ids.short_id("ARCH-GRAPH-ID-2")
    assert f"archived {one} → now {new_one} (archive)\narchived {two} → now {new_two} (archive)" in result.prompt_content
    assert [call for call in mailbox.calls if call[0] == "move"] == [("move", "GRAPH-ID-1"), ("move", "GRAPH-ID-2")]
    read = env.run(f"mail read {new_one}")
    assert read.ok and "Folder: Archive" in read.prompt_content
    assert ("get", "archive", "ARCH-GRAPH-ID-1") in mailbox.calls


def test_archive_refuses_a_non_inbox_id_and_moves_nothing(env) -> None:
    env.run("mail inbox")
    env.run("mail sent")
    result = env.run(f"mail archive {ids.short_id('GRAPH-ID-1')},{ids.short_id('SENT-1')}")
    assert not result.ok
    assert f"NOT_IN_INBOX: {ids.short_id('SENT-1')} is in sent" in result.prompt_content
    assert not any(call[0] == "move" for call in env.state["mailbox"].calls)


def test_archive_of_an_unknown_id_moves_nothing(env) -> None:
    env.run("mail inbox")
    result = env.run(f"mail archive {ids.short_id('GRAPH-ID-1')},m00000000")
    assert not result.ok and "UNKNOWN_MESSAGE_ID: m00000000" in result.prompt_content
    assert not any(call[0] == "move" for call in env.state["mailbox"].calls)


def test_a_failure_mid_archive_reports_what_moved_and_what_did_not(env) -> None:
    mailbox = FakeMailbox([_summary(1), _summary(2), _summary(3)])
    mailbox.move_failures["GRAPH-ID-2"] = graph.GraphHttpError(429, "TooManyRequests", "slow down", retry_after=5)
    env.state["mailbox"] = mailbox
    env.run("mail inbox")
    one, two, three = (ids.short_id(f"GRAPH-ID-{n}") for n in (1, 2, 3))
    result = env.run(f"mail archive {one},{two},{three}")
    assert not result.ok
    assert result.data["error"] == "\n".join([
        f"GRAPH_THROTTLED: {two} was not archived — retry after 5s (HTTP 429 TooManyRequests)",
        f"archived {one} → now {ids.short_id('ARCH-GRAPH-ID-1')} (archive)",
        f"Not attempted: {three}",
    ])
    assert [call for call in mailbox.calls if call[0] == "move"] == [("move", "GRAPH-ID-1"), ("move", "GRAPH-ID-2")]


def test_a_failed_first_archive_names_the_graph_code(env) -> None:
    mailbox = env.state["mailbox"]
    mailbox.move_failures["GRAPH-ID-1"] = graph.GraphHttpError(404, "ErrorItemNotFound", "Not found.")
    env.run("mail inbox")
    result = env.run(f"mail archive {ids.short_id('GRAPH-ID-1')}")
    assert not result.ok
    assert result.data["error"].startswith(f"MESSAGE_NOT_FOUND: {ids.short_id('GRAPH-ID-1')} was not archived — ")
    assert result.data["error"].endswith("(HTTP 404 ErrorItemNotFound)")
