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
    grantable_host_root,
    is_within_roots,
    looks_like_named_absolute_path,
    named_path_roots,
)
from core.bm_cli.host_path_consent import (
    _clean_channel_id,
    _enqueue_resume,
    canonical_host_path,
)
from core.bm_cli.results import consent_required_result, error_result, success_result
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.models import Agent
from core.models.host_path_consent import (
    WORKSPACE_PREFERENCE_BODY,
    WORKSPACE_PREFERENCE_KIND,
    WORKSPACE_PREFERENCE_TITLE,
    HostPathConsentRequest,
)

WorkspaceDecision = Literal["clone", "branch", "edit_host", "cancel"]

MUTATING_CLI_COMMANDS = frozenset({
    "write",
    "append",
    "bwrite",
    "mkdir",
    "repsect",
    "rewsect",
})

_HOST_WORK_DIR = "host-work"


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
    if not _host_path_is_allowlisted(agent, raw_path, task_id):
        return None
    prior = db.find_workspace_preference_for_scope(agent.id, canonical_host_path(raw_path), task_id=task_id)
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
    path = canonical_host_path(raw_path)
    grant_root = grantable_host_root(raw_path)
    if grant_root is None:
        grant_root = Path(path if Path(path).is_dir() else str(Path(path).parent))

    prior = db.find_workspace_preference_for_scope(agent.id, path, task_id=task_id)
    if prior is not None and prior.status != "edit_host":
        return _result_for_resolved_preference(prior, command=command, cwd=cwd)

    origin_channel = _clean_channel_id(channel_id)
    if origin_channel and db.is_channel_archived(origin_channel):
        from core.models.channel import THREAD_ARCHIVED_CONSENT_DENY

        return error_result(command, THREAD_ARCHIVED_CONSENT_DENY, cwd=cwd, executor="virtual")

    pending = db.find_pending_workspace_preference(agent.id, path)
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
        )
    return error_result(
        command,
        (
            f"Host writes stay blocked for {prior.path!r}. "
            "The operator cancelled workspace preference for this path."
        ),
        cwd=cwd,
        executor="virtual",
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
