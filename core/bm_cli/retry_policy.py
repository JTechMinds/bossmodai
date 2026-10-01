"""BossMod AI — The no-retry command list.

A failed turn is retried automatically unless it ran a command whose replay
is harmful (an email sent twice). Those commands are named, as data, in the
``cli_no_retry_commands`` setting: one command prefix per line, editable in
Settings → CLI Policy. This module parses that list and matches a parsed
command against it; ``core.bm_cli.runtime`` marks a result whose listed
command reached its handler (``BossModCliResult.blocks_retry``).
"""

from __future__ import annotations

from collections.abc import Iterable

from core.bm_cli.types import ParsedCliCommand
from db.crud import query_one

SETTING_KEY = "cli_no_retry_commands"


class NoRetryListError(ValueError):
    """The no-retry command list cannot be read: its settings row is missing.

    An empty value is not an error; it means nothing is listed.
    """


def parse_no_retry_list(raw: str) -> tuple[tuple[str, ...], ...]:
    """Parse the stored no-retry list into command prefixes.

    Pure. One entry per line; each line is trimmed, and a line that is blank
    after trimming is skipped (blank lines between entries are formatting,
    not data). Each entry is split on whitespace and lowercased, so
    ``"Mail  Send"`` becomes ``("mail", "send")``.

    Args:
        raw: The setting's stored value.

    Returns:
        One tuple of lowercased words per non-blank line, in order.
    """
    entries: list[tuple[str, ...]] = []
    for line in raw.splitlines():
        words = line.strip().lower().split()
        if words:
            entries.append(tuple(words))
    return tuple(entries)


def load_no_retry_list() -> tuple[tuple[str, ...], ...]:
    """Read and parse the no-retry list from the settings table, live.

    Read straight from the database on every call (no cache, no exception
    swallowing), so an operator's edit applies to the next command without a
    restart, and a database failure surfaces as itself. Called before a
    command runs, so a failure here never follows an outside effect.

    Returns:
        The parsed entries (see :func:`parse_no_retry_list`); ``()`` when the
        value is blank, the operator's choice to retry everything.

    Raises:
        NoRetryListError: The settings row is missing. There is no fallback
            list: a missing row must surface, not silently make every
            command retryable.
    """
    row = query_one("SELECT value FROM settings WHERE key = $1", [SETTING_KEY])
    if row is None:
        raise NoRetryListError(f"{SETTING_KEY} is missing")
    value = str(row["value"])  # settings.value is NOT NULL
    if not value.strip():
        return ()
    return parse_no_retry_list(value)


def blocks_retry(parsed: ParsedCliCommand, entries: Iterable[tuple[str, ...]]) -> bool:
    """Whether a parsed command is on the no-retry list.

    Pure. The command's words are its canonical name followed by its args,
    lowercased; it matches an entry when those words start with the entry's
    words. ``("mail", "send")`` matches ``mail send a@x.com --subject s`` but
    not ``mail read`` or ``mailx send``, and an entry longer than the command
    never matches.

    Args:
        parsed: The command as parsed by ``parse_cli_command``.
        entries: Parsed list entries (see :func:`parse_no_retry_list`).

    Returns:
        True when any entry is a prefix of the command's words.
    """
    words = tuple(word.lower() for word in (parsed.name, *parsed.args))
    return any(words[: len(entry)] == entry for entry in entries if entry)
