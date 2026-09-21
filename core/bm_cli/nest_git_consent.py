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
    GitAuthFailureKind,
    auth_failed_blocked_message,
    auth_failed_operator_copy,
    command_needs_gh_auth,
    command_needs_nest_git_auth,
    enable_host_git,
    gh_auth_blocked_message,
    nest_git_auth_ready,
    no_creds_blocked_message,
    no_match_blocked_message,
    write_nest_git_secret,
)
from core.bm_cli.nest_git_store import (
    add_credential,
    append_match,
    load_credentials,
    resolve_git_remote,
    select_nest_git_credential,
    suggested_label_for_remote,
    suggested_match_for_remote,
    update_credential,
)
from core.bm_cli.results import consent_required_result, error_result
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.models import Agent
from core.models.host_path_consent import HostPathConsentRequest
from core.models.nest_git import (
    GH_CLI_NO_AUTH_WHY,
    NEST_GIT_BODY,
    NEST_GIT_CARD_COPY,
    NEST_GIT_EMPTY_CREDS,
    NEST_GIT_ENABLED_NOTE,
    NEST_GIT_GRANT_ROOT,
    NEST_GIT_KIND,
    NEST_GIT_PAT_KEY,
    NEST_GIT_SSH_KEY,
)

NestGitDecision = Literal["enable", "credentials", "use"]


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
    if nest_git_auth_ready(agent=agent, parsed=parsed, cwd=cwd):
        return None
    if trigger_type == "host_path_consent_resolved":
        if nest_git_auth_ready(agent=agent, parsed=parsed, cwd=cwd):
            return None
        return _blocked_result(parsed.raw, cwd=cwd, unmatched=_unmatched(agent, parsed, cwd))
    reason = no_match_blocked_message() if _unmatched(agent, parsed, cwd) else None
    return request_nest_git_consent(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=channel_id,
        reason=reason,
    )


def maybe_block_gh_cli(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd: str,
    task_id: str | None,
    channel_id: str | None,
    trigger_type: str | None = None,
    persist_chrome: bool = True,
) -> BossModCliResult | None:
    """Fail-closed one Nest git / compare-URL card for a gh auth miss. No Approve.

    Nest git → gh inject is parked. When Nest git is not ready, reuse the Nest
    git card. When Nest git already covers push, post one Blocked note with the
    compare URL (or host ``gh auth login``) instead of Approve spam.
    """
    if not command_needs_gh_auth(parsed):
        return None
    ready = nest_git_auth_ready(agent=agent, parsed=parsed, cwd=cwd)
    if not ready and trigger_type != "host_path_consent_resolved" and persist_chrome:
        return request_nest_git_consent(
            agent=agent,
            parsed=parsed,
            content=content,
            cwd=cwd,
            task_id=task_id,
            channel_id=channel_id,
            reason=gh_auth_blocked_message(agent, parsed, cwd, nest_ready=False),
        )
    return _gh_blocked_result(
        agent=agent,
        parsed=parsed,
        cwd=cwd,
        channel_id=channel_id,
        nest_ready=ready,
        persist_chrome=persist_chrome,
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
    label: str | None = None,
    match: str | None = None,
    credential_id: str | None = None,
    is_default: bool | None = None,
) -> HostPathConsentRequest | None:
    """Apply Enable, Add PAT/SSH, or pick a saved credential."""
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
        _save_card_credentials(
            existing,
            pat=pat,
            ssh_key=ssh_key,
            label=label,
            match=match,
            credential_id=credential_id,
            is_default=is_default,
        )
    elif decision == "use":
        _use_saved_credential(existing, credential_id)
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
    auth_kind: GitAuthFailureKind = "ambiguous",
) -> BossModCliResult:
    """Fail-closed Nest git card + short origin note. Do not wipe secrets."""
    from core.agent_loop.blocked_origin import NEST_GIT_BLOCK_KIND, surface_blocked_origin

    message = auth_failed_blocked_message(auth_kind)
    why, _howto = auth_failed_operator_copy(auth_kind)
    paused = request_nest_git_consent(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=channel_id,
        reason=message,
    )
    blocked = error_result(
        parsed.raw,
        message,
        cwd=cwd,
        executor="shell",
        kind="nest_git_block",
    )
    origin: dict[str, Any] = {}
    surface_blocked_origin(
        origin,
        agent=agent,
        trigger={"channel_id": channel_id} if channel_id else None,
        why=why,
        kind=NEST_GIT_BLOCK_KIND,
    )
    data = dict(blocked.data or {})
    extras = [
        _json_safe_chrome(item)
        for item in origin.get("origin_status_messages") or []
        if isinstance(item, dict)
    ]
    if extras:
        data["origin_status_messages"] = extras
    chrome = origin.get("channel_message") or origin.get("chat_message")
    if isinstance(chrome, dict):
        data["origin_chrome"] = _json_safe_chrome(chrome)
    data["nest_git_origin_posted"] = True
    data["nest_git_auth_kind"] = auth_kind
    if not paused.consent_required:
        return replace(blocked, data=data)
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
    from core.models.nest_git import NEST_GIT_NO_CREDS_WHY, NEST_GIT_NO_MATCH_WHY

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
    pending_note = (pending.reason or "") if pending is not None else ""
    if NEST_GIT_NO_MATCH_WHY in pending_note:
        return NEST_GIT_NO_MATCH_WHY
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


