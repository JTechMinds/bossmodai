"""Parse the small, explicit subset of POSIX sh that agent CLI scripts may use.

Supported: pipes ``|``, connectors ``&&`` ``||`` ``;`` (and a newline as
``;``), redirects ``>`` ``>>`` ``<`` ``2>&1`` ``&>`` (an adjacent fd 1 or
2 before ``>``/``>>``), leading ``NAME=value`` assignments, and unquoted
globs. Quoting (single, double, backslash) is tracked per character by
:mod:`core.bm_cli.shell_lexer`, so a quoted ``|`` or ``*`` is a literal
and ``\\;`` is a word, not a connector.

Everything a shell would expand or interpret beyond that (substitution,
variables, background jobs, subshells, groups, heredocs, process
substitution, brace expansion, other fds) is rejected with a steer the
agent can act on. No shell ever runs these scripts: the parse is the
whole interpretation. Pure: no I/O.

``shlex(punctuation_chars=True)`` was rejected: it cannot tell ``'*.py'``
from ``*.py``, turns ``\\;`` into ``;`` and splits ``2>&1`` without adjacency.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from core.bm_cli.shell_lexer import (
    OpToken,
    ShellQuoteError,
    ShellSyntaxError,
    Token,
    WordToken,
    lex,
)

__all__ = [
    "Connector", "Pipeline", "Redirect", "ShellQuoteError", "ShellScript", "ShellSyntaxError",
    "SimpleCommand", "Word", "is_compound", "parse_shell_script", "segment_raw",
]

Connector = Literal["&&", "||", ";"]
RedirectFd = Literal[0, 1, 2, "both"]
RedirectMode = Literal["write", "append", "read", "dup_out"]
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


@dataclass(frozen=True, slots=True)
class Word:
    """One shell word after quote removal.

    Attributes:
        text: The literal word.
        glob: True only when an unquoted ``*``, ``?`` or ``[`` appears.
        pattern: The word as a :mod:`glob` pattern: quoted glob characters
            are escaped, so ``'a*'b*`` matches names starting ``a*b``.
    """

    text: str
    glob: bool
    pattern: str


@dataclass(frozen=True, slots=True)
class Redirect:
    """One redirect on a simple command.

    Attributes:
        fd: 1 or 2 for ``>``/``>>`` (``1`` when no fd is written), 0 for
            ``<``, 2 for ``2>&1``, ``"both"`` for ``&>``.
        mode: ``write`` (``>``), ``append`` (``>>``), ``read`` (``<``), or
            ``dup_out`` (``2>&1``: stderr goes where stdout goes now).
        target: The file word; None only for ``dup_out``.
    """

    fd: RedirectFd
    mode: RedirectMode
    target: Word | None


@dataclass(frozen=True, slots=True)
class SimpleCommand:
    """One command: leading assignments, argv words, redirects in source order."""

    assignments: tuple[tuple[str, str], ...]
    argv: tuple[Word, ...]
    redirects: tuple[Redirect, ...]


@dataclass(frozen=True, slots=True)
class Pipeline:
    """Commands joined by ``|``; the pipeline's status is its last command's."""

    commands: tuple[SimpleCommand, ...]


@dataclass(frozen=True, slots=True)
class ShellScript:
    """Pipelines in order, each with the connector to the next (None for the last)."""

    items: tuple[tuple[Pipeline, Connector | None], ...]

    def commands(self) -> tuple[SimpleCommand, ...]:
        """Every simple command, in source order."""
        return tuple(cmd for pipeline, _ in self.items for cmd in pipeline.commands)

    def with_commands(self, commands: Sequence[SimpleCommand]) -> ShellScript:
        """The same pipelines and connectors with each simple command replaced, in order.

        Raises:
            ValueError: *commands* does not have one entry per simple command.
        """
        if len(commands) != len(self.commands()):
            raise ValueError(f"{len(commands)} commands for a script of {len(self.commands())}")
        items: list[tuple[Pipeline, Connector | None]] = []
        taken = 0
        for pipeline, connector in self.items:
            count = len(pipeline.commands)
            items.append((Pipeline(tuple(commands[taken:taken + count])), connector))
            taken += count
        return ShellScript(tuple(items))


def parse_shell_script(text: str) -> ShellScript:
    """Parse *text* into a script, or explain what is not supported.

    Grammar::

        script   := pipeline ((&& | '||' | ; | NEWLINE) pipeline)* [; | NEWLINE]
        pipeline := command ('|' command)*
        command  := (NAME=word)* (word | redirect)+   -- at least one word
        redirect := ['1'|'2'] ('>'|'>>') word | '<' word | '2>&1' | '&>' word

    Args:
        text: The command line the agent sent.

    Returns:
        The parsed script.

    Raises:
        ShellQuoteError: An unterminated quote or a trailing backslash.
        ShellSyntaxError: Anything outside the grammar, with a steer.
    """
    return _Parser(lex(text)).script()


def is_compound(text: str) -> bool:
    """True when *text* must take the script path instead of the single-command path.

    False for one plain command (no operator, redirect, assignment or
    unquoted glob), so those keep today's exact path, and for text that
    is not words at all (the single-command parser reports that). True
    when the text uses any other unsupported construct, so the script path
    returns its steer instead of passing the construct through as literal
    arguments.
    """
    # Surrounding blank lines are not separators between commands.
    text = text.strip()
    if not text:
        return False
    try:
        tokens = lex(text)
        script = _Parser(tokens).script()
    except ShellQuoteError:
        return False
    except ShellSyntaxError:
        return True
    if any(isinstance(token, OpToken) for token in tokens):
        return True
    (command,) = script.commands()
    return bool(command.assignments) or any(word.glob for word in command.argv)


def segment_raw(command: SimpleCommand) -> str:
    """The policy subject for one segment: its argv, shell-quoted."""
    return shlex.join(word.text for word in command.argv)


class _Parser:
    """Recursive descent over the lexed tokens."""

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def script(self) -> ShellScript:
        self._skip_newlines()
        if self._at_end():
            raise ShellSyntaxError("the command is empty")
        items: list[tuple[Pipeline, Connector | None]] = []
        while True:
            pipeline = self._pipeline()
            if self._at_end():
                items.append((pipeline, None))
                break
            op = self._op()
            self._pos += 1
            self._skip_newlines()
            if op in ("&&", "||"):
                if self._at_end():
                    raise ShellSyntaxError(f"nothing follows {op}: add the next command or remove {op}")
                items.append((pipeline, op))
                continue
            if op not in (";", "\n"):
                raise ShellSyntaxError(f"unexpected {op!r}")
            if self._at_end():
                items.append((pipeline, None))
                break
            items.append((pipeline, ";"))
        return ShellScript(tuple(items))

    def _pipeline(self) -> Pipeline:
        commands = [self._command()]
        while not self._at_end() and self._op() == "|":
            self._pos += 1
            self._skip_newlines()
            if self._at_end():
                raise ShellSyntaxError("nothing follows |: add the next command or remove |")
            commands.append(self._command())
        return Pipeline(tuple(commands))

    def _command(self) -> SimpleCommand:
        assignments: list[tuple[str, str]] = []
        argv: list[Word] = []
        redirects: list[Redirect] = []
        while not self._at_end():
            token = self._tokens[self._pos]
            if isinstance(token, WordToken):
                self._pos += 1
                name = _assignment_name(token)
                if not argv and name is not None:
                    assignments.append((name, token.text[len(name) + 1:]))
                else:
                    argv.append(Word(token.text, token.glob, token.pattern))
                continue
            if token.op in ("|", "||", "&&", ";", "\n"):
                break
            self._pos += 1
            redirects.append(self._redirect(token))
        if not argv:
            following = "" if self._at_end() else f" before {self._op()!r}"
            raise ShellSyntaxError(f"a command is missing{following}")
        return SimpleCommand(tuple(assignments), tuple(argv), tuple(redirects))

    def _redirect(self, token: OpToken) -> Redirect:
        if token.op == "2>&1":
            return Redirect(2, "dup_out", None)
        target = None if self._at_end() else self._tokens[self._pos]
        if not isinstance(target, WordToken):
            raise ShellSyntaxError(f"{token.op} needs a file name after it")
        self._pos += 1
        if target.glob:
            raise ShellSyntaxError(f"the file after {token.op} must be one plain name, not a glob")
        word = Word(target.text, False, target.pattern)
        if token.op == "<":
            return Redirect(0, "read", word)
        if token.op == "&>":
            return Redirect("both", "write", word)
        fd: RedirectFd = 2 if token.fd == 2 else 1
        return Redirect(fd, "append" if token.op == ">>" else "write", word)

    def _op(self) -> str:
        token = self._tokens[self._pos]
        if not isinstance(token, OpToken):
            raise ShellSyntaxError(f"unexpected word {token.text!r}")
        return token.op

    def _skip_newlines(self) -> None:
        while not self._at_end() and isinstance(self._tokens[self._pos], OpToken) and self._op() == "\n":
            self._pos += 1

    def _at_end(self) -> bool:
        return self._pos >= len(self._tokens)


def _assignment_name(token: WordToken) -> str | None:
    """``NAME`` when the word starts with an unquoted ``NAME=``."""
    match = _ASSIGNMENT_RE.match(token.text)
    if match is None or any(token.quoted[: match.end()]):
        return None
    return match.group(0)[:-1]
