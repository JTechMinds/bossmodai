"""Workspace preference consent: clone, branch, edit-host, or cancel.

When a mutating CLI command targets a named host path that is already
allowlisted, do not write the host folder until the operator chooses.
Desk / ``/me`` stays the default workspace. Cancel is fail-closed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

import db
from core.bm_cli.filesystem import agent_artifact_dir, slugify_name
from core.bm_cli.host_roots import (
    denial_message,
    is_within_roots,
    looks_like_named_absolute_path,
    named_path_roots,
)
from core.bm_cli.host_path_consent import (
    _clean_channel_id,
    _enqueue_resume,
    canonical_host_path,
    looks_like_command_flag,
    resolve_consent_host_path,
)
from core.bm_cli.results import consent_required_result, error_result, success_result
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models import Agent
from core.models.host_path_consent import (
    WORKSPACE_PREFERENCE_BODY,
    WORKSPACE_PREFERENCE_KIND,
    WORKSPACE_PREFERENCE_TITLE,
    HostPathConsentRequest,
)

def workspace_preference_scopes_match(
    existing: HostPathConsentRequest,
    *,
    path: str,
    grant_root: str,
) -> bool:
    """Return True when a new write is nested under the same preference grant."""
    if (existing.grant_root or "").strip() and (existing.grant_root or "").strip() == (grant_root or "").strip():
        return True
    return _nested_host_paths(existing.path, path) or _nested_host_paths(
        existing.grant_root, path
    ) or _nested_host_paths(existing.grant_root, grant_root)


def _nested_host_paths(left: str | None, right: str | None) -> bool:
    a = canonical_host_path(left or "")
    b = canonical_host_path(right or "")
    if not a or not b:
        return False
    if a == b:
        return True
    prefix_a = a.rstrip("/") + "/"
    prefix_b = b.rstrip("/") + "/"
    return b.startswith(prefix_a) or a.startswith(prefix_b)


def _resolved_preference_for_write(
    *,
    agent_id: str,
    path: str,
    grant_root: str,
    task_id: str | None,
) -> HostPathConsentRequest | None:
    exact = db.find_workspace_preference_for_scope(agent_id, path, task_id=task_id)
    if exact is not None:
        return exact
    for row in db.list_consent_requests(agent_id=agent_id, limit=80):
        if (row.card_kind or "") != WORKSPACE_PREFERENCE_KIND:
            continue
        if row.status not in {"edit_host", "cloned", "branched", "denied"}:
            continue
        if task_id:
            if row.task_id != task_id:
                continue
        elif row.task_id:
            continue
        if workspace_preference_scopes_match(row, path=path, grant_root=grant_root):
            return row
    return None


def _pending_preference_for_write(
    *,
    agent_id: str,
    path: str,
    grant_root: str,
) -> HostPathConsentRequest | None:
    exact = db.find_pending_workspace_preference(agent_id, path)
    if exact is not None:
        return exact
    for row in db.list_consent_requests(agent_id=agent_id, status="pending", limit=80):
        if (row.card_kind or "") != WORKSPACE_PREFERENCE_KIND:
            continue
        if workspace_preference_scopes_match(row, path=path, grant_root=grant_root):
            return row
    return None


MUTATING_CLI_COMMANDS = frozenset({
    "write",
    "append",
    "bwrite",
    "mkdir",
    "repsect",
    "rewsect",
})

_HOST_WORK_DIR = "host-work"


def cwd_is_nested_clone_repo(agent: Agent, cwd: str) -> bool:
    """Return True when *cwd* sits in a git repo other than the agent /me root.

    Branch/workspace-copy clones live under ``/me/host-work/<name>`` and keep
    their own ``.git``. Virtual ``git`` always targets the agent workspace
    repo, so clone status/commit/push must use the shell executor.
    """
    try:
        resolved = resolve_cli_path(agent.storage_key, cwd, ".")
    except (OSError, ValueError):
        return False
    if resolved is None or resolved.real_path is None:
        return False
    git_root = find_git_root(Path(resolved.real_path))
    if git_root is None:
        return False
    try:
        workspace = agent_artifact_dir(agent.storage_key).resolve()
        git_root = git_root.resolve()
    except OSError:
        return False
    if git_root == workspace:
        return False
    try:
        git_root.relative_to(workspace)
    except ValueError:
        # A git root above the agent workspace (the host checkout) is not a clone.
        return False
    return True


def find_git_root(path: Path) -> Path | None:
    """Return the git repository root containing *path*, if any."""
    try:
        current = path if path.exists() and path.is_dir() else path.parent
        current = current.resolve()
    except OSError:
        current = path if path.is_dir() else path.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def is_mutating_named_host_command(parsed: ParsedCliCommand) -> bool:
    """Return True when this CLI would write a named host path."""
    if parsed.name not in MUTATING_CLI_COMMANDS:
        return False
    return named_host_path_from_command(parsed) is not None


def named_host_path_from_command(parsed: ParsedCliCommand) -> str | None:
    """Return the first named absolute host path in a parsed CLI command."""
    for arg in parsed.args:
        token = str(arg).strip()
        if looks_like_command_flag(token):
            continue
        if looks_like_named_absolute_path(token):
            return token
    return None


def maybe_pause_for_workspace_preference(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd: str,
    task_id: str | None,
    channel_id: str | None,
) -> BossModCliResult | None:
    """Pause mutating host writes until the operator picks a workspace preference."""
    if parsed.name not in MUTATING_CLI_COMMANDS:
        return None
    raw_path = named_host_path_from_command(parsed)
    if not raw_path:
        return None
    resolved = resolve_consent_host_path(raw_path)
    check_path = resolved[0] if resolved is not None else raw_path
    if not _host_path_is_allowlisted(agent, check_path, task_id) and not _host_path_is_allowlisted(
        agent, raw_path, task_id
    ):
        return None
    if resolved is None:
        return None
    path, grant_root = resolved
    prior = db.find_workspace_preference_for_scope(agent.id, path, task_id=task_id)
    if prior is None:
        prior = _resolved_preference_for_write(
            agent_id=agent.id,
            path=path,
            grant_root=str(grant_root),
            task_id=task_id,
        )
    if prior is not None and prior.status == "edit_host":
        return None
    return request_workspace_preference(
        agent=agent,
        raw_path=raw_path,
        command=parsed.raw,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=channel_id,
    )


def request_workspace_preference(
    *,
    agent: Agent,
    raw_path: str,
    command: str,
    content: str | None,
    cwd: str | None,
    task_id: str | None,
    channel_id: str | None = None,
) -> BossModCliResult:
    """Open the workspace-preference card, or apply a prior explicit choice."""
    resolved = resolve_consent_host_path(raw_path)
    if resolved is None:
        return error_result(
            command,
            denial_message(raw_path),
            cwd=cwd,
            executor="virtual",
        )
    path, grant_root = resolved

    prior = _resolved_preference_for_write(
        agent_id=agent.id,
        path=path,
        grant_root=str(grant_root),
        task_id=task_id,
    )
    if prior is not None and prior.status != "edit_host":
        return _result_for_resolved_preference(prior, command=command, cwd=cwd)

    origin_channel = _clean_channel_id(channel_id)
    if origin_channel and db.is_channel_archived(origin_channel):
        from core.models.channel import THREAD_ARCHIVED_CONSENT_DENY

        return error_result(command, THREAD_ARCHIVED_CONSENT_DENY, cwd=cwd, executor="virtual")

    pending = _pending_preference_for_write(
        agent_id=agent.id,
        path=path,
        grant_root=str(grant_root),
    )
    if pending is not None:
        if origin_channel and not pending.channel_id:
            pending = db.bind_consent_channel(pending.id, origin_channel) or pending
        return consent_required_result(
            command,
            _preference_message(pending),
            cwd=cwd,
            consent_request=pending,
            reused=True,
        )

    is_git = find_git_root(Path(path)) is not None
    request = db.create_consent_request(
        agent_id=agent.id,
        path=path,
        grant_root=str(grant_root),
        reason=WORKSPACE_PREFERENCE_BODY,
        command=command,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=origin_channel,
        card_kind=WORKSPACE_PREFERENCE_KIND,
        is_git=is_git,
    )
    return consent_required_result(
        command,
        _preference_message(request),
        cwd=cwd,
        consent_request=request,
        reused=False,
    )


async def resume_workspace_preference(
    request_id: str,
    *,
    decision: WorkspaceDecision,
    services: Any,
    decision_by: str = "human",
    note: str | None = None,
    enqueue_resume: bool = True,
    omit_origin_channel: bool = False,
) -> HostPathConsentRequest | None:
    """Apply Clone / Branch / Edit host / Cancel and wake the waiting agent."""
    existing = db.get_consent_request(request_id)
    if existing is None or existing.status != "pending":
        return None
    if (existing.card_kind or "host_path") != WORKSPACE_PREFERENCE_KIND:
        return None

    if decision == "cancel":
        updated = db.resolve_consent_request(
            request_id,
            status="denied",
            decision_by=decision_by,
            decision_note=note or "Cancelled. Host writes stay blocked.",
        )
        if updated is None:
            return None
        await _enqueue_resume(
            updated,
            status="denied",
            services=services,
            note=updated.decision_note,
            enqueue_resume=enqueue_resume,
            omit_origin_channel=omit_origin_channel,
        )
        return updated

    if decision == "edit_host":
        updated = db.resolve_consent_request(
            request_id,
            status="edit_host",
            decision_by=decision_by,
            decision_note=note or "Edit host directly (not advised).",
        )
        if updated is None:
            return None
        await _enqueue_resume(
            updated,
            status="edit_host",
            services=services,
            enqueue_resume=enqueue_resume,
            omit_origin_channel=omit_origin_channel,
        )
        return updated

    if decision == "clone":
        dest = _clone_into_workspace(existing)
        updated = db.resolve_consent_request(
            request_id,
            status="cloned",
            decision_by=decision_by,
            decision_note=note or f"Cloned into workspace at {dest}.",
            clone_dest=dest,
        )
        if updated is None:
            return None
        await _enqueue_resume(
            updated,
            status="cloned",
            services=services,
            enqueue_resume=enqueue_resume,
            omit_origin_channel=omit_origin_channel,
        )
        return updated

    if decision != "branch":
        raise ValueError(f"Unsupported workspace preference: {decision}")

    git_root = find_git_root(Path(existing.path))
    if git_root is None:
        raise ValueError("Make a branch is only available for git repositories.")
    dest, branch = _clone_and_branch(existing, git_root=git_root)
    updated = db.resolve_consent_request(
        request_id,
        status="branched",
        decision_by=decision_by,
        decision_note=note or f"Cloned to {dest} on branch {branch}.",
        clone_dest=dest,
    )
    if updated is None:
        return None
    await _enqueue_resume(
        updated,
        status="branched",
        services=services,
        enqueue_resume=enqueue_resume,
        omit_origin_channel=omit_origin_channel,
    )
    return updated


def _host_path_is_allowlisted(agent: Agent, raw_path: str, task_id: str | None) -> bool:
    from core.bm_cli.consent_scope import ConsentScope, host_path_consent_scope

    token = host_path_consent_scope.set(ConsentScope(agent_id=agent.id, task_id=task_id))
    try:
        resolved = Path(raw_path).expanduser().resolve()
        extras = named_path_roots(agent.storage_key)
        # Desk / projects are the default workspace; this card is for extra host roots.
        from core.bm_cli.filesystem import agent_artifact_dir, projects_artifact_root

        desk = agent_artifact_dir(agent.storage_key).resolve()
        projects = projects_artifact_root().resolve()
        if is_within_roots(resolved, (desk, projects)):
            return False
        host_roots = tuple(root for root in extras if root not in {desk, projects})
        return is_within_roots(resolved, host_roots)
    except OSError:
        return False
    finally:
        host_path_consent_scope.reset(token)


def _preference_message(request: HostPathConsentRequest) -> str:
    return (
        f"{WORKSPACE_PREFERENCE_TITLE} {WORKSPACE_PREFERENCE_BODY} "
        f"Named host path: {request.path}."
    )


def _result_for_resolved_preference(
    prior: HostPathConsentRequest,
    *,
    command: str,
    cwd: str | None,
) -> BossModCliResult:
    if prior.status == "edit_host":
        return success_result(
            command=command,
            detail=f"Workspace preference: edit host directly for {prior.path}.",
            kind="workspace_preference_edit_host",
            data={"workspace_preference": prior.as_card(), "already_chosen": True},
            sections=[("WORKSPACE PREFERENCE", ["Edit host directly was already chosen for this path."])],
            cwd=cwd,
            executor="virtual",
        )
    if prior.status in {"cloned", "branched"}:
        dest = prior.clone_dest or "/me"
        return error_result(
            command,
            (
                f"Host writes stay blocked. Work in the agent workspace copy at {dest} "
                "(desk / /me is the default workspace)."
            ),
            cwd=cwd,
            executor="virtual",
            kind="host_deny",
        )
    return error_result(
        command,
        (
            f"Host writes stay blocked for {prior.path!r}. "
            "The operator cancelled workspace preference for this path."
        ),
        cwd=cwd,
        executor="virtual",
        kind="host_deny",
    )


def _clone_into_workspace(request: HostPathConsentRequest, *, git_root: Path | None = None) -> str:
    agent = db.get_agent(request.agent_id)
    if agent is None:
        raise ValueError("Agent not found")
    src = Path(git_root or request.path)
    if not src.exists():
        src = Path(request.path)
    dest_root = agent_artifact_dir(agent.storage_key) / _HOST_WORK_DIR
    dest_root.mkdir(parents=True, exist_ok=True)
    name = slugify_name(src.name or "host")
    dest = dest_root / name
    suffix = 2
    while dest.exists():
        dest = dest_root / f"{name}-{suffix}"
        suffix += 1
    if src.is_dir():
        shutil.copytree(src, dest)
    elif src.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    return f"/me/{_HOST_WORK_DIR}/{dest.name}"


def _clone_and_branch(request: HostPathConsentRequest, *, git_root: Path) -> tuple[str, str]:
    dest_virtual = _clone_into_workspace(request, git_root=git_root)
    agent = db.get_agent(request.agent_id)
    if agent is None:
        raise ValueError("Agent not found")
    real = agent_artifact_dir(agent.storage_key) / _HOST_WORK_DIR / Path(dest_virtual).name
    branch = f"bossmod-work-{(request.id or 'work')[:8]}"
    result = subprocess.run(
        ["git", "-C", str(real), "checkout", "-B", branch],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "Could not create a git branch in the workspace clone.")
    return dest_virtual, branch
