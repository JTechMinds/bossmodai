"""Nest git Settings store, host probe, and Shell env — one vault.

Host Enable only flips On after a probe that the Shell can see a credential
helper or SSH agent. PAT/SSH live in Settings with the same ``bm1:`` wrap as
API keys. Secrets never go in logs or command strings.
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import db
from core import config
from core.bm_cli.cli_always import is_nest_cwd
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.types import ParsedCliCommand
from core.models import Agent
from core.models.nest_git import (
    NEST_GIT_BOT_EMAIL,
    NEST_GIT_BOT_NAME,
    NEST_GIT_CATEGORY,
    NEST_GIT_HOST_ENABLED_KEY,
    NEST_GIT_HOWTO,
    NEST_GIT_NO_CREDS_WHY,
    NEST_GIT_PAT_KEY,
    NEST_GIT_PROBE_FAIL_WHY,
    NEST_GIT_SSH_KEY,
)
from db.secret_store import decrypt_setting_value

logger = logging.getLogger(__name__)

# Remote ops that talk to a host. Local add/commit/status do not.
_AUTH_GIT_SUBCOMMANDS = frozenset({
    "push", "fetch", "pull", "clone", "ls-remote",
})

_HOST_PASSTHROUGH_ENV = (
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "GIT_ASKPASS",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
)

_ASKPASS_SCRIPT = """#!/bin/sh
case \"$1\" in
  *[Uu]sername*) printf '%s' \"${BOSSMOD_NEST_GIT_USERNAME:-x-access-token}\" ;;
  *) printf '%s' \"$BOSSMOD_NEST_GIT_PASSWORD\" ;;
