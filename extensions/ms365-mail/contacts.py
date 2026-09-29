"""Microsoft 365 Mailbox — an agent's saved contacts (plan revisions 3 and 4).

Kept by BossMod, not synced to Outlook (Outlook contacts would widen the
Graph scope beyond the inbox and sendMail). One JSON file per agent, written
atomically. Every rule is an explicit result: nothing is saved, removed or
sent on a guess.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# An address shape check (the same one the host uses for the mailbox field);
# the mail service is the authority on deliverability.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContactError(Exception):
    """Base for the contact rules; ``str()`` is the agent-facing message."""


class InvalidAddress(ContactError):
    """An address fails the email pattern."""

    def __init__(self, address: str) -> None:
        super().__init__(f"INVALID_ADDRESS: {address}")


class InvalidName(ContactError):
    """A ``--name`` is blank or contains ``@`` or ``,``."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"INVALID_NAME: {reason}")


class NameNeedsOneAddress(ContactError):
    """``--name`` was given with more or fewer than one address."""

    def __init__(self) -> None:
        super().__init__("NAME_NEEDS_ONE_ADDRESS: --name applies to exactly one address")


class NotInContacts(ContactError):
    """``remove`` named an address that is not saved."""

    def __init__(self, address: str) -> None:
        super().__init__(f"NOT_IN_CONTACTS: {address}")


class UnknownContact(ContactError):
    """A recipient name matches no saved contact."""

    def __init__(self, token: str) -> None:
        super().__init__(f'UNKNOWN_CONTACT: "{token}" — run "mail contacts" to see saved names, or use the address')


class AmbiguousContact(ContactError):
    """A recipient name matches more than one saved contact.

    Attributes:
        matches: The contacts it matched.
    """

    def __init__(self, token: str, matches: list["Contact"]) -> None:
        listed = ", ".join(contact.display() for contact in matches)
        super().__init__(f'AMBIGUOUS_CONTACT: "{token}" matches {listed} — use the full name or the address')
        self.matches = matches


class ContactBookError(Exception):
    """The contacts file is unreadable."""


@dataclass(frozen=True)
class Contact:
    """A saved address with an optional name."""

    address: str
    name: str | None = None

    def display(self) -> str:
        """``Name <address>``, or the bare address."""
        return f"{self.name} <{self.address}>" if self.name else self.address


@dataclass(frozen=True)
class AddResult:
    """What ``add`` did: new contacts, and existing ones whose entry was updated."""

    added: list[Contact]
    updated: list[Contact]


class ContactBook:
    """One agent's saved contacts.

    Args:
        path: The JSON file (its folder is created on first write).
    """

    _locks: dict[Path, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: Path) -> None:
        self._path = path
        with ContactBook._locks_guard:
            self._lock = ContactBook._locks.setdefault(path, threading.Lock())

    def list(self) -> list[Contact]:
        """Return every contact, sorted by name (unnamed by address), then address.

        Raises:
            ContactBookError: The file is unreadable.
        """
        with self._lock:
            contacts = self._read()
        return sorted(contacts, key=lambda c: ((c.name or c.address).lower(), c.address.lower()))

    def add(self, addresses: Iterable[str], name: str | None) -> AddResult:
        """Save addresses; a case-insensitive duplicate updates the saved entry.

        The address is kept as typed (the local part is case-sensitive by the
        RFC). An existing entry takes the new ``name`` when one is given.

        Raises:
            InvalidAddress: Any address fails the pattern (nothing is saved).
            NameNeedsOneAddress: ``name`` given with other than one address.
            InvalidName: ``name`` is blank or contains ``@`` or ``,``.
            ContactBookError: The file is unreadable.
        """
        addrs = [address.strip() for address in addresses]
        for address in addrs:
            if not EMAIL_RE.match(address):
                raise InvalidAddress(address)
        if name is not None:
            if len(addrs) != 1:
                raise NameNeedsOneAddress()
            name = _valid_name(name)
        added: list[Contact] = []
        updated: list[Contact] = []
        with self._lock:
            contacts = self._read()
            for address in addrs:
                index = _index(contacts, address)
                if index is None:
                    contact = Contact(address=address, name=name)
                    contacts.append(contact)
                    added.append(contact)
                else:
                    kept = contacts[index]
                    contact = Contact(address=kept.address, name=name if name is not None else kept.name)
                    contacts[index] = contact
                    updated.append(contact)
            self._write(contacts)
        return AddResult(added=added, updated=updated)

    def remove(self, addresses: Iterable[str]) -> list[str]:
        """Remove saved addresses (matched case-insensitively).

        Returns:
            The addresses removed, as they were saved.

        Raises:
            NotInContacts: Any address is not saved (nothing is removed).
            ContactBookError: The file is unreadable.
        """
        addrs = [address.strip() for address in addresses]
        with self._lock:
            contacts = self._read()
            for address in addrs:
                if _index(contacts, address) is None:
                    raise NotInContacts(address)
            removed: list[str] = []
            for address in addrs:
                index = _index(contacts, address)
                if index is not None:
                    removed.append(contacts.pop(index).address)
            self._write(contacts)
        return removed

    def resolve(self, token: str) -> Contact:
        """Resolve one recipient token to a contact (pure; no Graph call).

        A token containing ``@`` is an address and need not be saved. Any
        other token matches saved names case-insensitively: the full name
        first, and only when no full name matches, the first word of names.

        Raises:
            InvalidAddress: An address token fails the pattern.
            UnknownContact: No name matches.
            AmbiguousContact: More than one contact matches at the step that matched.
            ContactBookError: The file is unreadable.
        """
        text = token.strip()
        if "@" in text:
            if not EMAIL_RE.match(text):
                raise InvalidAddress(text)
            with self._lock:
                saved = self._read()
            index = _index(saved, text)
            return saved[index] if index is not None else Contact(address=text)
        if not text:
            raise UnknownContact(token)
        wanted = text.lower()
        with self._lock:
            named = [contact for contact in self._read() if contact.name]
        full = [contact for contact in named if contact.name.lower() == wanted]
        matches = full or [contact for contact in named if contact.name.split()[0].lower() == wanted]
        if not matches:
            raise UnknownContact(text)
        if len(matches) > 1:
            raise AmbiguousContact(text, sorted(matches, key=lambda c: (c.name.lower(), c.address.lower())))
        return matches[0]

    def _read(self) -> list[Contact]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ContactBookError(f"cannot read {self._path.name}: {exc.strerror or exc}") from exc
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContactBookError(f"{self._path.name} is not JSON") from exc
        if not isinstance(items, list):
            raise ContactBookError(f"{self._path.name} is not a list")
        contacts: list[Contact] = []
        for item in items:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("address"), str)
                or not isinstance(item.get("name"), (str, type(None)))
            ):
                raise ContactBookError(f"{self._path.name} has an entry that is not {{address, name}}")
            contacts.append(Contact(address=item["address"], name=item["name"]))
        return contacts

    def _write(self, contacts: list[Contact]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(
            json.dumps([{"address": c.address, "name": c.name} for c in contacts], ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)


def _index(contacts: list[Contact], address: str) -> int | None:
    wanted = address.lower()
    return next((i for i, contact in enumerate(contacts) if contact.address.lower() == wanted), None)


def _valid_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise InvalidName("the name is empty")
    if "@" in cleaned or "," in cleaned:
        raise InvalidName("a name cannot contain @ or ,")
    return cleaned
