"""Hard-deny environment dumps of GitHub / nest-git tokens.

``printenv`` and ``env`` argv that name ``GH_TOKEN`` / ``GITHUB_TOKEN`` (and
kin) are ``never_allowed``. The detector is shared policy, not a per-command
runtime if/else. Token *values* must never enter chat, logs, or prompts —
this module only matches variable *names*.
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
    the diagnostic ``env`` seed stays always-allowed.
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
    argv0_names = _argv0_dump_names(tokens[0])
    if "printenv" in argv0_names:
        return True
    if "env" not in argv0_names:
        return False
    blob = " ".join(tokens[1:])
    return bool(_TOKEN_NAME_RE.search(blob))


def _argv0_dump_names(argv0: str) -> set[str]:
    token = (argv0 or "").strip()
    if not token:
        return set()
    names = {token.lower(), Path(token).name.lower()}
    try:
        names.add(Path(token).expanduser().name.lower())
    except OSError:
        pass
    return {name for name in names if name in _DUMP_ARGV0}
