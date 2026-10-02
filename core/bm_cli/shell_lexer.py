"""Tokenize agent script text, tracking quoting per character; never a shell.

Words come out with quotes removed but each character's quoting kept, so
the grammar (:mod:`core.bm_cli.shell_script`) can tell ``'*.py'`` from
``*.py`` and ``\\;`` from ``;``. Operators come out with the fd digit that
was written adjacent to them (``2>`` but not ``2 >``). Every construct a
shell would expand or interpret beyond the supported subset raises
:class:`ShellSyntaxError` with an agent-facing steer. Pure: no I/O.
"""

from __future__ import annotations

import glob
import re
import string
from collections.abc import Callable
from dataclasses import dataclass

_GLOB_CHARS = frozenset("*?[")
_OPERATOR_CHARS = frozenset("|&;<>()\n")
_BLANKS = frozenset(" \t")
# After ``$`` these start an expansion bash would perform (``$(`` is
# substitution; ``$'…'`` and ``$"…"`` are quoting forms, unquoted only).
_DQ_EXPANSION_STARTS = frozenset("({_?$!#@*-" + string.ascii_letters + string.digits)
_EXPANSION_STARTS = _DQ_EXPANSION_STARTS | frozenset("'\"")
# Inside double quotes a backslash escapes only these.
_DQ_ESCAPABLE = frozenset('$`"\\\n')

STEER_SUBSTITUTION = "command substitution: run the inner command first, then use its output"
STEER_VARIABLE = "variables are not expanded: write the value"
STEER_BACKGROUND = "background jobs are not supported"
STEER_SUBSHELL = "subshells and ( ) grouping are not supported: run the commands in sequence"
STEER_GROUP = "{ } command groups are not supported: run the commands in sequence"
STEER_BRACES = "brace expansion is not supported: write each word out"
STEER_HEREDOC = "heredocs (<<) are not supported: write the text to a file first, then use < file"
STEER_PROCESS_SUB = "process substitution is not supported: write the output to a file first"
STEER_FD = "only stdout (1) and stderr (2) can be redirected, with >, >>, 2>&1 or &>"
STEER_DUP = "only 2>&1 is supported for joining output streams"


class ShellSyntaxError(ValueError):
    """The text is outside the supported subset; ``steer`` tells the agent what to do.

    Attributes:
        steer: One agent-facing line naming the construct and the alternative.
    """

    def __init__(self, steer: str) -> None:
        super().__init__(steer)
        self.steer = steer


class ShellQuoteError(ShellSyntaxError):
    """An unterminated quote or a trailing backslash: the text is not words at all."""


@dataclass(frozen=True, slots=True)
class WordToken:
    """One word after quote removal.

    Attributes:
        text: The literal word.
        pattern: The word as a :mod:`glob` pattern (quoted glob characters escaped).
        glob: True only when an unquoted ``*``, ``?`` or ``[`` appears.
        quoted: Per character of ``text``, whether it was quoted.
    """

    text: str
    pattern: str
    glob: bool
    quoted: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class OpToken:
    """One operator: ``|`` ``||`` ``&&`` ``;`` newline ``>`` ``>>`` ``<`` ``&>`` or ``2>&1``.

    ``fd`` is the redirect's file descriptor (an adjacent ``1``/``2``
    before ``>``/``>>``, 1 when none was written, 0 for ``<``); None for
    connectors and ``&>``.
    """

    op: str
    fd: int | None = None


Token = WordToken | OpToken


def lex(text: str) -> list[Token]:
    """Split *text* into words and operators.

    Raises:
        ShellQuoteError: An unterminated quote or a trailing backslash.
        ShellSyntaxError: An unsupported construct, with its steer.
    """
    tokens: list[Token] = []
    chars: list[str] = []
    quoted: list[bool] = []
    in_word = False
    index = 0
    length = len(text)

    def add(char: str, is_quoted: bool) -> None:
        chars.append(char)
        quoted.append(is_quoted)

    def flush() -> None:
        nonlocal in_word
        if in_word:
            tokens.append(_word(chars, quoted))
        chars.clear()
        quoted.clear()
        in_word = False

    while index < length:
        char = text[index]
        if char in _BLANKS:
            flush()
            index += 1
        elif char == "'":
            end = text.find("'", index + 1)
            if end < 0:
                raise ShellQuoteError("unterminated single quote")
            for item in text[index + 1:end]:
                add(item, True)
            in_word = True
            index = end + 1
        elif char == '"':
            index = _double_quoted(text, index + 1, add)
            in_word = True
        elif char == "\\":
            if index + 1 >= length:
                raise ShellQuoteError("trailing backslash")
            if text[index + 1] != "\n":  # backslash-newline continues the line
                add(text[index + 1], True)
                in_word = True
            index += 2
        elif char == "`":
            raise ShellSyntaxError(STEER_SUBSTITUTION)
        elif char == "$" and index + 1 < length and text[index + 1] in _EXPANSION_STARTS:
            raise ShellSyntaxError(STEER_SUBSTITUTION if text[index + 1] == "(" else STEER_VARIABLE)
        elif char in _OPERATOR_CHARS:
            fd = _fd_prefix(chars, quoted) if char in "<>" and in_word else None
            if fd is not None:
                chars.clear()
                quoted.clear()
                in_word = False
            else:
                flush()
            op, index = _operator(text, index, fd)
            tokens.append(op)
        else:
            add(char, False)
            in_word = True
            index += 1
    flush()
    return tokens


