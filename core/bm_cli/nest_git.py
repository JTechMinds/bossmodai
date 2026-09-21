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
from typing import Literal

import db
from core import config
from core.bm_cli.cli_always import is_nest_cwd
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.types import ParsedCliCommand
from core.models import Agent
from core.bm_cli.nest_git_store import (
    NestGitCredential,
    credential_has_secrets,
    credential_secrets,
    ensure_default_from_legacy,
    load_credentials,
    migrate_legacy_store,
    read_secret,
    redact_credential,
    repo_current_branch,
    resolve_git_remote,
    select_nest_git_credential,
    write_secret,
)
from core.models.nest_git import (
    GH_CLI_COMPARE_HOWTO,
    GH_CLI_HOWTO,
    GH_CLI_NO_AUTH_WHY,
    NEST_GIT_AMBIGUOUS_CREDS_WHY,
    NEST_GIT_BAD_CREDS_HOWTO,
    NEST_GIT_BOT_EMAIL,
    NEST_GIT_BOT_NAME,
    NEST_GIT_CATEGORY,
    NEST_GIT_HOST_ENABLED_KEY,
    NEST_GIT_HOWTO,
    NEST_GIT_NO_CREDS_WHY,
    NEST_GIT_NO_MATCH_HOWTO,
    NEST_GIT_NO_MATCH_WHY,
    NEST_GIT_PAT_KEY,
    NEST_GIT_PROBE_FAIL_WHY,
    NEST_GIT_SSH_KEY,
    NEST_GIT_TOKEN_NO_REPO_HOWTO,
    NEST_GIT_TOKEN_NO_REPO_WHY,
    NEST_GIT_TOKEN_REJECTED_HOWTO,
    NEST_GIT_TOKEN_REJECTED_WHY,
)

logger = logging.getLogger(__name__)

# Remote ops that talk to a host. Local add/commit/status do not.
_AUTH_GIT_SUBCOMMANDS = frozenset({
    "push", "fetch", "pull", "clone", "ls-remote",
})

# gh help/version/completion do not need a GitHub login.
_GH_LOCAL_SUBCOMMANDS = frozenset({"help", "completion", "version"})
_GH_LOCAL_FLAGS = frozenset({"--help", "-h", "--version"})

# Global git options that consume the next argv token.
_GIT_VALUE_OPTIONS = frozenset({
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--config-env",
    "--attr-source",
})

_AUTH_FAIL_MARKERS = (
    "authentication failed",
    "invalid username or password",
    "could not read username",
    "terminal prompts disabled",
    "username for '",
    "password for '",
    "permission denied (publickey)",
    "the requested url returned error: 401",
    "the requested url returned error: 403",
    "error: 401",
    "error: 403",
    "write access to repository not granted",
    "resource not accessible by personal access token",
    "bad credentials",
)

# Strong 403 / fine-grained PAT-without-repo hints from GitHub.
_REPO_ACCESS_MARKERS = (
    "the requested url returned error: 403",
    "error: 403",
    "write access to repository not granted",
    "resource not accessible by personal access token",
    "repository access",
    "permission to ",
)

# Strong 401 / wrong-or-expired token or SSH key hints.
_TOKEN_REJECTED_MARKERS = (
    "the requested url returned error: 401",
    "error: 401",
    "authentication failed",
    "invalid username or password",
    "bad credentials",
    "permission denied (publickey)",
    "could not read username",
    "terminal prompts disabled",
)

GitAuthFailureKind = Literal["token_rejected", "repo_access", "ambiguous"]

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
    """Return a decrypted nest-git secret. Empty when unset."""
    return read_secret(key)


def nest_git_pat() -> str:
    """Return the Default / legacy PAT, or empty."""
    migrate_legacy_store()
    return read_nest_git_secret(NEST_GIT_PAT_KEY)


def nest_git_ssh_key() -> str:
    """Return the Default / legacy SSH private key, or empty."""
    migrate_legacy_store()
    return read_nest_git_secret(NEST_GIT_SSH_KEY)


