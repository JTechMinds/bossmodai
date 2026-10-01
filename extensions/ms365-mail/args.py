"""Microsoft 365 Mailbox — argument parsing for the ``mail`` subcommands (pure).

Splits the tokens after a verb into positionals, valued flags and switches,
with the comma-continuation rule recipient lists need. No mailbox, file or
network access happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Sequence


class CommandError(ValueError):
    """A usage or precondition error; the message goes to the agent as is."""


@dataclass(frozen=True)
class Args:
    """Parsed tokens: positionals, each valued flag's values in order, and the switches found."""

    positional: list[str]
    values: dict[str, list[str]]
    switches: set[str]

    def one(self, flag: str) -> str | None:
        """The value of a non-repeatable flag (``parse_args`` refuses it twice), or ``None``."""
        given = self.values.get(flag)
        return given[0] if given else None


def parse_args(
    args: Sequence[str],
    *,
    valued: AbstractSet[str],
    switches: AbstractSet[str],
    usage: str,
    repeatable: AbstractSet[str] = frozenset(),
    continues: AbstractSet[str] = frozenset(),
) -> Args:
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
    return Args(positional=positional, values=values, switches=found)


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


def int_flag(raw: str | None, flag: str, default: int) -> int:
    """A whole-number flag value, or ``default`` when the flag was not given.

    Raises:
        CommandError: ``USAGE`` when the value is not a whole number.
    """
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise CommandError(f"USAGE: {flag} takes a whole number, got {raw!r}") from exc
