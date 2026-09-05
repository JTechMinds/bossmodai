"""Allowlisted extra host roots for named-path access.

This is not a full host mount. Built-in roots stay ``/me`` (agent workspace)
and ``/projects`` (``artifacts/projects``). Operators may add extra absolute
directories via the ``workspace_host_roots`` setting. Empty setting = no
extra host access. Path jail and company-file confinement both read this
list; approval does not bypass it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from core.bm_cli.filesystem import (
    agent_artifact_dir,
    is_denied_company_file,
    projects_artifact_root,
)

SETTING_KEY = "workspace_host_roots"
SETTING_CATEGORY = "cli_policy"

# Extra host roots may not be the filesystem root or these system trees.
_DENIED_SYSTEM_ROOTS = frozenset({
    Path("/etc"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
    Path("/root"),
})


class PathOutsideRootsError(ValueError):
    """Raised when a user-named path is outside the allowlisted roots."""


def parse_host_root_setting(raw: str | None) -> list[str]:
    """Split a setting value into candidate root strings.

    Accepts newline- or comma-separated absolute paths. Empty tokens
    and comment lines starting with ``#`` are ignored.
    """
    if raw is None:
        return []
    tokens: list[str] = []
    for line in str(raw).replace(",", "\n").splitlines():
        token = line.strip()
        if not token or token.startswith("#"):
            continue
        tokens.append(token)
    return tokens


def is_within_roots(path: Path, roots: Sequence[Path]) -> bool:
    """Return True if *path* is equal to or inside any allowed root."""
    resolved = Path(path).resolve()
    for root in roots:
        root_resolved = Path(root).resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return True
    return False


def validate_host_root(raw: str) -> Path:
    """Resolve and validate one extra host root directory.

    Raises
    ------
    ValueError
        If the path is relative, missing, not a directory, the filesystem
        root, or a denied system tree.
    """
    token = (raw or "").strip()
    if not token:
        raise ValueError("Host workspace root cannot be empty")
    path = Path(token).expanduser()
    if not path.is_absolute():
        raise ValueError(f"Host workspace root must be an absolute path: {token!r}")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ValueError(f"Cannot resolve host workspace root {token!r}: {exc}") from exc
    if resolved == Path(resolved.anchor):
        raise ValueError("Host workspace root cannot be the filesystem root")
    for denied in _DENIED_SYSTEM_ROOTS:
        denied_resolved = denied.resolve()
        if resolved == denied_resolved or denied_resolved in resolved.parents:
            raise ValueError(
                f"Host workspace root {str(resolved)!r} is a denied system directory"
            )
    if not resolved.exists():
        raise ValueError(f"Host workspace root does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"Host workspace root must be a directory: {resolved}")
    return resolved


def normalize_host_root_setting(raw: str | None) -> str:
    """Validate every configured root and return a canonical newline-joined value.

    Empty input stays empty (fail-closed: no extra host access).
    """
    tokens = parse_host_root_setting(raw)
    if not tokens:
        return ""
    seen: set[Path] = set()
    resolved: list[Path] = []
    for token in tokens:
        path = validate_host_root(token)
        if path in seen:
            continue
        seen.add(path)
        resolved.append(path)
    return "\n".join(str(path) for path in resolved)


def configured_host_roots(raw: str | None = None) -> tuple[Path, ...]:
    """Return validated extra host roots from *raw* or the live setting.

    Invalid stored tokens are skipped (fail-closed per entry) so a bad
    line cannot widen the jail.
    """
    if raw is None:
        from core import config

        raw = config.get(SETTING_KEY)
    roots: list[Path] = []
    seen: set[Path] = set()
    for token in parse_host_root_setting(raw):
        try:
            path = validate_host_root(token)
        except ValueError:
            continue
        if path in seen:
            continue
        seen.add(path)
        roots.append(path)
    return tuple(roots)


def named_path_roots(agent_storage_key: str | None = None) -> tuple[Path, ...]:
    """Return the real directories a named absolute path may resolve into.

    Always includes the shared projects mount. Includes the agent's
    personal workspace when *agent_storage_key* is provided. Extra host
    roots come from settings.
    """
    roots: list[Path] = [projects_artifact_root().resolve()]
    if agent_storage_key:
        roots.append(agent_artifact_dir(agent_storage_key).resolve())
    roots.extend(configured_host_roots())
    # Preserve order, drop duplicates.
    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        unique.append(root)
    return tuple(unique)


def allowed_workspace_roots(agent_storage_key: str) -> tuple[Path, ...]:
    """Return path-jail roots for one agent: workspace + projects + host roots."""
    return named_path_roots(agent_storage_key)


def describe_allowed_roots(
    *,
    extra_roots: Sequence[Path] | None = None,
    include_virtual: bool = True,
) -> str:
    """Return a short human-readable description of the current allowlist."""
    extras = tuple(Path(root).resolve() for root in (extra_roots if extra_roots is not None else configured_host_roots()))
    parts: list[str] = []
    if include_virtual:
        parts.append('"/me"')
        parts.append('"/projects"')
    if extras:
        parts.append("configured host roots: " + ", ".join(str(root) for root in extras))
    else:
        parts.append("no extra host roots")
    return ", ".join(parts)


def denial_message(raw_path: str, *, extra_roots: Sequence[Path] | None = None) -> str:
    """Return a clear denial for a path outside the allowlisted roots."""
    return (
        f"Path {raw_path!r} is outside the allowed workspace roots "
        f"({describe_allowed_roots(extra_roots=extra_roots)}). "
        "This is an allowlisted-roots model, not a full host mount."
    )


def resolve_absolute_under_roots(
    raw_path: str,
    roots: Sequence[Path],
    *,
    deny_company_backup_suffix: bool = False,
) -> Path:
    """Resolve an absolute user-named path and require it stay inside *roots*.

    Raises
    ------
    PathOutsideRootsError
        If the path is not absolute or escapes every root.
    ValueError
        If the path cannot be resolved or is a denied backup/database suffix.
    """
    token = (raw_path or "").strip()
    if not token:
        raise PathOutsideRootsError(denial_message(raw_path, extra_roots=roots))
    path = Path(token).expanduser()
    if not path.is_absolute():
        raise PathOutsideRootsError(denial_message(raw_path, extra_roots=roots))
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ValueError(f"Cannot resolve path {raw_path!r}: {exc}") from exc
    if not is_within_roots(resolved, roots):
        raise PathOutsideRootsError(denial_message(raw_path, extra_roots=roots))
    if deny_company_backup_suffix and is_denied_company_file(resolved):
        raise ValueError("File type is not allowed in the company workspace")
    return resolved


def looks_like_named_absolute_path(raw_path: str) -> bool:
    """Return True when *raw_path* should be treated as a host/absolute name.

    Historical company-browser paths under ``/projects`` stay relative.
    Virtual CLI mounts ``/me`` and ``/projects`` are not named host paths.
    """
    cleaned = (raw_path or "").replace("\\", "/").strip()
    if not cleaned.startswith("/"):
        return False
    if cleaned in {"/", "/me", "/projects"}:
        return False
    if cleaned.startswith("/me/") or cleaned.startswith("/projects/"):
        return False
    return Path(cleaned).is_absolute()
