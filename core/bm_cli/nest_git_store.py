"""Named Nest git credentials — Settings JSON plus bm1 secrets, match by remote.

One shared select path. Push/fetch/clone/inject all call
:func:`select_nest_git_credential` instead of per-command if/elses.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

import db
from core.bm_cli.types import ParsedCliCommand
from core.models import Agent
from core.models.nest_git import (
    NEST_GIT_CATEGORY,
    NEST_GIT_CREDENTIALS_KEY,
    NEST_GIT_DEFAULT_CREDENTIAL_ID,
    NEST_GIT_DEFAULT_CREDENTIAL_LABEL,
    NEST_GIT_EMPTY_CREDS,
    NEST_GIT_PAT_KEY,
    NEST_GIT_SSH_KEY,
)
from db.secret_store import decrypt_setting_value

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

_URL_MARKERS = ("://", "git@", ".git")


@dataclass(frozen=True, slots=True)
class NestGitCredential:
    """One named Nest git credential. Secrets are not on this object."""

    id: str
    label: str
    match: str
    is_default: bool = False

    def patterns(self) -> tuple[str, ...]:
        """Return match patterns, skipping blanks."""
        return tuple(
            part.strip()
            for part in (self.match or "").split(",")
            if part.strip()
        )


def pat_setting_key(credential_id: str) -> str:
    """Return the Settings key for this credential’s access token."""
    if credential_id == NEST_GIT_DEFAULT_CREDENTIAL_ID:
        return NEST_GIT_PAT_KEY
    return f"nest_git_pat_{credential_id}"


def ssh_setting_key(credential_id: str) -> str:
    """Return the Settings key for this credential’s SSH key."""
    if credential_id == NEST_GIT_DEFAULT_CREDENTIAL_ID:
        return NEST_GIT_SSH_KEY
    return f"nest_git_ssh_{credential_id}"


def read_secret(key: str) -> str:
    """Return a decrypted nest-git secret. Empty when unset."""
    from db.crud import query_one

    row = query_one("SELECT value FROM settings WHERE key = $1", [key])
    raw = "" if row is None or row.get("value") is None else str(row["value"])
    return (decrypt_setting_value(key, raw) or "").strip()


def write_secret(key: str, value: str) -> None:
    """Write or clear one nest-git secret through Settings (bm1 wrap)."""
    from core import config

    db.set_setting(key, value or "", NEST_GIT_CATEGORY)
    config.reload()


def credential_secrets(credential_id: str) -> tuple[str, str]:
    """Return ``(pat, ssh)`` for *credential_id*."""
    return (
        read_secret(pat_setting_key(credential_id)),
        read_secret(ssh_setting_key(credential_id)),
    )


def credential_has_secrets(credential_id: str) -> bool:
    """Return True when the credential has a PAT or SSH key."""
    pat, ssh = credential_secrets(credential_id)
    return bool(pat or ssh)


def normalize_remote_url(url: str | None) -> str:
    """Return ``host/owner/repo`` (or host) for HTTPS, SSH, and scp remotes."""
    text = unquote((url or "").strip())
    if not text:
        return ""
    if text.startswith("git@") and ":" in text:
        host, _, path = text.partition(":")
        host = host[4:]
        return _join_host_path(host, path)
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        host = parsed.hostname or parsed.netloc.split("@")[-1]
        return _join_host_path(host, parsed.path)
    stripped = text.split("@")[-1]
    return _join_host_path("", stripped) if "/" in stripped else stripped.lower()


def suggested_match_for_remote(remote: str | None) -> str:
    """Return ``host/owner/*`` when owner is known, else the exact remote."""
    norm = normalize_remote_url(remote)
    if not norm:
        return ""
    parts = [part for part in norm.split("/") if part]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}/*"
    return norm


def suggested_label_for_remote(remote: str | None) -> str:
    """Return the owner (or host) as a beginner credential name."""
    norm = normalize_remote_url(remote)
    parts = [part for part in norm.split("/") if part]
    if len(parts) >= 2:
        return parts[1]
    if parts:
        return parts[0]
    return ""


def match_score(pattern: str, remote: str) -> int:
    """Return specificity: 3 exact repo, 2 owner/*, 1 host, 0 no match."""
    pat = normalize_remote_url(pattern) if "://" in pattern or pattern.startswith("git@") else _normalize_pattern(pattern)
    rem = normalize_remote_url(remote) if "://" in remote or remote.startswith("git@") else _normalize_pattern(remote)
    if not pat or not rem:
        return 0
    if pat == rem:
        return 3
    if pat.endswith("/*"):
        prefix = pat[:-2]
        if rem == prefix or rem.startswith(prefix + "/"):
            return 2
        return 0
    if "/" not in pat:
        return 1 if rem.split("/", 1)[0] == pat else 0
    return 0


def select_nest_git_credential(remote: str | None) -> NestGitCredential | None:
    """Pick the matching credential, then the optional default.

    *remote* may be a URL or already-normalized ``host/owner/repo``.
    """
    store = load_credentials()
    rem = normalize_remote_url(remote) if remote and ("://" in remote or remote.startswith("git@")) else _normalize_pattern(remote)
    best: NestGitCredential | None = None
    best_score = 0
    if rem:
        for cred in store:
            if not credential_has_secrets(cred.id):
                continue
            for pattern in cred.patterns():
                score = match_score(pattern, rem)
                if score > best_score:
                    best = cred
                    best_score = score
        if best is not None:
            return best
    default = next((item for item in store if item.is_default), None)
    if default is not None and credential_has_secrets(default.id):
        return default
    return None


def resolve_git_remote(
    agent: Agent | None,
    parsed: ParsedCliCommand | None,
    cwd: str | None,
) -> str:
    """Return a normalized remote for this git command + cwd, or empty."""
    hint = command_remote_hint(parsed) if parsed is not None else None
    if hint and _looks_like_url(hint):
        return normalize_remote_url(hint)
    work_tree = git_work_tree(parsed, cwd) if parsed is not None else cwd
    remote_name = hint if hint and not _looks_like_url(hint) else "origin"
    url = repo_remote_url(agent, work_tree, remote_name)
    if not url and remote_name != "origin":
        url = repo_remote_url(agent, work_tree, "origin")
    return normalize_remote_url(url)


def command_remote_hint(parsed: ParsedCliCommand | None) -> str | None:
    """Return a URL or remote name from git argv, skipping flags."""
    if parsed is None:
        return None
    tokens = list(parsed.args)
    index = 0
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
        # subcommand
        index += 1
        break
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            if "=" not in token and token in {"-o", "--origin", "--upload-pack", "--exec", "--depth", "--shallow-since", "--recurse-submodules"}:
                index += 2
                continue
            index += 1
            continue
        return token
    return None


def git_work_tree(parsed: ParsedCliCommand | None, cwd: str | None) -> str:
    """Return ``-C`` dest when present, else *cwd*."""
    if parsed is not None:
        tokens = list(parsed.args)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "-C" and index + 1 < len(tokens):
                return tokens[index + 1]
            if token.startswith("-C") and len(token) > 2 and not token.startswith("-C-"):
                return token[2:]
            if token.startswith("-"):
                key = token.split("=", 1)[0]
                if key in _GIT_VALUE_OPTIONS and "=" not in token:
                    index += 2
                    continue
                index += 1
                continue
            break
    return (cwd or "").strip()


def repo_remote_url(agent: Agent | None, cwd: str | None, name: str = "origin") -> str:
    """Read ``remote.<name>.url`` from the repo at *cwd* without shelling git."""
    repo = _repo_root(agent, cwd)
    if repo is None:
        return ""
    config = _git_config_path(repo)
    if config is None or not config.is_file():
        return ""
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return ""
    return _parse_git_config_remote(text, name)


def load_credentials() -> list[NestGitCredential]:
    """Return named credentials, migrating the single store when needed."""
    migrate_legacy_store()
    payload = _read_store()
    default_id = str(payload.get("default_id") or "") or None
    items: list[NestGitCredential] = []
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            continue
        cred_id = str(raw.get("id") or "").strip()
        if not cred_id:
            continue
        items.append(
            NestGitCredential(
                id=cred_id,
                label=str(raw.get("label") or cred_id).strip() or cred_id,
                match=str(raw.get("match") or "").strip(),
                is_default=cred_id == default_id,
            )
        )
    return items


def migrate_legacy_store() -> NestGitCredential | None:
    """Promote the single PAT/SSH store to a named Default. Idempotent."""
    payload = _read_store()
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    if items:
        return None
    pat = read_secret(NEST_GIT_PAT_KEY)
    ssh = read_secret(NEST_GIT_SSH_KEY)
    if not pat and not ssh:
        return None
    cred = NestGitCredential(
        id=NEST_GIT_DEFAULT_CREDENTIAL_ID,
        label=NEST_GIT_DEFAULT_CREDENTIAL_LABEL,
        match="",
        is_default=True,
    )
    _write_store({"default_id": cred.id, "items": [_item_payload(cred)]})
    return cred


def add_credential(
    *,
    label: str,
    match: str = "",
    pat: str | None = None,
    ssh_key: str | None = None,
    is_default: bool | None = None,
    credential_id: str | None = None,
) -> NestGitCredential:
    """Create one named credential. First secret-bearing row becomes default."""
    token = (pat or "").strip()
    key = (ssh_key or "").strip()
    if not token and not key:
        raise ValueError(NEST_GIT_EMPTY_CREDS)
    store = load_credentials()
    cred_id = (credential_id or "").strip() or uuid.uuid4().hex[:12]
    if any(item.id == cred_id for item in store):
        raise ValueError(f"Nest git credential {cred_id} already exists")
    become_default = bool(is_default) if is_default is not None else not any(item.is_default for item in store)
    if cred_id == NEST_GIT_DEFAULT_CREDENTIAL_ID:
        write_secret(NEST_GIT_PAT_KEY, token)
        write_secret(NEST_GIT_SSH_KEY, key)
    else:
        write_secret(pat_setting_key(cred_id), token)
        write_secret(ssh_setting_key(cred_id), key)
    cred = NestGitCredential(
        id=cred_id,
        label=(label or "").strip() or cred_id,
        match=(match or "").strip(),
        is_default=become_default,
    )
    default_id = cred.id if become_default else next((item.id for item in store if item.is_default), None)
    items = [_item_payload(item) for item in store] + [_item_payload(cred)]
    _write_store({"default_id": default_id, "items": items})
    return cred


def update_credential(
    credential_id: str,
    *,
    label: str | None = None,
    match: str | None = None,
    pat: str | None = None,
    ssh_key: str | None = None,
    clear_pat: bool = False,
    clear_ssh: bool = False,
    is_default: bool | None = None,
) -> NestGitCredential:
    """Edit one named credential. Empty secret fields keep the current value."""
    store = load_credentials()
    existing = next((item for item in store if item.id == credential_id), None)
    if existing is None:
        raise KeyError(credential_id)
    if clear_pat:
        write_secret(pat_setting_key(credential_id), "")
    elif (pat or "").strip():
        write_secret(pat_setting_key(credential_id), pat.strip())
    if clear_ssh:
        write_secret(ssh_setting_key(credential_id), "")
    elif (ssh_key or "").strip():
        write_secret(ssh_setting_key(credential_id), ssh_key.strip())
    if not credential_has_secrets(credential_id) and not (existing.is_default and not store):
        # Allow metadata-only edits; clearing both secrets is fine.
        pass
    next_label = existing.label if label is None else (label.strip() or existing.label)
    next_match = existing.match if match is None else match.strip()
    default_id = next((item.id for item in store if item.is_default), None)
    if is_default is True:
        default_id = credential_id
    elif is_default is False and default_id == credential_id:
        default_id = None
    updated = NestGitCredential(
        id=credential_id,
        label=next_label,
        match=next_match,
        is_default=credential_id == default_id,
    )
    items = [
        _item_payload(updated if item.id == credential_id else item)
        for item in store
    ]
    _write_store({"default_id": default_id, "items": items})
    return updated


def append_match(credential_id: str, remote: str) -> NestGitCredential:
    """Add an exact remote pattern so this credential matches next time."""
    store = load_credentials()
    existing = next((item for item in store if item.id == credential_id), None)
    if existing is None:
        raise KeyError(credential_id)
    exact = normalize_remote_url(remote) or _normalize_pattern(remote)
    if not exact:
        return existing
    current = list(existing.patterns())
    if any(match_score(pattern, exact) >= 2 for pattern in current):
        return existing
    if exact in current:
        return existing
    current.append(exact)
    return update_credential(credential_id, match=", ".join(current))


def delete_credential(credential_id: str) -> None:
    """Remove one credential and its secrets."""
    store = load_credentials()
    if not any(item.id == credential_id for item in store):
        raise KeyError(credential_id)
    write_secret(pat_setting_key(credential_id), "")
    write_secret(ssh_setting_key(credential_id), "")
    remaining = [item for item in store if item.id != credential_id]
    default_id = next((item.id for item in remaining if item.is_default), None)
    if default_id == credential_id:
        default_id = None
    _write_store({
        "default_id": default_id,
        "items": [_item_payload(item) for item in remaining],
    })


def ensure_default_from_legacy() -> NestGitCredential | None:
    """Write Default metadata after a legacy PAT/SSH save. Idempotent."""
    migrated = migrate_legacy_store()
    if migrated is not None:
        return migrated
    store = load_credentials()
    if store:
        return next((item for item in store if item.is_default), store[0])
    pat = read_secret(NEST_GIT_PAT_KEY)
    ssh = read_secret(NEST_GIT_SSH_KEY)
    if not pat and not ssh:
        return None
    return migrate_legacy_store()


def redact_credential(cred: NestGitCredential) -> dict[str, Any]:
    """Settings / card payload. No secret material."""
    pat, ssh = credential_secrets(cred.id)
    return {
        "id": cred.id,
        "label": cred.label,
        "match": cred.match,
        "is_default": cred.is_default,
        "has_pat": bool(pat),
        "pat_last4": pat[-4:] if pat else None,
        "has_ssh": bool(ssh),
        "ssh_last4": ssh[-4:] if ssh else None,
    }


def _read_store() -> dict[str, Any]:
    from db.crud import query_one

    row = query_one("SELECT value FROM settings WHERE key = $1", [NEST_GIT_CREDENTIALS_KEY])
    raw = "" if row is None or row.get("value") is None else str(row["value"]).strip()
    if not raw:
        return {"default_id": None, "items": []}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"default_id": None, "items": []}
    if not isinstance(payload, dict):
        return {"default_id": None, "items": []}
    payload.setdefault("default_id", None)
    payload.setdefault("items", [])
    return payload


def _write_store(payload: dict[str, Any]) -> None:
    from core import config

    db.set_setting(NEST_GIT_CREDENTIALS_KEY, json.dumps(payload, separators=(",", ":")), NEST_GIT_CATEGORY)
    config.reload()


def _item_payload(cred: NestGitCredential) -> dict[str, str]:
    return {"id": cred.id, "label": cred.label, "match": cred.match}


def _normalize_pattern(value: str | None) -> str:
    text = (value or "").strip().lower().rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^ssh://", "", text)
    if text.startswith("git@"):
        text = text[4:].replace(":", "/", 1)
    if "@" in text and "/" in text:
        text = text.split("@", 1)[-1]
    return text


def _join_host_path(host: str, path: str) -> str:
    clean_host = (host or "").strip().lower().split(":")[0]
    parts = [part for part in path.strip("/").split("/") if part]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][:-4]
    if clean_host:
        return "/".join([clean_host, *parts])
    return "/".join(parts).lower()


def _looks_like_url(token: str) -> bool:
    text = (token or "").strip()
    if not text:
        return False
    if any(marker in text for marker in _URL_MARKERS):
        return True
    return text.startswith("github.com/") or text.startswith("gitlab.")


def _repo_root(agent: Agent | None, cwd: str | None) -> Path | None:
    token = (cwd or "").strip()
    if not token:
        return None
    if agent is not None:
        try:
            from core.bm_cli.virtual_fs import resolve_cli_path

            resolved = resolve_cli_path(agent.storage_key, token, None)
            if resolved.real_path is not None:
                return resolved.real_path
        except Exception:
            pass
    candidate = Path(token)
    return candidate if candidate.exists() else None


def _git_config_path(repo: Path) -> Path | None:
    git = repo / ".git"
    if git.is_file():
        try:
            text = git.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in text.splitlines():
            if line.lower().startswith("gitdir:"):
                pointer = Path(line.split(":", 1)[1].strip())
                if not pointer.is_absolute():
                    pointer = (repo / pointer).resolve()
                config = pointer / "config"
                return config if config.is_file() else None
        return None
    if git.is_dir():
        config = git / "config"
        return config if config.is_file() else None
    return None


def _parse_git_config_remote(text: str, name: str) -> str:
    wanted = f'remote "{name.lower()}"'
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != wanted:
            continue
        key, _, value = line.partition("=")
        if key.strip().lower() == "url":
            return value.strip().strip('"').strip("'")
    return ""