def _double_quoted(text: str, index: int, add: Callable[[str, bool], None]) -> int:
    """Consume a double-quoted span starting after its ``"``; return the index after it."""
    while index < len(text):
        char = text[index]
        if char == '"':
            return index + 1
        if char == "\\" and index + 1 < len(text) and text[index + 1] in _DQ_ESCAPABLE:
            if text[index + 1] != "\n":
                add(text[index + 1], True)
            index += 2
            continue
        if char == "`":
            raise ShellSyntaxError(STEER_SUBSTITUTION)
        if char == "$" and index + 1 < len(text) and text[index + 1] in _DQ_EXPANSION_STARTS:
            raise ShellSyntaxError(STEER_SUBSTITUTION if text[index + 1] == "(" else STEER_VARIABLE)
        add(char, True)
        index += 1
    raise ShellQuoteError("unterminated double quote")


def _fd_prefix(chars: list[str], quoted: list[bool]) -> int | None:
    """The fd number when the word just read is unquoted digits adjacent to ``<``/``>``."""
    if not chars or any(quoted) or not all(char.isdigit() for char in chars):
        return None
    return int("".join(chars))


def _operator(text: str, index: int, fd: int | None) -> tuple[OpToken, int]:
    """Lex one operator at *index*; return it and the index after it."""
    rest = text[index:]
    char = rest[0]
    if char == "|":
        if rest.startswith("||"):
            return OpToken("||"), index + 2
        if rest.startswith("|&"):
            raise ShellSyntaxError("|& is not supported: use 2>&1 |")
        return OpToken("|"), index + 1
    if char == "&":
        if rest.startswith("&&"):
            return OpToken("&&"), index + 2
        if rest.startswith("&>>"):
            raise ShellSyntaxError("&>> is not supported: use >> file 2>&1")
        if rest.startswith("&>"):
            return OpToken("&>"), index + 2
        raise ShellSyntaxError(STEER_BACKGROUND)
    if char == ";":
        if rest.startswith(";;") or rest.startswith(";&"):
            raise ShellSyntaxError(f"{rest[:2]} is not supported: separate commands with one ;")
        return OpToken(";"), index + 1
    if char == "\n":
        return OpToken("\n"), index + 1
    if char in "()":
        raise ShellSyntaxError(STEER_SUBSHELL)
    if char == "<":
        if rest.startswith("<<"):
            raise ShellSyntaxError(STEER_HEREDOC)
        if rest.startswith("<("):
            raise ShellSyntaxError(STEER_PROCESS_SUB)
        if fd is not None or rest.startswith("<&") or rest.startswith("<>"):
            raise ShellSyntaxError("only < file is supported for input")
        return OpToken("<", 0), index + 1
    # ``>``
    if rest.startswith(">("):
        raise ShellSyntaxError(STEER_PROCESS_SUB)
    if rest.startswith(">|"):
        raise ShellSyntaxError(">| is not supported: use >")
    if rest.startswith(">&"):
        after = rest[3:4]
        if fd == 2 and rest[2:3] == "1" and (not after or after in _BLANKS or after in _OPERATOR_CHARS):
            return OpToken("2>&1", 2), index + 3
        raise ShellSyntaxError(STEER_DUP)
    if fd is not None and fd not in (1, 2):
        raise ShellSyntaxError(STEER_FD)
    if rest.startswith(">>"):
        return OpToken(">>", fd if fd is not None else 1), index + 2
    return OpToken(">", fd if fd is not None else 1), index + 1


def _word(chars: list[str], quoted: list[bool]) -> WordToken:
    text = "".join(chars)
    flags = tuple(quoted)
    if text in {"{", "}"} and not any(flags):
        raise ShellSyntaxError(STEER_GROUP)
    if _has_brace_expansion(chars, flags):
        raise ShellSyntaxError(STEER_BRACES)
    has_glob = any(char in _GLOB_CHARS and not is_quoted for char, is_quoted in zip(chars, flags))
    pattern = "".join(
        glob.escape(char) if is_quoted else char for char, is_quoted in zip(chars, flags)
    )
    return WordToken(text, pattern, has_glob, flags)


def _has_brace_expansion(chars: list[str], quoted: tuple[bool, ...]) -> bool:
    """An unquoted ``{…,…}`` or ``{a..b}``; ``{}`` (find's placeholder) is not one."""
    unquoted = "".join(char if not is_quoted else "\0" for char, is_quoted in zip(chars, quoted))
    return re.search(r"\{[^{}\0]*(,|\.\.)[^{}\0]*\}", unquoted) is not None