def nest_git_has_stored_creds() -> bool:
    """Return True when any named (or legacy) PAT/SSH is saved."""
    migrate_legacy_store()
    if any(credential_has_secrets(item.id) for item in load_credentials()):
        return True
    return bool(nest_git_pat() or nest_git_ssh_key())


def nest_git_auth_ready(
    *,
    remote: str | None = None,
    agent: Agent | None = None,
    parsed: ParsedCliCommand | None = None,
    cwd: str | None = None,
) -> bool:
    """Return True when a usable auth path exists (fail-closed otherwise).

    With a remote (or command + cwd), only a matching credential or the
    optional default counts. Host Enable remains a global opt-in.
    """
    if parsed is not None or cwd:
        remote = remote or resolve_git_remote(agent, parsed, cwd)
    if select_nest_git_credential(remote) is not None:
        return True
    if remote is None and nest_git_has_stored_creds():
        return True
    if not host_git_is_enabled():
        return False
    return probe_host_git_for_shell().ok


def write_nest_git_secret(key: str, value: str) -> None:
    """Write or clear one nest-git secret through Settings (bm1 wrap)."""
    write_secret(key, value or "")
    if key in {NEST_GIT_PAT_KEY, NEST_GIT_SSH_KEY}:
        ensure_default_from_legacy()


def is_git_cli(parsed: ParsedCliCommand) -> bool:
    """Return True when argv0 is git (path-stripped)."""
    name = Path(parsed.name).name.lower()
    return name in {"git", "git.exe"}


def is_gh_cli(parsed: ParsedCliCommand) -> bool:
    """Return True when argv0 is the GitHub CLI (path-stripped)."""
    name = Path(parsed.name).name.lower()
    return name in {"gh", "gh.exe"}


def gh_subcommand(args: tuple[str, ...] | list[str]) -> str:
    """Return the gh subcommand, skipping leading global options."""
    return git_subcommand(args)


def command_needs_gh_auth(parsed: ParsedCliCommand) -> bool:
    """Return True when this gh argv would need a GitHub login in Shell.

    Help/version/completion stay off this gate. Everything else is fail-closed
    because agent HOME is rewritten and GH_TOKEN is not in the Shell allowlist.
    Nest git → gh inject is parked — do not copy a PAT into gh env.
    """
    if not is_gh_cli(parsed):
        return False
    tokens = list(parsed.args)
    if any(token in _GH_LOCAL_FLAGS for token in tokens) and not any(
        not token.startswith("-") for token in tokens
    ):
        return False
    subcommand = gh_subcommand(tokens)
    if subcommand in _GH_LOCAL_SUBCOMMANDS:
        return False
    return True


def git_subcommand(args: tuple[str, ...] | list[str]) -> str:
    """Return the git subcommand, skipping leading global options.

    ``git -C dest --no-pager push`` must still classify as ``push``.
    """
    index = 0
    tokens = list(args)
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token.startswith("-"):
            key = token.split("=", 1)[0]
            if key in _GIT_VALUE_OPTIONS and "=" not in token:
                index += 2
                continue
            index += 1
            continue
        return token
    return tokens[index] if index < len(tokens) else ""


def command_needs_nest_git_auth(
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str,
) -> bool:
    """Return True when this CLI is a nest remote-git op that needs auth."""
    if not is_git_cli(parsed):
        return False
    subcommand = git_subcommand(parsed.args)
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
    migrate_legacy_store()
    creds = [redact_credential(item) for item in load_credentials()]
    default = next((item for item in creds if item.get("is_default")), None)
    pat = nest_git_pat()
    ssh = nest_git_ssh_key()
    if default is not None:
        pat_last4 = default.get("pat_last4")
        ssh_last4 = default.get("ssh_last4")
        has_pat = bool(default.get("has_pat"))
        has_ssh = bool(default.get("has_ssh"))
    else:
        pat_last4 = pat[-4:] if pat else None
        ssh_last4 = ssh[-4:] if ssh else None
        has_pat = bool(pat)
        has_ssh = bool(ssh)
    probe = probe_host_git_for_shell()
    return {
        "host_enabled": host_git_is_enabled(),
        "has_pat": has_pat,
        "pat_last4": pat_last4,
        "has_ssh": has_ssh,
        "ssh_last4": ssh_last4,
        "default_id": next((item["id"] for item in creds if item.get("is_default")), None),
        "credentials": creds,
        "probe_ok": probe.ok,
        "probe_via": probe.via or None,
        "probe_why": None if probe.ok else probe.why,
        "how_to": NEST_GIT_HOWTO,
        "match_how_to": NEST_GIT_NO_MATCH_HOWTO,
    }


