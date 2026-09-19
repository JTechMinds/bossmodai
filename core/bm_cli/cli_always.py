"""Nest-scoped CLI Always rules — the Settings Always path, cwd-gated.

Always allow on a nest Approve card writes a real ``always_allowed`` row
the same way Settings does. The rule only matches when cwd sits under
``/me/host-work`` (locked nest clone). Desktop and host outside the clone
never match. No fake card flag.
"""

from __future__ import annotations

import shlex

import db
from core.bm_cli.policy_engine import argv0_basename_after_resolve
from core.models.cli_policy import CliPolicyRule

NEST_CWD_PREFIX = "/me/host-work"
NEST_ALWAYS_CATEGORY = "nest"


def _posix_cwd(raw: str | None) -> str:
    return (raw or "").replace("\\", "/").strip()


def looks_like_desktop_or_home(raw: str | None) -> bool:
    """Return True for Desktop / $HOME-shaped paths that must never get Always."""
    token = _posix_cwd(raw)
    if not token:
        return False
    parts = [part for part in token.split("/") if part]
    if "Desktop" in parts or "desktop" in parts:
        return True
    if token == "/me" or token == "/":
        return False
    if len(parts) >= 2 and parts[0] in {"home", "Users"} and "host-work" not in parts:
        return True
    return False


def is_nest_cwd(raw: str | None) -> bool:
    """Return True when *raw* is a locked nest clone cwd under /me/host-work."""
    token = _posix_cwd(raw)
    if not token or looks_like_desktop_or_home(token):
        return False
    if token == NEST_CWD_PREFIX or token.startswith(NEST_CWD_PREFIX + "/"):
        return True
    parts = token.split("/")
    try:
        index = parts.index("host-work")
    except ValueError:
        return False
    return index + 1 < len(parts)


def offers_always_allow_cli(cwd: str | None) -> bool:
    """Return True when a nest Approve card may offer Always allow."""
    return is_nest_cwd(cwd)


def cwd_matches_rule_scope(cwd: str | None, cwd_prefix: str | None) -> bool:
    """Return True when a rule's optional cwd prefix covers *cwd*.

    Unscoped rules (no prefix) match any cwd. Scoped rules fail closed when
    cwd is missing, Desktop/home, or outside the prefix.
    """
    prefix = _posix_cwd(cwd_prefix)
    if not prefix:
        return True
    token = _posix_cwd(cwd)
    if not token or looks_like_desktop_or_home(token):
        return False
    if token == prefix or token.startswith(prefix + "/"):
        return True
    if prefix == NEST_CWD_PREFIX or prefix.startswith(NEST_CWD_PREFIX + "/"):
        return is_nest_cwd(token)
    return False


def always_allow_pattern(command: str, matched_rule_id: str | None = None) -> str:
    """Return the Settings Always prefix for *command*.

    Reuses the matched approval rule's pattern when present (``git push``).
    Otherwise the argv0 basename (``sed``).
    """
    if matched_rule_id:
        rule = db.get_cli_policy_rule(matched_rule_id)
        pattern = (getattr(rule, "pattern", None) or "").strip()
        if pattern:
            return pattern
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return (command or "").strip()
    return argv0_basename_after_resolve(tokens[0]) or tokens[0]


def find_nest_always_rule(pattern: str) -> CliPolicyRule | None:
    """Return an existing nest-scoped Always rule for *pattern*, if any."""
    for rule in db.list_cli_policy_rules(tier="always_allowed"):
        if (
            rule.pattern == pattern
            and rule.match_mode == "prefix"
            and _posix_cwd(getattr(rule, "cwd_prefix", None)) == NEST_CWD_PREFIX
        ):
            return rule
    return None


def write_nest_always_rule(
    command: str,
    cwd: str | None,
    *,
    matched_rule_id: str | None = None,
) -> CliPolicyRule:
    """Write (or reuse) a real Settings Always rule scoped to /me/host-work."""
    if not offers_always_allow_cli(cwd):
        raise ValueError(
            "Always allow is only offered for commands in a locked nest clone "
            "under /me/host-work."
        )
    pattern = always_allow_pattern(command, matched_rule_id)
    existing = find_nest_always_rule(pattern)
    if existing is not None:
        return existing
    return db.create_cli_policy_rule(
        tier="always_allowed",
        pattern=pattern,
        match_mode="prefix",
        cwd_prefix=NEST_CWD_PREFIX,
        description=f"Always allow {pattern} in locked nest clones.",
        category=NEST_ALWAYS_CATEGORY,
    )
