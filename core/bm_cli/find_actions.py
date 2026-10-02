"""What a ``find`` command runs or changes besides searching. Pure; no I/O.

``-exec``/``-execdir``/``-ok``/``-okdir CMD … ;|+`` run ``CMD``; ``-delete``
deletes; ``-fprint*``/``-fls FILE`` write ``FILE``. Each becomes a command
that policy and the approval gate evaluate next to the ``find`` itself
(see :mod:`core.bm_cli.effective_commands`).
"""

from __future__ import annotations

from dataclasses import dataclass

_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FILE_OUTPUT = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})
_LEADING_FLAGS = frozenset({"-H", "-L", "-P"})
_OPERATORS = frozenset({"(", ")", "!", "-not", "-a", "-and", "-o", "-or", ","})
# Expression tokens that are not tests, with how many values each takes.
# Anything else (``-name``, ``-type``, ``-newer`` …) filters what is found.
_NOT_TESTS: dict[str, int] = {
    "-delete": 0, "-print": 0, "-print0": 0, "-printf": 1, "-ls": 0,
    "-fprint": 1, "-fprint0": 1, "-fprintf": 2, "-fls": 1,
    "-depth": 0, "-maxdepth": 1, "-mindepth": 1, "-xdev": 0, "-mount": 0,
    "-follow": 0, "-H": 0, "-L": 0, "-P": 0, "-daystart": 0, "-noleaf": 0,
    "-ignore_readdir_race": 0, "-noignore_readdir_race": 0,
}


@dataclass(frozen=True)
class FindExtra:
    """One command a ``find`` runs on the side.

    Attributes:
        argv: The command's words.
        unwrap: True for an ``-exec``-style command, which may itself be a
            wrapper; False for the synthesized ``rm``/``tee``.
        filtered_starts: Operands of *argv* that stand for a start path of
            a find whose expression has test predicates. The find acts on
            matches under that path, not on the whole path, so such an
            operand is not a whole-root target.
    """

    argv: tuple[str, ...]
    unwrap: bool
    filtered_starts: frozenset[str] = frozenset()


def find_extras(args: tuple[str, ...], *, braces_to_paths: bool) -> list[FindExtra]:
    """Return every command a ``find`` with *args* runs on the side.

    Args:
        args: The ``find`` arguments (argv without ``find``).
        braces_to_paths: Replace ``{}`` in an ``-exec`` command with the
            start paths (for path facts); otherwise ``{}`` is kept (policy
            matches the command as written).

    Returns:
        The side commands in expression order: ``-exec`` commands,
        ``rm <start paths>`` for ``-delete`` and ``tee FILE`` for file
        output actions.
    """
    index = 0
    while index < len(args):
        token = args[index]
        if token in _LEADING_FLAGS or token.startswith("-O"):
            index += 1
        elif token == "-D":
            index += 2
        else:
            break
    paths: list[str] = []
    while index < len(args) and not args[index].startswith("-") and args[index] not in _OPERATORS:
        paths.append(args[index])
        index += 1
    starts = paths or ["."]  # GNU find searches "." when given no path
    expression = args[index:]
    filtered = frozenset(starts) if has_test_predicates(expression) else frozenset()
    extras: list[FindExtra] = []
    index = 0
    while index < len(expression):
        token = expression[index]
        if token in _EXEC:
            command, index = _exec_command(expression, index + 1)
            if command and braces_to_paths:
                extras.append(FindExtra(_fill_braces(command, starts), True, filtered))
            elif command:
                extras.append(FindExtra(tuple(command), True))
        elif token == "-delete":
            extras.append(FindExtra(("rm", *starts), False, filtered))
        elif token in _FILE_OUTPUT and index + 1 < len(expression):
            extras.append(FindExtra(("tee", expression[index + 1]), False))
            index += 1
        index += 1
    return extras


def has_test_predicates(expression: tuple[str, ...]) -> bool:
    """Return whether a ``find`` expression filters what it acts on.

    Actions (with their arguments and ``-exec`` commands), global and
    positional options, and operators are not tests; every other token is.

    Args:
        expression: The tokens after the start paths.

    Returns:
        True when any token is a test (``-name``, ``-type``, ``-path`` …).
    """
    index = 0
    while index < len(expression):
        token = expression[index]
        if token in _EXEC:
            _command, index = _exec_command(expression, index + 1)
        elif token in _OPERATORS:
            pass
        elif token in _NOT_TESTS:
            index += _NOT_TESTS[token]
        else:
            return True
        index += 1
    return False


def _exec_command(expression: tuple[str, ...], index: int) -> tuple[list[str], int]:
    """Read an ``-exec`` command from *index*; return it and its terminator index."""
    command: list[str] = []
    while index < len(expression) and expression[index] != ";" and not (
        expression[index] == "+" and command and command[-1] == "{}"
    ):
        command.append(expression[index])
        index += 1
    return command, index


def _fill_braces(command: list[str], starts: list[str]) -> tuple[str, ...]:
    filled: list[str] = []
    for word in command:
        filled.extend([word.replace("{}", start) for start in starts] if "{}" in word else [word])
    return tuple(filled)
