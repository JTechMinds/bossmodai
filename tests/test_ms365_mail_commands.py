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


def _summary(n: int, *, read: bool = False, preview: str = "Hello there") -> object:
    return graph.MessageSummary(
        id=f"GRAPH-ID-{n}", subject=f"Subject {n}", sender=graph.Address("Alice Doe", "alice@x.com"),
        received=datetime(2026, 9, 29, 9, 14, tzinfo=timezone.utc), is_read=read, preview=preview,
        has_attachments=False,
    )


class FakeMailbox:
    """A MailboxLike that records every call."""

    mailbox = "reports@contoso.com"

    def __init__(self, messages=None, has_more=False, to=None, cc=None) -> None:
        self.messages = messages if messages is not None else [_summary(1), _summary(2, read=True)]
        self.has_more = has_more
        self.to = to if to is not None else [graph.Address("", "reports@contoso.com")]
        self.cc = cc if cc is not None else []
        self.calls: list[tuple] = []

    def list_inbox(self, top, skip, unread_only):
        self.calls.append(("list", top, skip, unread_only))
        found = [m for m in self.messages if not (unread_only and m.is_read)]
        return graph.InboxPage(messages=found[:top], has_more=self.has_more)

    def get_message(self, message_id):
        self.calls.append(("get", message_id))
        summary = next(m for m in self.messages if m.id == message_id)
        return graph.Message(
            id=summary.id, subject=summary.subject, sender=summary.sender,
            to=self.to, cc=self.cc, received=summary.received,
            is_read=summary.is_read, has_attachments=False, body="Line one\r\nLine two",
        )

    def mark_read(self, message_id):
        self.calls.append(("mark_read", message_id))

    def send(self, to, cc, subject, body_html):
        self.calls.append(("send", [a.address for a in to], [a.address for a in cc], subject, body_html))

    def reply(self, message_id, body_html, reply_all):
        self.calls.append(("reply", message_id, body_html, reply_all))


@pytest.fixture()
def env(tmp_path: Path):
    state = {"mailbox": FakeMailbox()}
    runner = commands.MailCommands(
        mailbox_for=lambda agent_id: state["mailbox"],
        id_map_for=lambda agent_id: ids.IdMap(tmp_path / "ids" / f"{agent_id}.json", DEFAULTS.id_map_keep),
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
    assert "Inbox of reports@contoso.com — messages 1–2" in result.prompt_content
    assert env.state["mailbox"].calls == [("list", DEFAULTS.inbox_default_limit, 0, False)]


def test_inbox_unread_limit_and_skip(env) -> None:
    env.state["mailbox"].has_more = True
    result = env.run("mail inbox --unread --limit 1 --skip 3")
    assert result.ok
    assert env.state["mailbox"].calls == [("list", 1, 3, True)]
    assert "unread messages 4–4" in result.prompt_content
    assert "More: mail inbox --unread --limit 1 --skip 4" in result.prompt_content


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
    for line in ("From: Alice Doe <alice@x.com>", "To: reports@contoso.com", "Date: 2026-09-29 09:14 UTC",
                 "Subject: Subject 1", "Attachments: none", "Line one", "Line two"):
        assert line in result.prompt_content
    assert ("mark_read", "GRAPH-ID-1") in env.state["mailbox"].calls


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
    for raw in ("mail inbox", "mail read m1", "mail send a@x.com --subject s", "mail reply m1"):
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
    ("mail send --subject Hi", "text", "USAGE"),
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
    assert "sent to a@x.com, b@x.com; cc c@x.com" in result.prompt_content


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
        ("reply", "GRAPH-ID-1", "<div><p><strong>Thanks</strong></p>\n<ul>\n<li>one</li>\n</ul>\n</div>", False),
        ("reply", "GRAPH-ID-1", "<div><p>Thanks all</p>\n</div>", True),
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


def test_the_id_map_is_capped_and_survives_a_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "ids" / "a.json"
    first = ids.IdMap(path, keep=3)
    shorts = first.remember([f"G{n}" for n in range(5)])
    again = ids.IdMap(path, keep=3)
    assert again.resolve(shorts[4]) == "G4" and again.resolve(shorts[2]) == "G2"
    with pytest.raises(ids.UnknownMessageId):
        again.resolve(shorts[0])
    # Listing an old id again makes it the most recent.
    again.remember(["G2"])
    again.remember(["G5"])
    assert again.resolve(shorts[2]) == "G2"
    with pytest.raises(ids.UnknownMessageId):
        again.resolve(shorts[3])
    assert not list(path.parent.glob(".*.tmp"))


def test_short_ids_are_stable_hashes() -> None:
    assert ids.short_id("abc") == ids.short_id("abc")
    assert ids.short_id("abc") != ids.short_id("abd")
    assert len(ids.short_id("abc")) == 9 and ids.short_id("abc").startswith("m")


def test_a_corrupt_id_map_is_an_error_not_an_empty_map(env) -> None:
    path = env.tmp / "ids" / "agent-1.json"
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
    assert "sent to Alice Doe <alice@contoso.com>, new@x.com; cc Ops Team <ops@contoso.com>" in result.prompt_content


def test_an_address_not_in_contacts_still_sends(env) -> None:
    assert env.run("mail send stranger@x.com --subject Hi", "text").ok
    assert env.state["mailbox"].calls[0][1] == ["stranger@x.com"]


def test_a_name_and_its_own_address_are_sent_once(env) -> None:
    _seed(env)
    assert env.run('mail send "Alice Doe",ALICE@contoso.com --subject Hi', "text").ok
    assert env.state["mailbox"].calls[0][1] == ["alice@contoso.com"]


def test_the_same_person_in_to_and_cc_is_an_error(env) -> None:
    _seed(env)
    result = env.run("mail send alice --cc alice@contoso.com --subject Hi", "text")
    assert not result.ok and "RECIPIENT_TWICE: alice@contoso.com" in result.prompt_content
    assert env.state["mailbox"].calls == []


@pytest.mark.parametrize("name", ["a@b", "Doe, Alice", "   "])
def test_a_name_with_at_or_comma_or_blank_is_invalid(env, name: str) -> None:
    result = env.run(f"mail contacts add x@y.com --name '{name}'")
    assert not result.ok and "INVALID_NAME" in result.prompt_content
    assert _book(env).list() == []
