"""The effect table: a coarse, reproducible class for one command.

Keyed by argv0, or argv0 plus subcommand. System AI receives the class as
a hint, not as a verdict. Pure: no I/O.
"""

from __future__ import annotations

from typing import Literal

# Reused, not copied: the git global-option walk must agree with policy
# evaluation's ``git -C … <subcommand>`` matching.
from core.bm_cli.git_argv import _git_command_tail
from core.bm_cli.policy_engine import argv0_basename_after_resolve
from core.bm_cli.types import ParsedCliCommand

EffectClass = Literal[
    "read_only",
    "local_write",
    "delete",
    "network_read",
    "network_write",
    "install",
    "host_process",
    "unknown",
]

WRITE_NAMES = frozenset({
    "rm", "rmdir", "unlink", "mv", "cp", "chmod", "chown", "chgrp",
    "touch", "mkdir", "install", "tee", "dd", "truncate", "ln", "shred",
})
REDIRECT_TOKENS = frozenset({">", ">>"})

_DELETE_NAMES = frozenset({"rm", "rmdir", "unlink", "shred"})
_HOST_PROCESS_NAMES = frozenset({"kill", "pkill", "killall", "docker"})
_GIT_DELETE = frozenset({"rm", "clean"})
_GIT_LOCAL_WRITE = frozenset({"commit", "add", "mv", "checkout", "reset", "restore"})
_GIT_NETWORK_READ = frozenset({"clone", "fetch", "pull"})
_GH_NETWORK_WRITE = {
    "pr": frozenset({"create", "merge", "close"}),
    "repo": frozenset({"create", "delete"}),
}
_GH_NETWORK_READ = {"pr": frozenset({"view", "list"})}
_GH_API_FIELD_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})
_CURL_WRITE_FLAGS = frozenset({
    "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--json",
    "-T", "--upload-file", "-F", "--form",
})
# Read-only commands, as argv token prefixes (git after its global options).
# Classification only: policy seeds authorize, this table describes.
_READ_ONLY: tuple[tuple[str, ...], ...] = (
    ("git", "status"), ("git", "log"), ("git", "diff"), ("git", "show"),
    ("git", "ls-files"), ("git", "ls-tree"), ("git", "rev-parse"), ("git", "rev-list"),
    ("git", "remote", "-v"), ("git", "remote", "get-url"),
    ("git", "branch", "--show-current"), ("git", "branch", "--list"),
    ("git", "blame"), ("git", "describe"), ("git", "shortlog"), ("git", "cat-file"),
    ("git", "config", "--get"), ("git", "stash", "list"), ("git", "tag", "--list"),
    ("readlink",), ("realpath",), ("file",), ("stat",), ("du",), ("df",), ("ps",),
    ("pgrep",), ("sleep",), ("printf",), ("tree",), ("cut",), ("nl",), ("md5sum",),
    ("sha1sum",), ("sha256sum",), ("jq",), ("ffprobe",), ("unzip", "-l"),
    # Not ``tar -t…``: ``-I``/``--use-compress-program`` run a program.
    ("id",), ("hostname",), ("nproc",), ("uptime",),
    ("ls",), ("cat",), ("head",), ("tail",), ("grep",), ("find",), ("wc",),
)
# ``find`` actions that execute a command or write/delete files
# (``-fls FILE`` writes an ``ls -dils`` listing to FILE).
_FIND_ACTIONS = frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fls"})
_FIND_WRITE_PREFIX = "-fprint"
_INSTALL = {
    "pip": frozenset({"install"}),
    "npm": frozenset({"install"}),
    "uv": frozenset({"add", "pip"}),
}