esac
"""


@dataclass(frozen=True, slots=True)
class HostGitProbe:
    """Result of asking whether host git is visible to Shell."""

    ok: bool
    via: str = ""
    why: str = ""
    how_to: str = NEST_GIT_HOWTO

    def blocked_message(self) -> str:
        reason = (self.why or NEST_GIT_PROBE_FAIL_WHY).strip()
        return f"{reason}. {self.how_to}"


def host_git_is_enabled() -> bool:
    """Return True when host Enable is On in Settings."""
    return config.get_live(NEST_GIT_HOST_ENABLED_KEY) == "true"


def read_nest_git_secret(key: str) -> str:
    """Return a decrypted nest-git secret. Empty when unset.

    ``config.get_live`` returns ciphertext for secret keys. Settings CRUD
    decrypts; this path does the same unwrap.
    """
    from db.crud import query_one

    row = query_one("SELECT value FROM settings WHERE key = $1", [key])
    raw = "" if row is None or row.get("value") is None else str(row["value"])
    return (decrypt_setting_value(key, raw) or "").strip()


def nest_git_pat() -> str:
    """Return the stored PAT, or empty."""
    return read_nest_git_secret(NEST_GIT_PAT_KEY)


def nest_git_ssh_key() -> str:
    """Return the stored SSH private key, or empty."""
    return read_nest_git_secret(NEST_GIT_SSH_KEY)


def nest_git_has_stored_creds() -> bool:
    """Return True when a PAT or SSH key is saved in Settings."""
    return bool(nest_git_pat() or nest_git_ssh_key())


def nest_git_auth_ready() -> bool:
    """Return True when a usable auth path exists (fail-closed otherwise)."""
    if nest_git_has_stored_creds():
        return True
    if not host_git_is_enabled():
        return False
    return probe_host_git_for_shell().ok


def write_nest_git_secret(key: str, value: str) -> None:
    """Write or clear one nest-git secret through Settings (bm1 wrap)."""
    db.set_setting(key, value or "", NEST_GIT_CATEGORY)
    config.reload()


def command_needs_nest_git_auth(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
) -> bool:
    """Return True when this CLI is a nest remote-git op that needs auth."""
    if parsed.name != "git":
        return False
    subcommand = parsed.args[0] if parsed.args else ""
    if subcommand not in _AUTH_GIT_SUBCOMMANDS:
        return False
    if is_nest_cwd(cwd):
        return True
    from core.bm_cli.workspace_preference import cwd_is_nested_clone_repo

    return cwd_is_nested_clone_repo(agent, cwd)


def command_text_needs_nest_git_auth(
    agent: Agent,
    command: str,
    cwd: str,
) -> bool:
    """Parse *command* and apply :func:`command_needs_nest_git_auth`."""
    try:
        parsed = parse_cli_command(command)
    except ValueError:
        return False
    return command_needs_nest_git_auth(agent, parsed, cwd)


def probe_host_git_for_shell() -> HostGitProbe:
    """Return whether a credential helper or SSH agent is visible to Shell.

    Uses the same host passthrough env nest git would inject — not a browser
    or desktop GitHub session.
    """
    extra = host_git_passthrough_env()
    helper = _credential_helper_visible(extra)
    if helper:
        return HostGitProbe(ok=True, via="credential_helper")
    if _ssh_agent_visible(extra):
        return HostGitProbe(ok=True, via="ssh_agent")
    return HostGitProbe(
        ok=False,
        why=NEST_GIT_PROBE_FAIL_WHY,
        how_to=NEST_GIT_HOWTO,
    )


def enable_host_git() -> HostGitProbe:
    """Flip host Enable On only after the Shell probe passes."""
    probe = probe_host_git_for_shell()
    if not probe.ok:
        logger.info("nest git host Enable refused: probe failed")
        return probe
    db.set_setting(NEST_GIT_HOST_ENABLED_KEY, "true", NEST_GIT_CATEGORY)
    config.reload()
    return probe


def disable_host_git() -> None:
    """Turn host Enable Off. No probe."""
    db.set_setting(NEST_GIT_HOST_ENABLED_KEY, "false", NEST_GIT_CATEGORY)
    config.reload()


def nest_git_status() -> dict[str, object]:
    """Redacted status for Settings. No secret material."""
    pat = nest_git_pat()
    ssh = nest_git_ssh_key()
    probe = probe_host_git_for_shell()
    return {
        "host_enabled": host_git_is_enabled(),
        "has_pat": bool(pat),
        "pat_last4": pat[-4:] if pat else None,
        "has_ssh": bool(ssh),
        "ssh_last4": ssh[-4:] if ssh else None,
        "probe_ok": probe.ok,
        "probe_via": probe.via or None,
        "probe_why": None if probe.ok else probe.why,
        "how_to": NEST_GIT_HOWTO,
    }


def host_git_passthrough_env() -> dict[str, str]:
    """Host git vars the Shell may receive when host Enable is On."""
    extra: dict[str, str] = {}
    for name in _HOST_PASSTHROUGH_ENV:
        value = os.environ.get(name)
        if value:
            extra[name] = value
    return extra


def nest_git_shell_env(agent: Agent) -> dict[str, str]:
    """Extra env for one nest git Shell invocation. Never log the values."""
    del agent
    extra: dict[str, str] = {}
    pat = nest_git_pat()
    ssh = nest_git_ssh_key()
    if pat:
        extra.update(_pat_env(pat))
    elif ssh:
        extra.update(_ssh_key_env(ssh))
    elif host_git_is_enabled() and probe_host_git_for_shell().ok:
        extra.update(host_git_passthrough_env())
    extra.setdefault("GIT_TERMINAL_PROMPT", "0")
    return extra


def no_creds_blocked_message() -> str:
    """Return Blocked why + how-to when no auth path is ready."""
    return f"{NEST_GIT_NO_CREDS_WHY}. {NEST_GIT_HOWTO}"


def _credential_helper_visible(extra: dict[str, str]) -> bool:
    env = _probe_env(extra)
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "credential.helper"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool((proc.stdout or "").strip())


def _ssh_agent_visible(extra: dict[str, str]) -> bool:
    sock = extra.get("SSH_AUTH_SOCK") or os.environ.get("SSH_AUTH_SOCK") or ""
    if not sock:
        return False
    path = Path(sock)
    if not path.exists():
        return False
    env = _probe_env(extra)
    try:
        proc = subprocess.run(
            ["ssh-add", "-l"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return path.exists()
    # 0 = keys, 1 = agent up but no keys. Either is "agent visible".
    return proc.returncode in {0, 1}


def _probe_env(extra: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in {
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE",
    }}
    env.update(extra)
    return env


def _pat_env(pat: str) -> dict[str, str]:
    return {
        "GIT_ASKPASS": str(_ensure_askpass()),
        "GIT_TERMINAL_PROMPT": "0",
        "BOSSMOD_NEST_GIT_USERNAME": "x-access-token",
        "BOSSMOD_NEST_GIT_PASSWORD": pat,
        "GIT_AUTHOR_NAME": NEST_GIT_BOT_NAME,
        "GIT_AUTHOR_EMAIL": NEST_GIT_BOT_EMAIL,
        "GIT_COMMITTER_NAME": NEST_GIT_BOT_NAME,
        "GIT_COMMITTER_EMAIL": NEST_GIT_BOT_EMAIL,
    }


def _ssh_key_env(key_material: str) -> dict[str, str]:
    path = _write_ssh_key(key_material)
    return {
        "GIT_SSH_COMMAND": f"ssh -i {path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _ensure_askpass() -> Path:
    from db.connection import database_path

    path = database_path().resolve().parent / ".nest_git_askpass"
    if not path.exists() or path.read_text(encoding="utf-8") != _ASKPASS_SCRIPT:
        path.write_text(_ASKPASS_SCRIPT, encoding="utf-8")
        path.chmod(0o700)
    return path


def _write_ssh_key(material: str) -> Path:
    from db.connection import database_path

    path = database_path().resolve().parent / ".nest_git_ssh"
    path.write_text(material if material.endswith("\n") else material + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path
