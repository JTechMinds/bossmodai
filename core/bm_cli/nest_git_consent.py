"""In-thread nest git auth card — Enable host git or Add PAT/SSH.

Both actions write the one Settings store. Always-allow does not skip this
gate. Probe fail does not persist host Enable On.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal

import db
from core.agent_loop.runtime_core import locked_workspace_copies_for_turn
from core.bm_cli.host_path_consent import _clean_channel_id, _enqueue_resume
from core.bm_cli.nest_git import (
    auth_failed_blocked_message,
    command_needs_nest_git_auth,
    enable_host_git,
    nest_git_auth_ready,
    no_creds_blocked_message,
    write_nest_git_secret,
)
from core.bm_cli.results import consent_required_result, error_result
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.models import Agent
from core.models.host_path_consent import HostPathConsentRequest
from core.models.nest_git import (
    NEST_GIT_BODY,
    NEST_GIT_CARD_COPY,
    NEST_GIT_EMPTY_CREDS,
    NEST_GIT_ENABLED_NOTE,
    NEST_GIT_GRANT_ROOT,
    NEST_GIT_KIND,
    NEST_GIT_PAT_KEY,
    NEST_GIT_SSH_KEY,
)

NestGitDecision = Literal["enable", "credentials"]


def maybe_pause_for_nest_git(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd: str,
    task_id: str | None,
    channel_id: str | None,
    trigger_type: str | None = None,
) -> BossModCliResult | None:
    """Pause for the nest git card when a nest remote-git op has no creds."""
    if not command_needs_nest_git_auth(agent, parsed, cwd):
        return None
    if nest_git_auth_ready():
        return None
    if trigger_type == "host_path_consent_resolved":
        if nest_git_auth_ready():
            return None
        return _blocked_result(parsed.raw, cwd=cwd)
    return request_nest_git_consent(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=channel_id,
    )


def request_nest_git_consent(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd: str | None,
    task_id: str | None,
    channel_id: str | None,
    reason: str | None = None,
) -> BossModCliResult:
    """Open or reuse the nest git Enable / Add PAT/SSH card."""
    from core.bm_cli.host_path_consent import require_consent_chrome

    origin_channel = _clean_channel_id(channel_id)
    if origin_channel and db.is_channel_archived(origin_channel):
        from core.models.channel import THREAD_ARCHIVED_CONSENT_DENY

        return error_result(parsed.raw, THREAD_ARCHIVED_CONSENT_DENY, cwd=cwd, executor="shell")

    pending = _pending_for_agent(agent.id)
    if pending is not None:
        if origin_channel and not pending.channel_id:
            pending = db.bind_consent_channel(pending.id, origin_channel) or pending
        chrome_error = require_consent_chrome(
            agent,
            pending,
            channel_id=origin_channel or pending.channel_id,
            command=parsed.raw,
            cwd=cwd,
            executor="shell",
            abandon=False,
        )
        if chrome_error is not None:
            return chrome_error
        return consent_required_result(
            parsed.raw,
            _consent_message(pending, agent_name=agent.name),
            cwd=cwd,
            consent_request=pending,
            reused=True,
        )

    dest = _clone_dest_for_turn(agent.id, task_id)
    request = db.create_consent_request(
        agent_id=agent.id,
        path=dest or NEST_GIT_GRANT_ROOT,
        grant_root=NEST_GIT_GRANT_ROOT,
        reason=reason or NEST_GIT_BODY,
        command=parsed.raw,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=origin_channel,
        card_kind=NEST_GIT_KIND,
    )
    chrome_error = require_consent_chrome(
        agent,
        request,
        channel_id=origin_channel,
        command=parsed.raw,
        cwd=cwd,
        executor="shell",
        abandon=True,
    )
    if chrome_error is not None:
        return chrome_error
    return consent_required_result(
        parsed.raw,
        _consent_message(request, agent_name=agent.name),
        cwd=cwd,
        consent_request=request,
        reused=False,
    )


async def resume_nest_git_consent(
    request_id: str,
    *,
    decision: NestGitDecision,
    services: Any,
    decision_by: str = "human",
    note: str | None = None,
    enqueue_resume: bool = True,
    omit_origin_channel: bool = False,
    pat: str | None = None,
    ssh_key: str | None = None,
) -> HostPathConsentRequest | None:
    """Apply Enable or Add PAT/SSH and wake the waiting agent."""
    existing = db.get_consent_request(request_id)
    if existing is None or existing.status != "pending":
        return None
    if (existing.card_kind or "") != NEST_GIT_KIND:
        return None

    if decision == "enable":
        probe = enable_host_git()
        if not probe.ok:
            raise NestGitProbeError(probe.blocked_message())
    elif decision == "credentials":
        token = (pat or "").strip()
        key = (ssh_key or "").strip()
        if not token and not key:
            raise ValueError(NEST_GIT_EMPTY_CREDS)
        if token:
            write_nest_git_secret(NEST_GIT_PAT_KEY, token)
        if key:
            write_nest_git_secret(NEST_GIT_SSH_KEY, key)
    else:
        raise ValueError(f"Unsupported nest git decision: {decision}")

    updated = db.resolve_consent_request(
        request_id,
        status="enabled",
        decision_by=decision_by,
        decision_note=note or NEST_GIT_ENABLED_NOTE,
    )
    if updated is None:
        return None
    await _enqueue_resume(
        updated,
        status="enabled",
        services=services,
        enqueue_resume=enqueue_resume,
        omit_origin_channel=omit_origin_channel,
    )
    for sibling in _pending_all():
        if sibling.id == updated.id:
            continue
        other = db.resolve_consent_request(
            sibling.id,
            status="enabled",
            decision_by=decision_by,
            decision_note=note or NEST_GIT_ENABLED_NOTE,
        )
        if other is None:
            continue
        await _enqueue_resume(
            other,
            status="enabled",
            services=services,
            follow_through=False,
            enqueue_resume=enqueue_resume,
            omit_origin_channel=omit_origin_channel,
        )
    return updated


def bounce_nest_git_after_auth_failure(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd: str,
    task_id: str | None,
    channel_id: str | None,
) -> BossModCliResult:
    """Fail-closed Blocked + reopen the Nest git card. Do not wipe secrets."""
    paused = request_nest_git_consent(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=channel_id,
        reason=auth_failed_blocked_message(),
    )
    blocked = error_result(
        parsed.raw,
        auth_failed_blocked_message(),
        cwd=cwd,
        executor="shell",
        kind="nest_git_block",
    )
    if not paused.consent_required:
        return blocked
    data = dict(blocked.data or {})
    card = (paused.data or {}).get("host_path_consent") or {}
    if card:
        data["host_path_consent"] = card
    return replace(
        blocked,
        data=data,
        consent_request_id=paused.consent_request_id,
    )


def named_nest_git_block_reason(
    agent: Agent,
    reason: str | None,
    *,
    task_id: str | None,
) -> str | None:
    """Return the named nest git why when that gate is why work stopped."""
    from core.models.nest_git import NEST_GIT_NO_CREDS_WHY

    pending = _pending_for_agent(agent.id)
    blob = (reason or "").lower()
    myth = (
        "desk" in blob
        or "github login" in blob
        or "operator" in blob
        or "can't" in blob
        or "cannot" in blob
        or "git push" in blob
    )
    if not myth and pending is None:
        return None
    if nest_git_auth_ready():
        return None
    if pending is not None or myth:
        return NEST_GIT_NO_CREDS_WHY
    del task_id
    return None


class NestGitProbeError(ValueError):
    """Host Enable probe failed; Settings On was not saved."""


def _pending_for_agent(agent_id: str) -> HostPathConsentRequest | None:
    for row in db.list_consent_requests(agent_id=agent_id, status="pending", limit=80):
        if (row.card_kind or "") == NEST_GIT_KIND:
            return row
    return None


def _pending_all() -> list[HostPathConsentRequest]:
    return [
        row
        for row in db.list_consent_requests(status="pending", limit=200)
        if (row.card_kind or "") == NEST_GIT_KIND
    ]


def _clone_dest_for_turn(agent_id: str, task_id: str | None) -> str:
    rows = locked_workspace_copies_for_turn(agent_id, task_id)
    if not rows:
        return ""
    return (rows[0].clone_dest or "").strip()


def _consent_message(request: HostPathConsentRequest, *, agent_name: str) -> str:
    name = (agent_name or "").strip() or "Agent"
    dest = (request.path or "").strip()
    suffix = f" Locked copy: {dest}." if dest and dest != NEST_GIT_GRANT_ROOT else ""
    return f"{name} {NEST_GIT_CARD_COPY}.{suffix}"


def _blocked_result(command: str, *, cwd: str | None) -> BossModCliResult:
    return error_result(
        command,
        no_creds_blocked_message(),
        cwd=cwd,
        executor="shell",
        kind="nest_git_block",
    )
