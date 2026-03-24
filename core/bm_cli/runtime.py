"""BossMod AI — Execution/runtime support for BossMod CLI calls."""

from __future__ import annotations

from typing import Callable

from core.bm_cli.artifacts import register_cli_artifacts
from core.bm_cli.audit import record_bm_cli_event
from core.bm_cli.fs_commands import (
    handle_append,
    handle_cat,
    handle_cd,
    handle_ls,
    handle_mkdir,
    handle_pwd,
    handle_write,
)
from core.bm_cli.git_commands import handle_git
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policies import evaluate_parsed_command_policy
from core.bm_cli.results import approval_required_result, error_result
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.state_commands import (
    handle_activity,
    handle_current_task,
    handle_location,
    handle_recent_work,
    handle_runtime,
    handle_status,
    handle_tasks,
)
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.models import Agent, AgentState

CliHandler = Callable[[CliExecutionContext, ParsedCliCommand, str | None], BossModCliResult]

_HANDLERS: dict[str, CliHandler] = {
    "pwd": handle_pwd,
    "cd": handle_cd,
    "ls": handle_ls,
    "cat": handle_cat,
    "mkdir": handle_mkdir,
    "write": handle_write,
    "append": handle_append,
    "git": handle_git,
    "status": handle_status,
    "runtime": handle_runtime,
    "activity": handle_activity,
    "current-task": handle_current_task,
    "tasks": handle_tasks,
    "recent-work": handle_recent_work,
    "location": handle_location,
}


def execute_bm_cli(
    agent: Agent,
    state: AgentState,
    command: str,
    content: str | None = None,
    *,
    trigger_type: str | None = None,
) -> BossModCliResult:
    """Execute a bounded shell-like BossMod CLI command for the given agent."""
    cwd_before = get_cli_cwd(agent.id)
    try:
        parsed = parse_cli_command(command)
    except ValueError as exc:
        result = error_result(command, str(exc), cwd=cwd_before, executor="virtual")
        record_bm_cli_event(
            agent_id=agent.id,
            command=command,
            content=content,
            executor=result.executor,
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier="parse",
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return result

    policy = evaluate_parsed_command_policy(parsed)
    if policy.approval_required:
        result = approval_required_result(
            parsed.raw,
            policy.message or "Approval required.",
            cwd=cwd_before,
            executor=policy.executor,
        )
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor=policy.executor,
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier=policy.tier,
            decision="approval_required",
            result=result,
            trigger_type=trigger_type,
        )
        return result
    if not policy.allowed:
        result = error_result(
            parsed.raw,
            policy.message or f"Unsupported command: {parsed.name}",
            cwd=cwd_before,
            executor=policy.executor,
        )
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor=policy.executor,
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier=policy.tier,
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return result

    handler = _HANDLERS.get(parsed.name)
    if handler is None:
        result = error_result(
            parsed.raw,
            f"Unsupported command: {parsed.name}",
            cwd=cwd_before,
            executor=policy.executor,
        )
        record_bm_cli_event(
            agent_id=agent.id,
            command=parsed.raw,
            content=content,
            executor=policy.executor,
            cwd_before=cwd_before,
            cwd_after=result.cwd,
            policy_tier=policy.tier,
            decision="denied",
            result=result,
            trigger_type=trigger_type,
        )
        return result

    try:
        result = handler(CliExecutionContext(agent=agent, state=state, cwd=cwd_before), parsed, content)
    except ValueError as exc:
        result = error_result(parsed.raw, str(exc), cwd=cwd_before, executor=policy.executor)

    artifact_ids = register_cli_artifacts(agent, result)
    if artifact_ids:
        data = dict(result.data or {})
        data["artifact_ids"] = artifact_ids
        result = BossModCliResult(
            command=result.command,
            ok=result.ok,
            detail=result.detail,
            prompt_content=result.prompt_content,
            kind=result.kind,
            data=data,
            cwd=result.cwd,
            approval_required=result.approval_required,
            executor=result.executor,
            exit_code=result.exit_code,
        )

    record_bm_cli_event(
        agent_id=agent.id,
        command=parsed.raw,
        content=content,
        executor=policy.executor,
        cwd_before=cwd_before,
        cwd_after=result.cwd,
        policy_tier=policy.tier,
        decision="allowed",
        result=result,
        trigger_type=trigger_type,
    )
    return result