def classify_effect(parsed: ParsedCliCommand) -> EffectClass:
    """Return the effect class of one command from the named table.

    The order is: delete, host process, install, network write, network
    read, local write, then ``read_only`` when the ``_READ_ONLY`` table
    matches and ``unknown`` otherwise. ``find`` with an exec, delete,
    ``-fprint*`` or ``-fls`` action is not read-only. A redirect makes an
    otherwise read-only or unknown command a ``local_write``.

    Args:
        parsed: The parsed command.

    Returns:
        The effect class. It is a hint for the reviewer, not a verdict.
    """
    name = argv0_basename_after_resolve(parsed.name)
    args = parsed.args
    sub = _subcommand(name, args)
    if name in _DELETE_NAMES or (name == "git" and sub in _GIT_DELETE):
        return "delete"
    if name in _HOST_PROCESS_NAMES:
        return "host_process"
    if sub is not None and sub in _INSTALL.get(name, frozenset()):
        return "install"
    remote = _remote_effect(name, sub, args)
    if remote is not None:
        return remote
    if name in WRITE_NAMES or (name == "git" and sub in _GIT_LOCAL_WRITE):
        return "local_write"
    if any(token in REDIRECT_TOKENS for token in args):
        return "local_write"
    if _is_read_only(name, args):
        return "read_only"
    return "unknown"


def _subcommand(name: str, args: tuple[str, ...]) -> str | None:
    if name == "git":
        tail = _git_command_tail(args)
        return tail[0] if tail else None
    for token in args:
        if not token.startswith("-"):
            return token
    return None


def _second_operand(args: tuple[str, ...], first: str) -> str | None:
    """The operand right after ``first`` (e.g. ``create`` in ``gh pr create``)."""
    seen = False
    for token in args:
        if token.startswith("-"):
            continue
        if seen:
            return token
        seen = token == first
    return None


def _remote_effect(name: str, sub: str | None, args: tuple[str, ...]) -> EffectClass | None:
    if name == "git":
        if sub == "push":
            return "network_write"
        if sub in _GIT_NETWORK_READ:
            return "network_read"
        return None
    if name == "gh":
        if sub == "api":
            return "network_write" if _gh_api_writes(args) else "network_read"
        if sub == "release":
            return "network_write"
        action = _second_operand(args, sub) if sub is not None else None
        if sub is not None and action in _GH_NETWORK_WRITE.get(sub, frozenset()):
            return "network_write"
        if sub is not None and action in _GH_NETWORK_READ.get(sub, frozenset()):
            return "network_read"
        return None
    if name == "npm" and sub == "publish":
        return "network_write"
    if name == "curl":
        return "network_write" if _curl_writes(args) else "network_read"
    if name == "wget":
        return "network_read"
    return None


def _method_flag(args: tuple[str, ...], short: str, long: str) -> str | None:
    """Return the value of ``-X POST`` / ``-XPOST`` / ``--method=POST`` style flags."""
    for index, token in enumerate(args):
        if token in {short, long}:
            return args[index + 1] if index + 1 < len(args) else ""
        if token.startswith(long + "="):
            return token.split("=", 1)[1]
        if token.startswith(short) and len(token) > len(short) and not token.startswith("--"):
            return token[len(short):]
    return None


def _gh_api_writes(args: tuple[str, ...]) -> bool:
    method = _method_flag(args, "-X", "--method")
    if method is not None and method.upper() != "GET":
        return True
    return any(token.split("=", 1)[0] in _GH_API_FIELD_FLAGS for token in args)


def _curl_writes(args: tuple[str, ...]) -> bool:
    method = _method_flag(args, "-X", "--request")
    if method is not None and method.upper() != "GET":
        return True
    return any(token.split("=", 1)[0] in _CURL_WRITE_FLAGS for token in args)


def _is_read_only(name: str, args: tuple[str, ...]) -> bool:
    if name == "find" and any(
        token in _FIND_ACTIONS or token.startswith(_FIND_WRITE_PREFIX) for token in args
    ):
        return False
    tokens = (name, *(_git_command_tail(args) if name == "git" else args))
    return any(tokens[: len(entry)] == entry for entry in _READ_ONLY)