def host_git_passthrough_env() -> dict[str, str]:
    """Host git vars the Shell may receive when host Enable is On."""
    extra: dict[str, str] = {}
    for name in _HOST_PASSTHROUGH_ENV:
        value = os.environ.get(name)
        if value:
            extra[name] = value
    return extra


def nest_git_shell_env(
    agent: Agent,
    parsed: ParsedCliCommand | None = None,
    cwd: str | None = None,
) -> dict[str, str]:
    """Extra env for one nest git Shell invocation. Never log the values.

    Applied on the shared Shell path for every git argv — not only when the
    nest-git gate matched — so a saved PAT still reaches ``git push``.
    The matching credential is chosen by :func:`select_nest_git_credential`.

    Parked: Nest git → gh subprocess inject. Never copy a PAT into ``GH_TOKEN``
    / ``GITHUB_TOKEN`` for a gh argv.
    """
    extra: dict[str, str] = {}
    if parsed is not None and is_gh_cli(parsed):
        return extra
    remote = resolve_git_remote(agent, parsed, cwd) if (parsed is not None or cwd) else ""
    chosen = select_nest_git_credential(remote or None)
    pat = ""
    ssh = ""
    if chosen is not None:
        pat, ssh = credential_secrets(chosen.id)
    elif parsed is None and cwd is None:
        pat, ssh = nest_git_pat(), nest_git_ssh_key()
    host = remote.split("/", 1)[0] if remote else "github.com"
    if pat:
        extra.update(_pat_env(pat, host=host or "github.com"))
    elif ssh:
        extra.update(_ssh_key_env(ssh))
    elif host_git_is_enabled() and probe_host_git_for_shell().ok:
        extra.update(host_git_passthrough_env())
    extra.setdefault("GIT_TERMINAL_PROMPT", "0")
    extra.setdefault("GCM_INTERACTIVE", "never")
    return extra


def github_compare_url(
    agent: Agent | None,
    parsed: ParsedCliCommand | None = None,
    cwd: str | None = None,
) -> str:
    """Return a public github.com compare URL, or empty when unknown.

    Branch and remote are read from the nest clone. No token material.
    """
    from urllib.parse import quote

    remote = resolve_git_remote(agent, parsed, cwd) if (parsed is not None or cwd) else ""
    if not remote.lower().startswith("github.com/"):
        return ""
    path = remote.split("/", 1)[1] if "/" in remote else ""
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return ""
    owner, repo = parts[0], parts[1]
    branch = repo_current_branch(agent, cwd)
    if not branch:
        return ""
    return f"https://github.com/{owner}/{repo}/compare/{quote(branch, safe='/@')}"


def gh_auth_blocked_message(
    agent: Agent | None = None,
    parsed: ParsedCliCommand | None = None,
    cwd: str | None = None,
    *,
    nest_ready: bool = False,
) -> str:
    """Return Blocked why + how-to for a gh auth miss. No token values."""
    url = github_compare_url(agent, parsed, cwd)
    if nest_ready and url:
        howto = GH_CLI_COMPARE_HOWTO.format(url=url)
    else:
        howto = GH_CLI_HOWTO
        if url and url not in howto:
            howto = f"{howto} Compare URL: {url}."
    return f"{GH_CLI_NO_AUTH_WHY}. {howto}"


