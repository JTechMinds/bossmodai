"""Hard-deny environment dumps of GitHub / nest-git tokens.

``printenv`` and ``env`` argv that name ``GH_TOKEN`` / ``GITHUB_TOKEN`` (and
kin) are ``never_allowed``. ``gh auth token`` is the same class of dump.
The detector is shared policy, not a per-command runtime if/else. Token
*values* must never enter chat, logs, or prompts — matching uses variable
*names*; redaction uses values already present on a subprocess env dict.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

# Variable names only. Never log or interpolate values for these keys.
SECRET_TOKEN_ENV_NAMES: frozenset[str] = frozenset({
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GH_PAT",
    "GITHUB_PAT",
    "GITHUB_API_TOKEN",
    "BOSSMOD_NEST_GIT_PASSWORD",
})

_DUMP_ARGV0: frozenset[str] = frozenset({"printenv", "env"})
_GH_ARGV0: frozenset[str] = frozenset({"gh", "gh.exe"})
_GH_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-h",
    "--hostname",
    "-R",
    "--repo",
    "--dir",
})

_TOKEN_NAME_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_])("
    + "|".join(sorted(SECRET_TOKEN_ENV_NAMES, key=len, reverse=True))
    + r")(?:[^A-Za-z0-9_]|$)"
)

SECRET_TOKEN_ENV_DUMP_WHY = (
    "Environment dumps of GH_TOKEN / GITHUB_TOKEN are never allowed"
)
SECRET_TOKEN_ENV_DUMP_STEER = (
    "Tokens must not enter chat. Use Nest git for push, or open the compare URL. "
    "Do not retry printenv or env dumps of those names. "
    "Do not invent a desk deny. Do not park @Operator as an enablement switch."
)
SECRET_TOKEN_ENV_DUMP_MESSAGE = (
    f"{SECRET_TOKEN_ENV_DUMP_WHY}. {SECRET_TOKEN_ENV_DUMP_STEER}"
)


def command_dumps_secret_token_env(command_str: str) -> bool:
    """Return True when *command_str* would dump GH_TOKEN / GITHUB_TOKEN (or kin).

    Any ``printenv`` is a dump (including a bare ``printenv``). ``env`` only
    matches when an argv token names one of :data:`SECRET_TOKEN_ENV_NAMES`, so
    the diagnostic ``env`` seed stays always-allowed. ``gh auth token`` prints
    the injected token and is the same deny.
    """
    text = (command_str or "").strip()
    if not text:
        return False
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()
    if not tokens:
        return False
    argv0_names = _argv0_names(tokens[0])
    if argv0_names & _DUMP_ARGV0 and "printenv" in argv0_names:
        return True
    if argv0_names & _GH_ARGV0 and _gh_auth_token_dump(tokens[1:]):
        return True
    if "env" not in argv0_names:
        return False
    blob = " ".join(tokens[1:])
    return bool(_TOKEN_NAME_RE.search(blob))


def redact_secret_env_values(text: str, extra_env: dict[str, str] | None) -> str:
    """Replace secret *values* from *extra_env* so they never enter results.

    Only keys in :data:`SECRET_TOKEN_ENV_NAMES` are scrubbed. Empty values
    are skipped. The env dict itself is never logged.
    """
    if not text or not extra_env:
        return text
    redacted = text
    for name, value in extra_env.items():
        if name not in SECRET_TOKEN_ENV_NAMES:
            continue
        secret = (value or "").strip()
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def _gh_auth_token_dump(args: list[str]) -> bool:
    index = 0
    tokens = list(args)
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token.startswith("-"):
            key = token.split("=", 1)[0]
            if key in _GH_VALUE_OPTIONS and "=" not in token:
                index += 2
                continue
            index += 1
            continue
        break
    if index >= len(tokens) or tokens[index] != "auth":
        return False
    index += 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token.startswith("-"):
            key = token.split("=", 1)[0]
            if key in _GH_VALUE_OPTIONS and "=" not in token:
                index += 2
                continue
            index += 1
            continue
        return token == "token"
    return index < len(tokens) and tokens[index] == "token"


def _argv0_names(argv0: str) -> set[str]:
    token = (argv0 or "").strip()
    if not token:
        return set()
    names = {token.lower(), Path(token).name.lower()}
    try:
        names.add(Path(token).expanduser().name.lower())
    except OSError:
        pass
    return names
