"""In-thread Shell Executor consent for validate-on-clone.

When Branch/workspace-copy is locked and ``cli_shell_enabled`` is still the
secure default (false), a shell-needing CLI attempt pauses on an Enable/Deny
card instead of a raw "shell execution is not enabled" error. Enable turns
the setting on and resumes the pending command. Deny refuses cleanly.
"""

from __future__ import annotations

from typing import Any, Literal

import db
from core import config
from core.agent_loop.runtime_core import locked_workspace_copies_for_turn
from core.bm_cli.host_path_consent import _clean_channel_id, _enqueue_resume
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.results import consent_required_result, error_result
from core.bm_cli.types import BossModCliResult, ParsedCliCommand
from core.bm_cli.workspace_preference import cwd_is_nested_clone_repo
from core.models import Agent
from core.models.host_path_consent import (
    SHELL_EXECUTOR_BODY,
    SHELL_EXECUTOR_DENIED_NOTE,
    SHELL_EXECUTOR_ENABLED_NOTE,
    SHELL_EXECUTOR_GRANT_ROOT,
    SHELL_EXECUTOR_KIND,
    HostPathConsentRequest,
)

ShellExecutorDecision = Literal["enable", "deny"]

_VIRTUAL_GIT_SUBCOMMANDS = frozenset({"status", "log", "diff", "show", "restore"})
_SETTING_KEY = "cli_shell_enabled"
_SETTING_CATEGORY = "cli_policy"


def shell_executor_is_enabled() -> bool:
    """Return True when ``cli_shell_enabled`` is on in the database.

    Reads through :func:`config.get_live` so a company-wide Enable in the
    API process is visible to the runtime worker without a cache reload.
    """
    return config.get_live(_SETTING_KEY) == "true"


def command_needs_shell_executor(agent: Agent, parsed: ParsedCliCommand, cwd: str) -> bool:
    """Return True when this CLI would use the shell executor if it were on."""
    from core.bm_cli.runtime import VIRTUAL_COMMANDS

    if parsed.name not in VIRTUAL_COMMANDS:
        return True
    if parsed.name != "git":
        return False
    subcommand = parsed.args[0] if parsed.args else ""
    if subcommand not in _VIRTUAL_GIT_SUBCOMMANDS:
        return True
    return cwd_is_nested_clone_repo(agent, cwd)


def maybe_pause_for_shell_executor(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd: str,
    task_id: str | None,
    channel_id: str | None,
    trigger_type: str | None = None,
) -> BossModCliResult | None:
    """Pause for Enable/Deny when a locked clone needs the shell executor."""
    if shell_executor_is_enabled():
        return None
    # A just-granted resume must re-run, not open a second pending card.
    if trigger_type == "host_path_consent_resolved":
        return None
    if not command_needs_shell_executor(agent, parsed, cwd):
        return None
    if not locked_workspace_copies_for_turn(agent.id, task_id):
        return None

    peek = policy_engine.evaluate(
        parsed.raw,
        frozenset(),
        agent_id=agent.id,
        assume_shell=True,
        cwd=cwd,
    )
    if peek.tier == "never_allowed":
        from core.bm_cli.locked_clone_outcome import never_allowed_cli_result

        return never_allowed_cli_result(
            agent,
            parsed,
            cwd,
            peek,
            channel_id=channel_id,
        )
    return request_shell_executor_consent(
        agent=agent,
        parsed=parsed,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=channel_id,
    )


def request_shell_executor_consent(
    *,
    agent: Agent,
    parsed: ParsedCliCommand,
    content: str | None,
    cwd: str | None,
    task_id: str | None,
    channel_id: str | None,
) -> BossModCliResult:
    """Open or reuse the Shell Executor Enable/Deny card."""
    denied = _denied_for_scope(agent.id, task_id=task_id)
    if denied is not None:
        return _denied_result(parsed.raw, cwd=cwd)

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
        path=dest or SHELL_EXECUTOR_GRANT_ROOT,
        grant_root=SHELL_EXECUTOR_GRANT_ROOT,
        reason=SHELL_EXECUTOR_BODY,
        command=parsed.raw,
        content=content,
        cwd=cwd,
        task_id=task_id,
        channel_id=origin_channel,
        card_kind=SHELL_EXECUTOR_KIND,
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