def chosen_nest_git_credential(
    agent: Agent | None,
    parsed: ParsedCliCommand | None,
    cwd: str | None,
) -> NestGitCredential | None:
    """Return the credential the shared match path would inject."""
    remote = resolve_git_remote(agent, parsed, cwd)
    return select_nest_git_credential(remote or None)


def no_creds_blocked_message() -> str:
    """Return Blocked why + how-to when no auth path is ready."""
    return f"{NEST_GIT_NO_CREDS_WHY}. {NEST_GIT_HOWTO}"


def no_match_blocked_message() -> str:
    """Return Blocked why + how-to when saved creds exist but none match."""
    return f"{NEST_GIT_NO_MATCH_WHY}. {NEST_GIT_NO_MATCH_HOWTO}"


def auth_failed_blocked_message(kind: GitAuthFailureKind = "ambiguous") -> str:
    """Return Blocked why + how-to when GitHub rejected the saved creds."""
    why, howto = auth_failed_operator_copy(kind)
    return f"{why}. {howto}"


def auth_failed_operator_copy(kind: GitAuthFailureKind) -> tuple[str, str]:
    """Return ``(short why, card how-to)`` for one auth-reject class."""
    if kind == "token_rejected":
        return NEST_GIT_TOKEN_REJECTED_WHY, NEST_GIT_TOKEN_REJECTED_HOWTO
    if kind == "repo_access":
        return NEST_GIT_TOKEN_NO_REPO_WHY, NEST_GIT_TOKEN_NO_REPO_HOWTO
    return NEST_GIT_AMBIGUOUS_CREDS_WHY, NEST_GIT_BAD_CREDS_HOWTO


def classify_git_auth_failure(stdout: str, stderr: str) -> GitAuthFailureKind:
    """Split 401-style token reject from 403-style repo access when possible."""
    blob = f"{stdout or ''}\n{stderr or ''}".lower()
    has_repo = any(marker in blob for marker in _REPO_ACCESS_MARKERS)
    has_token = any(marker in blob for marker in _TOKEN_REJECTED_MARKERS)
    if has_repo and has_token:
        return "ambiguous"
    if has_repo:
        return "repo_access"
    if has_token:
        return "token_rejected"
    return "ambiguous"


def shell_output_looks_like_git_auth_failure(stdout: str, stderr: str) -> bool:
    """Return True when git asked for a username/password or rejected auth."""
    blob = f"{stdout or ''}\n{stderr or ''}".lower()
    return any(marker in blob for marker in _AUTH_FAIL_MARKERS)


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


def _pat_env(pat: str, host: str = "github.com") -> dict[str, str]:
    askpass = str(_ensure_askpass())
    extra = {
        "GIT_ASKPASS": askpass,
        "SSH_ASKPASS": askpass,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_CONFIG_NOSYSTEM": "1",
        "BOSSMOD_NEST_GIT_USERNAME": "x-access-token",
        "BOSSMOD_NEST_GIT_PASSWORD": pat,
        "GIT_AUTHOR_NAME": NEST_GIT_BOT_NAME,
        "GIT_AUTHOR_EMAIL": NEST_GIT_BOT_EMAIL,
        "GIT_COMMITTER_NAME": NEST_GIT_BOT_NAME,
        "GIT_COMMITTER_EMAIL": NEST_GIT_BOT_EMAIL,
    }
    extra.update(_https_username_inject_env(host))
    return extra


def _https_username_inject_env(host: str = "github.com") -> dict[str, str]:
    """HTTPS username ``x-access-token`` plus no interactive credential helper.

    Token stays in askpass env, never in the rewritten URL (no log leak).
    """
    name = (host or "github.com").strip() or "github.com"
    return {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.username",
        "GIT_CONFIG_VALUE_1": "x-access-token",
        "GIT_CONFIG_KEY_2": f"url.https://x-access-token@{name}/.insteadOf",
        "GIT_CONFIG_VALUE_2": f"https://{name}/",
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