def _gh_blocked_result(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    cwd: str | None,
    channel_id: str | None,
    nest_ready: bool,
    persist_chrome: bool,
) -> BossModCliResult:
    """One Blocked origin note. No Approve card. Do not wipe Nest git secrets."""
    from core.agent_loop.notifications import persist_origin_system_note

    message = gh_auth_blocked_message(agent, parsed, cwd, nest_ready=nest_ready)
    blocked = error_result(
        parsed.raw,
        message,
        cwd=cwd,
        executor="shell",
        kind="nest_git_block",
    )
    data = dict(blocked.data or {})
    data["nest_git_auth_kind"] = "gh_cli"
    if not persist_chrome:
        data["nest_git_origin_posted"] = True
        return replace(blocked, data=data)
    origin_channel = _clean_channel_id(channel_id)
    note = persist_origin_system_note(
        agent,
        gh_auth_operator_note(agent.name, parsed.raw, message),
        channel_id=origin_channel,
        kind="blocked",
        source_channel="channel" if origin_channel else "chat",
        policy="all",
    )
    chrome = note.get("channel_message") or note.get("chat_message")
    if isinstance(chrome, dict) and chrome:
        data["origin_chrome"] = _json_safe_chrome(chrome)
        data["origin_status_messages"] = [_json_safe_chrome(chrome)]
    data["nest_git_origin_posted"] = True
    return replace(blocked, data=data)


def gh_auth_operator_note(agent_name: str, command: str, message: str) -> str:
    """Return the in-thread system note for a gh auth miss. Not an Approve card."""
    name = (agent_name or "").strip() or "Agent"
    cmd = (command or "").strip() or "gh"
    why = (message or GH_CLI_NO_AUTH_WHY).strip()
    if why.lower().startswith("blocked"):
        body = why
    else:
        body = f"Blocked — {why}"
    return f"{name} tried `{cmd}` — {body}"
    """Drop non-JSON persist fields so CLI data can be logged without leaking."""
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"feed_entry", "host_path_consent", "cli_approval"}:
            continue
        if hasattr(value, "isoformat"):
            safe[key] = value.isoformat()
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    return safe


def _blocked_result(
    command: str,
    *,
    cwd: str | None,
    unmatched: bool = False,
) -> BossModCliResult:
    message = no_match_blocked_message() if unmatched else no_creds_blocked_message()
    return error_result(
        command,
        message,
        cwd=cwd,
        executor="shell",
        kind="nest_git_block",
    )


def _unmatched(agent: Agent, parsed: ParsedCliCommand, cwd: str) -> bool:
    if not load_credentials():
        return False
    remote = resolve_git_remote(agent, parsed, cwd)
    return select_nest_git_credential(remote or None) is None


def _request_remote(request: HostPathConsentRequest) -> str:
    from core.bm_cli.parser import parse_cli_command

    agent = db.get_agent(request.agent_id)
    try:
        parsed = parse_cli_command(request.command or "")
    except ValueError:
        parsed = None
    return resolve_git_remote(agent, parsed, request.cwd)


def _save_card_credentials(
    request: HostPathConsentRequest,
    *,
    pat: str | None,
    ssh_key: str | None,
    label: str | None,
    match: str | None,
    credential_id: str | None,
    is_default: bool | None,
) -> None:
    token = (pat or "").strip()
    key = (ssh_key or "").strip()
    if not token and not key:
        raise ValueError(NEST_GIT_EMPTY_CREDS)
    remote = _request_remote(request)
    store = load_credentials()
    target = (credential_id or "").strip()
    if not target:
        chosen = select_nest_git_credential(remote or None)
        if chosen is not None:
            target = chosen.id
    if target:
        update_credential(
            target,
            label=label,
            match=match,
            pat=token or None,
            ssh_key=key or None,
            is_default=is_default,
        )
        return
    if not store:
        if token:
            write_nest_git_secret(NEST_GIT_PAT_KEY, token)
        if key:
            write_nest_git_secret(NEST_GIT_SSH_KEY, key)
        return
    add_credential(
        label=(label or "").strip() or suggested_label_for_remote(remote) or "GitHub",
        match=(match or "").strip() or suggested_match_for_remote(remote),
        pat=token or None,
        ssh_key=key or None,
        is_default=is_default,
    )


def _use_saved_credential(request: HostPathConsentRequest, credential_id: str | None) -> None:
    target = (credential_id or "").strip()
    if not target:
        raise ValueError("Pick which saved credential to use.")
    store = {item.id: item for item in load_credentials()}
    if target not in store:
        raise ValueError("That Nest git credential is gone. Add one or pick another.")
    remote = _request_remote(request)
    if remote:
        append_match(target, remote)
    elif not store[target].is_default:
        update_credential(target, is_default=True)