async def resume_shell_executor_consent(
    request_id: str,
    *,
    decision: ShellExecutorDecision,
    services: Any,
    decision_by: str = "human",
    note: str | None = None,
    enqueue_resume: bool = True,
    omit_origin_channel: bool = False,
) -> HostPathConsentRequest | None:
    """Apply Enable / Deny and wake the waiting agent."""
    existing = db.get_consent_request(request_id)
    if existing is None or existing.status != "pending":
        return None
    if (existing.card_kind or "") != SHELL_EXECUTOR_KIND:
        return None

    if decision == "deny":
        updated = db.resolve_consent_request(
            request_id,
            status="denied",
            decision_by=decision_by,
            decision_note=note or SHELL_EXECUTOR_DENIED_NOTE,
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

    if decision != "enable":
        raise ValueError(f"Unsupported shell executor decision: {decision}")

    db.set_setting(_SETTING_KEY, "true", _SETTING_CATEGORY)
    config.reload()
    updated = db.resolve_consent_request(
        request_id,
        status="enabled",
        decision_by=decision_by,
        decision_note=note or SHELL_EXECUTOR_ENABLED_NOTE,
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
            decision_note=note or SHELL_EXECUTOR_ENABLED_NOTE,
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


def _pending_for_agent(agent_id: str) -> HostPathConsentRequest | None:
    for row in db.list_consent_requests(agent_id=agent_id, status="pending", limit=80):
        if (row.card_kind or "") == SHELL_EXECUTOR_KIND:
            return row
    return None


def _pending_all() -> list[HostPathConsentRequest]:
    return [
        row
        for row in db.list_consent_requests(status="pending", limit=200)
        if (row.card_kind or "") == SHELL_EXECUTOR_KIND
    ]


def _denied_for_scope(agent_id: str, *, task_id: str | None) -> HostPathConsentRequest | None:
    for row in db.list_consent_requests(agent_id=agent_id, status="denied", limit=80):
        if (row.card_kind or "") != SHELL_EXECUTOR_KIND:
            continue
        if task_id:
            if row.task_id == task_id:
                return row
        elif not row.task_id:
            return row
    return None


def _clone_dest_for_turn(agent_id: str, task_id: str | None) -> str:
    rows = locked_workspace_copies_for_turn(agent_id, task_id)
    if not rows:
        return ""
    return (rows[0].clone_dest or "").strip()


def _consent_message(request: HostPathConsentRequest, *, agent_name: str) -> str:
    name = (agent_name or "").strip() or "Agent"
    dest = (request.path or "").strip()
    suffix = f" Locked copy: {dest}." if dest and dest != SHELL_EXECUTOR_GRANT_ROOT else ""
    return f"{name} needs Shell Executor for validate-on-clone — enable or deny.{suffix}"


def _denied_result(command: str, *, cwd: str | None) -> BossModCliResult:
    return error_result(
        command,
        "Shell Executor is off — the operator denied enable for validate-on-clone.",
        cwd=cwd,
        executor="shell",
        kind="shell_executor_deny",
    )


def named_shell_executor_block_reason(
    agent: Agent,
    reason: str | None,
    *,
    task_id: str | None,
) -> str | None:
    """Return the named Shell Executor why when that gate is why work stopped."""
    from core.agent_loop.blocked_origin import SHELL_EXECUTOR_WHY

    pending = _pending_for_agent(agent.id)
    denied = _denied_for_scope(agent.id, task_id=task_id)
    blob = (reason or "").lower()
    myth = (
        "desk" in blob
        or "shell" in blob
        or "pytest" in blob
        or "operator" in blob
        or "can't" in blob
        or "cannot" in blob
    )
    if denied is not None:
        return SHELL_EXECUTOR_WHY
    if not myth:
        return None
    if pending is not None:
        return SHELL_EXECUTOR_WHY
    if not locked_workspace_copies_for_turn(agent.id, task_id):
        return None
    if shell_executor_is_enabled():
        return None
    return SHELL_EXECUTOR_WHY
