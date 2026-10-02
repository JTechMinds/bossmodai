"""Trampoline unwrapping: the commands a shell command really runs.

A rule for a wrapper or for ``find`` must not decide for the command it
runs. Pure; the policy engine imports it. Rules:
    * ``env``, ``nohup``, ``timeout``, ``nice``, ``ionice``, ``stdbuf`` and
      ``time`` (after their options and operands) are replaced by the
      command they wrap; bare ``env`` stays. A leading ``NAME=value`` argv0
      is the ``env`` wrapper.
    * ``find -exec/-execdir/-ok/-okdir CMD … ;|+`` adds ``CMD …``;
      ``-delete`` adds ``rm <paths>``; ``-fprint*``/``-fls FILE`` add
      ``tee FILE``. ``find`` itself stays.
    * ``time -o FILE`` adds ``tee FILE``.
    * Assigning a :data:`PROGRAM_SELECTING_ENV` name is reported.
    * An unknown wrapper option, or ``env -C``, is reported as needing approval.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

from core.bm_cli.find_actions import find_extras

# Variables that change which binary (or which code) an allowed name runs.
PROGRAM_SELECTING_ENV: frozenset[str] = frozenset({
    "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "BASH_ENV", "ENV",
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_EXEC_PATH", "GIT_ASKPASS",
    "NODE_OPTIONS", "PYTHONSTARTUP",
})

_SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def program_selecting_env_message(name: str) -> str:
    """Return the agent-facing block message for assigning *name*."""
    return f"setting {name} changes which program runs; run the program directly"


_INFO_OPTIONS = frozenset({"help", "version"})
# Parsed wrapper options as ``(name, value)``; a short option's name is its letter.
_Options = list[tuple[str, str | None]]


@dataclass(frozen=True)
class _WrapperSpec:
    """GNU-style options of one wrapper.

    ``positionals`` operands (``timeout``'s duration) precede the command.
    ``no_command`` options mean no command runs (``--help``, or the
    operands are ids: ``ionice -p PID``). ``output`` options write a file.
    """

    short_flags: str = ""
    short_valued: str = ""
    long_flags: frozenset[str] = frozenset()
    long_valued: frozenset[str] = frozenset()
    positionals: int = 0
    no_command: frozenset[str] = _INFO_OPTIONS
    output: frozenset[str] = frozenset()
    numeric_flag: bool = False


_ENV_SPEC = _WrapperSpec(
    short_flags="i0v", short_valued="uCS",
    long_flags=frozenset({"ignore-environment", "null", "debug", "list-signal-handling"}),
    long_valued=frozenset({"unset", "chdir", "split-string"}),
)
# env options whose argument is optional and only given as ``--name=value``.
_ENV_OPTIONAL_VALUED = frozenset({"ignore-signal", "default-signal", "block-signal"})

_WRAPPERS: dict[str, _WrapperSpec] = {
    "nohup": _WrapperSpec(),
    "timeout": _WrapperSpec(
        short_flags="v", short_valued="ks", positionals=1,
        long_flags=frozenset({"preserve-status", "foreground", "verbose"}),
        long_valued=frozenset({"kill-after", "signal"}),
    ),
    "nice": _WrapperSpec(short_valued="n", long_valued=frozenset({"adjustment"}), numeric_flag=True),
    "ionice": _WrapperSpec(
        short_flags="t", short_valued="cnpPu", long_flags=frozenset({"ignore"}),
        long_valued=frozenset({"class", "classdata", "pid", "pgid", "uid"}),
        no_command=frozenset({"p", "P", "u", "pid", "pgid", "uid", *_INFO_OPTIONS}),
    ),
    "stdbuf": _WrapperSpec(short_valued="ioe", long_valued=frozenset({"input", "output", "error"})),
    "time": _WrapperSpec(
        short_flags="pvqa", short_valued="fo", output=frozenset({"o", "output"}),
        long_flags=frozenset({"portability", "verbose", "quiet", "append"}),
        long_valued=frozenset({"format", "output"}),
    ),
}


@dataclass(frozen=True)
class EffectiveCommands:
    """What one command really runs, for policy and the approval gate.

    Attributes:
        commands: Command strings to evaluate, in order. The first is the
            command itself after wrapper unwrapping; when nothing was
            unwrapped it is the raw string unchanged. Extra entries come
            from ``find`` actions and ``time -o``.
        argvs: The same commands as argv tuples.
        replaced: True when the first command is not the raw command (a
            wrapper was replaced by what it wraps).
        program_env: The first :data:`PROGRAM_SELECTING_ENV` name assigned,
            or None.
        approval_reason: Why the command cannot be proven to run only the
            listed commands (an unknown wrapper option, ``env -C``, or an
            unparsable string), or None.
        filtered_starts: Per argv, the operands that stand for a start path
            of a ``find`` with test predicates (see
            :class:`~core.bm_cli.find_actions.FindExtra`); empty otherwise.
    """

    commands: tuple[str, ...]
    argvs: tuple[tuple[str, ...], ...]
    replaced: bool
    program_env: str | None
    approval_reason: str | None
    filtered_starts: tuple[frozenset[str], ...] = ()


@dataclass
class _Acc:
    braces_to_paths: bool
    argvs: list[tuple[str, ...]] = field(default_factory=list)
    filtered: dict[int, frozenset[str]] = field(default_factory=dict)
    program_env: str | None = None
    approval_reason: str | None = None

    def assign(self, name: str) -> None:
        if self.program_env is None and name in PROGRAM_SELECTING_ENV:
            self.program_env = name

    def needs_approval(self, reason: str) -> None:
        if self.approval_reason is None:
            self.approval_reason = reason


def unwrap_command(command_str: str) -> EffectiveCommands:
    """Return the commands *command_str* really runs.

    Args:
        command_str: One shell-like command (no pipes or connectors).

    Returns:
        The effective commands. A string that does not split as shell words
        is returned as its only command with an ``approval_reason``, so it
        is never decided by a wrapper's rule.
    """
    try:
        argv = tuple(shlex.split(command_str, posix=True))
    except ValueError as exc:
        reason = f"cannot split the command into words: {exc}"
        return EffectiveCommands((command_str,), (), False, None, reason)
    unwrapped = unwrap_argv(argv)
    if not unwrapped.argvs:
        # Nothing to unwrap: an empty command is evaluated as given.
        return replace(unwrapped, commands=(command_str,))
    if unwrapped.replaced:
        return unwrapped
    # Not unwrapped: keep the raw string, so rules see the command as written.
    return replace(unwrapped, commands=(command_str, *unwrapped.commands[1:]))


def effective_commands(command_str: str) -> tuple[str, ...]:
    """Return the command strings policy evaluates in place of *command_str*."""
    return unwrap_command(command_str).commands


def unwrap_argv(argv: Sequence[str], *, braces_to_paths: bool = False) -> EffectiveCommands:
    """Return the commands one argv really runs.

    Args:
        argv: The command's words, argv0 first.
        braces_to_paths: Replace ``{}`` in a ``find -exec`` command with
            the find's start paths. Policy keeps ``{}`` (rules match the
            command as written); path facts need the places it acts on,
            which include each start path itself.

    Returns:
        The effective commands; ``commands`` are ``shlex.join`` of ``argvs``.
        An empty argv has no commands.
    """
    acc = _Acc(braces_to_paths=braces_to_paths)
    tokens = tuple(argv)
    if tokens:
        _expand(tokens, acc)
    replaced = bool(acc.argvs) and acc.argvs[0] != tokens
    commands = tuple(shlex.join(item) for item in acc.argvs)
    filtered = tuple(acc.filtered.get(index, frozenset()) for index in range(len(acc.argvs)))
    return EffectiveCommands(
        commands, tuple(acc.argvs), replaced, acc.program_env, acc.approval_reason, filtered,
    )


def _expand(argv: tuple[str, ...], acc: _Acc) -> None:
    """Peel wrappers off *argv*, record it, then add what ``find`` runs."""
    side: list[tuple[str, ...]] = []
    while True:
        inner = _peel(argv, acc, side)
        if inner is None:
            break
        argv = inner
    acc.argvs.append(argv)
    acc.argvs.extend(side)
    if _basename(argv[0]) == "find":
        for extra in find_extras(argv[1:], braces_to_paths=acc.braces_to_paths):
            # An expanded command is recorded first, so it lands at this index.
            acc.filtered[len(acc.argvs)] = extra.filtered_starts
            if extra.unwrap:
                _expand(extra.argv, acc)
            else:
                acc.argvs.append(extra.argv)


def _peel(argv: tuple[str, ...], acc: _Acc, side: list[tuple[str, ...]]) -> tuple[str, ...] | None:
    """Return the command *argv* wraps, or None when it is not a wrapper.

    Files a wrapper option writes (``time -o FILE``) go to *side* as
    ``tee FILE``.
    """
    if _SHELL_ASSIGNMENT_RE.match(argv[0]):
        index = 0
        while index < len(argv) and _SHELL_ASSIGNMENT_RE.match(argv[index]):
            acc.assign(argv[index].split("=", 1)[0])
            index += 1
        return argv[index:] or None
    name = _basename(argv[0])
    if name == "env":
        return _peel_env(argv[1:], acc)
    spec = _WRAPPERS.get(name)
    if spec is None:
        return None
    scanned = _scan_options(name, argv[1:], spec, acc)
    if scanned is None:
        return None
    options, index = scanned
    for option, value in options:
        if option in spec.output and value is not None:
            side.append(("tee", value))
    rest = argv[1 + index + spec.positionals:]
    return rest or None


def _peel_env(args: tuple[str, ...], acc: _Acc) -> tuple[str, ...] | None:
    scanned = _scan_options("env", args, _ENV_SPEC, acc)
    if scanned is None:
        return None
    options, index = scanned
    split: list[str] = []
    for option, value in options:
        if option in {"C", "chdir"}:
            acc.needs_approval("env -C runs the command in another directory")
        elif option in {"S", "split-string"} and value is not None:
            try:
                split.extend(shlex.split(value, posix=True))
            except ValueError as exc:
                acc.needs_approval(f"env -S string does not split into words: {exc}")
                return None
    if split:
        # GNU env reads the -S words as if they were given in its place.
        return _peel_env((*split, *args[index:]), acc)
    while index < len(args) and "=" in args[index]:
        acc.assign(args[index].split("=", 1)[0])
        index += 1
    return args[index:] or None


def _scan_options(
    wrapper: str, args: tuple[str, ...], spec: _WrapperSpec, acc: _Acc,
) -> tuple[_Options, int] | None:
    """Scan leading GNU options; return ``(options, first operand index)``.

    Returns None when the wrapper runs no command (an info option or an
    ``ionice -p`` style id list) or an option is unknown; the latter is
    recorded as needing approval.
    """
    options: _Options = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            return options, index + 1
        if wrapper == "env" and token == "-":
            options.append(("i", None))
            index += 1
            continue
        if spec.numeric_flag and re.fullmatch(r"-[-+]?\d+", token):
            options.append(("n", token[1:]))
            index += 1
            continue
        if token.startswith("--"):
            name, eq, value = token[2:].partition("=")
            if name in spec.long_valued:
                if not eq:
                    if index + 1 >= len(args):
                        acc.needs_approval(f"{wrapper} --{name} has no value")
                        return None
                    value = args[index + 1]
                    index += 1
                options.append((name, value))
            elif name in spec.long_flags or name in spec.no_command:
                options.append((name, None))
            elif wrapper == "env" and name in _ENV_OPTIONAL_VALUED:
                options.append((name, value if eq else None))
            else:
                acc.needs_approval(f"{wrapper} option {token} is not recognised")
                return None
            index += 1
            continue
        if token.startswith("-") and len(token) > 1:
            consumed = _scan_short_cluster(wrapper, args, index, spec, options, acc)
            if consumed is None:
                return None
            index += consumed
            continue
        break
    if any(option in spec.no_command for option, _value in options):
        return None
    return options, index


def _scan_short_cluster(
    wrapper: str, args: tuple[str, ...], index: int, spec: _WrapperSpec, options: _Options, acc: _Acc,
) -> int | None:
    """Read one ``-abc``/``-kVALUE``/``-k VALUE`` cluster; return tokens used."""
    token = args[index]
    for offset, char in enumerate(token[1:], start=1):
        if char in spec.short_flags or char in spec.no_command:
            options.append((char, None))
            continue
        if char in spec.short_valued:
            value = token[offset + 1:]
            if value:
                options.append((char, value))
                return 1
            if index + 1 >= len(args):
                acc.needs_approval(f"{wrapper} -{char} has no value")
                return None
            options.append((char, args[index + 1]))
            return 2
        acc.needs_approval(f"{wrapper} option -{char} is not recognised")
        return None
    return 1


def _basename(argv0: str) -> str:
    return PurePosixPath(argv0).name or argv0
