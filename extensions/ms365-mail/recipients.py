"""Microsoft 365 Mailbox — reading a recipient list the way an agent writes it.

Pure: text plus the agent's contact book in, contacts out; no Graph call and
no CLI concerns (``commands.py`` orchestrates). Every unambiguous way of
writing a list is accepted — entries separated by ``,`` or ``;``, addresses
separated by spaces, saved contact names (several words are fine), and
``Name <address>`` exactly as ``mail read`` shows people. Anything genuinely
ambiguous, such as a name and an address with only a space between them, is
refused with a coded error, so nothing is sent on a guess.
"""

from __future__ import annotations

import re

from .contacts import Contact, ContactBook, ContactError, InvalidAddress

_SEPARATORS = re.compile(r"[,;]")
# ``Name <address>`` or ``<address>``; the name may not contain brackets.
_BRACKETED = re.compile(r"^(?P<name>[^<>]*?)\s*<(?P<address>[^<>]*)>$")
_QUOTES = "\"'"


class RecipientError(ContactError):
    """A recipient list cannot be read; ``str()`` is the coded agent message.

    Codes: ``EMPTY_RECIPIENT`` (an empty entry between separators, or no
    entry at all) and ``AMBIGUOUS_RECIPIENTS`` (where one recipient ends and
    the next begins cannot be told).
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")


def split_entries(raw: str) -> list[str]:
    """Split a recipient list into entries on ``,`` and ``;``.

    Each entry is stripped. A trailing separator (``a@x.com,``) is ignored;
    an empty entry anywhere else (``a,,b`` or ``,a``) is refused rather than
    dropped, since it usually means something was lost.

    Args:
        raw: The list as written.

    Returns:
        The non-empty entries, in order.

    Raises:
        RecipientError: ``EMPTY_RECIPIENT`` for an empty entry before a
            separator, or when ``raw`` holds no entry at all.
    """
    parts = [part.strip() for part in _SEPARATORS.split(raw)]
    if len(parts) > 1 and not parts[-1]:
        parts.pop()
    if parts == [""]:
        raise RecipientError("EMPTY_RECIPIENT", f"{raw!r} names no recipient")
    if any(not part for part in parts):
        raise RecipientError("EMPTY_RECIPIENT", f"{raw!r} has an empty entry between separators")
    return parts


def parse_entry(entry: str) -> list[tuple[str | None, str]]:
    """Read one entry into ``(display_name, token)`` pairs.

    The token is what ``ContactBook.resolve`` takes: an address or a saved
    contact name.

    * ``Name <address>`` or ``<address>`` gives one pair with the address;
      quotes around the name are stripped (an empty name is ``None``).
    * Several whitespace-separated parts that all contain ``@`` give one pair
      per address.
    * Words mixed with an address (``alice bob@x.com``) are ambiguous.
    * Anything else is one token: an address, or a name that may contain
      spaces (``Alice Doe``).

    Args:
        entry: One entry from ``split_entries`` (already stripped).

    Returns:
        One or more ``(display_name | None, token)`` pairs.

    Raises:
        InvalidAddress: The brackets hold no address.
        RecipientError: ``AMBIGUOUS_RECIPIENTS`` for words mixed with an
            address, including a bracketed form whose name holds an address.
    """
    bracketed = _BRACKETED.match(entry)
    if bracketed is not None:
        address = bracketed.group("address").strip()
        if "@" not in address:
            raise InvalidAddress(address or entry)
        name = bracketed.group("name").strip()
        if len(name) >= 2 and name[0] == name[-1] and name[0] in _QUOTES:
            name = name[1:-1].strip()
        if "@" in name:
            # "a@x.com Gene <g@x.com>": two recipients with no separator.
            raise _ambiguous(entry)
        return [(name or None, address)]
    words = entry.split()
    if len(words) > 1 and "@" in entry:
        if all("@" in word for word in words):
            return [(None, word) for word in words]
        raise _ambiguous(entry)
    return [(None, entry)]


def resolve_list(book: ContactBook, raw: str) -> list[Contact]:
    """Resolve a whole recipient list to contacts (pure apart from reading the book).

    Every entry must resolve or nothing is returned. A display name written
    with an address (``Name <address>``) is kept for an address that has no
    saved name; a saved contact's stored name wins. The same address twice
    (case-insensitive, including a name and its own address) counts once,
    first-seen order kept.

    Args:
        book: The agent's contact book.
        raw: The list as written.

    Returns:
        The recipients, deduplicated.

    Raises:
        RecipientError: ``EMPTY_RECIPIENT`` or ``AMBIGUOUS_RECIPIENTS``.
        InvalidAddress: An address fails the pattern.
        UnknownContact: A name matches no saved contact.
        AmbiguousContact: A name matches more than one saved contact.
        ContactBookError: The contacts file is unreadable.
    """
    resolved: list[Contact] = []
    seen: set[str] = set()
    for entry in split_entries(raw):
        for display_name, token in parse_entry(entry):
            contact = book.resolve(token)
            if display_name and contact.name is None:
                contact = Contact(address=contact.address, name=display_name)
            key = contact.address.lower()
            if key not in seen:
                seen.add(key)
                resolved.append(contact)
    return resolved


def _ambiguous(entry: str) -> RecipientError:
    return RecipientError(
        "AMBIGUOUS_RECIPIENTS",
        f"{entry!r} — separate recipients with commas (a name with spaces needs no quotes then)",
    )
