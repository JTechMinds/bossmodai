"""Microsoft 365 Mailbox: reading a recipient list (``recipients.py``), pure."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_PACKAGE = import_package(get_discovery().get("ms365-mail"))
recipients = importlib.import_module(f"{_PACKAGE.__name__}.recipients")
contacts_mod = importlib.import_module(f"{_PACKAGE.__name__}.contacts")


@pytest.fixture()
def book(tmp_path: Path):
    book = contacts_mod.ContactBook(tmp_path / "contacts.json")
    book.add(["alice@contoso.com"], "Alice Doe")
    book.add(["ops@contoso.com"], "Ops Team")
    book.add(["plain@x.com"], None)
    return book


# ─── split_entries ───


@pytest.mark.parametrize("raw, entries", [
    ("a, b", ["a", "b"]),
    ("a;b", ["a", "b"]),
    (" a ; b , c ", ["a", "b", "c"]),
    ("a@x.com,", ["a@x.com"]),
    ("a; ", ["a"]),
    ("Alice Doe", ["Alice Doe"]),
])
def test_split_entries(raw: str, entries: list[str]) -> None:
    assert recipients.split_entries(raw) == entries


@pytest.mark.parametrize("raw", ["a,,b", "a;;b", "a, ;b", ",a", "", "  ", ","])
def test_an_empty_entry_is_refused_not_dropped(raw: str) -> None:
    with pytest.raises(recipients.RecipientError, match="^EMPTY_RECIPIENT: "):
        recipients.split_entries(raw)


# ─── parse_entry ───


@pytest.mark.parametrize("entry, pairs", [
    ("Gene Whiddon <g@x.com>", [("Gene Whiddon", "g@x.com")]),
    ('"Gene Whiddon" <g@x.com>', [("Gene Whiddon", "g@x.com")]),
    ("'Gene' <g@x.com>", [("Gene", "g@x.com")]),
    ("<g@x.com>", [(None, "g@x.com")]),
    ("Gene<g@x.com>", [("Gene", "g@x.com")]),
    ("a@x.com b@x.com", [(None, "a@x.com"), (None, "b@x.com")]),
    ("a@x.com", [(None, "a@x.com")]),
    ("Alice Doe", [(None, "Alice Doe")]),
    ("alice", [(None, "alice")]),
])
def test_parse_entry(entry: str, pairs: list[tuple]) -> None:
    assert recipients.parse_entry(entry) == pairs


@pytest.mark.parametrize("entry", ["alice bob@x.com", "bob@x.com alice", "a@x.com Gene <g@x.com>"])
def test_words_mixed_with_an_address_are_ambiguous(entry: str) -> None:
    with pytest.raises(recipients.RecipientError, match="^AMBIGUOUS_RECIPIENTS: "):
        recipients.parse_entry(entry)


def test_brackets_without_an_address_are_an_invalid_address() -> None:
    with pytest.raises(contacts_mod.InvalidAddress, match="^INVALID_ADDRESS: alice$"):
        recipients.parse_entry("Alice <alice>")


def test_recipient_errors_are_contact_errors() -> None:
    # The command layer reports every ContactError to the agent as is.
    assert issubclass(recipients.RecipientError, contacts_mod.ContactError)


# ─── resolve_list ───


def test_resolve_list_mixes_names_addresses_and_display_forms(book) -> None:
    resolved = recipients.resolve_list(book, "alice; Ops Team, Gene Whiddon <g@x.com>, x@y.com z@y.com")
    assert resolved == [
        contacts_mod.Contact("alice@contoso.com", "Alice Doe"),
        contacts_mod.Contact("ops@contoso.com", "Ops Team"),
        contacts_mod.Contact("g@x.com", "Gene Whiddon"),
        contacts_mod.Contact("x@y.com"),
        contacts_mod.Contact("z@y.com"),
    ]


def test_a_saved_name_wins_over_a_written_display_name(book) -> None:
    assert recipients.resolve_list(book, "Someone Else <ALICE@contoso.com>") == [
        contacts_mod.Contact("alice@contoso.com", "Alice Doe"),
    ]


def test_a_saved_address_without_a_name_takes_the_written_one(book) -> None:
    assert recipients.resolve_list(book, "Pat <plain@x.com>") == [contacts_mod.Contact("plain@x.com", "Pat")]


def test_duplicates_count_once_in_first_seen_order(book) -> None:
    resolved = recipients.resolve_list(book, "b@x.com, Alice Doe, B@X.com; alice@CONTOSO.com, <alice@contoso.com>")
    assert [contact.address for contact in resolved] == ["b@x.com", "alice@contoso.com"]


@pytest.mark.parametrize("raw, error, code", [
    ("alice, nobody", contacts_mod.UnknownContact, "UNKNOWN_CONTACT"),
    ("a@x.com, not-an-address@", contacts_mod.InvalidAddress, "INVALID_ADDRESS"),
    ("alice bob@x.com", recipients.RecipientError, "AMBIGUOUS_RECIPIENTS"),
    ("a@x.com,,b@x.com", recipients.RecipientError, "EMPTY_RECIPIENT"),
])
def test_resolve_list_refuses_the_whole_list(book, raw: str, error: type, code: str) -> None:
    with pytest.raises(error, match=f"^{code}: "):
        recipients.resolve_list(book, raw)
