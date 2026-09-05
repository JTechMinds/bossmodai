"""BossMod AI — Shared managed-writer progress, annotation, and text helpers."""

from __future__ import annotations

from typing import Any

from core import config
from core.bm_cli.artifacts import register_cli_artifacts
from core.bm_cli.audit import record_bm_cli_event
from core.bm_cli.managed_writer.types import (
    ManagedWriteProgress,
    ManagedWriteProgressCallback,
    _MANAGED_WRITE_DONE_SENTINEL,
    _MANAGED_WRITE_PLAN_SENTINEL,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policies import evaluate_parsed_command_policy
from core.bm_cli.results import error_result, render_sections
from core.bm_cli.runtime import VIRTUAL_COMMANDS
from core.bm_cli.types import BossModCliResult
from core.default_prompts import load_default_prompt
from core.models import Agent

def _managed_max_batch_files() -> int:
    """Return the configured upper bound for files in one batch-write request."""
    return config.require_int("managed_writer_max_batch_files")


def _managed_max_sections_per_file() -> int:
    """Return the configured upper bound for planned sections in one file."""
    return config.require_int("managed_writer_max_sections_per_file")


def _managed_write_byte_limit() -> int:
    """Return the configured byte limit for one managed file body."""
    return config.require_int("cli_max_write_bytes")


async def _emit_progress(
    progress_callback: ManagedWriteProgressCallback | None,
    update: ManagedWriteProgress,
) -> None:
    """Forward one structured progress update when a runtime reporter is present."""
    if progress_callback is None:
        return
    await progress_callback(update)


def _clone_cli_result(
    cli_result: BossModCliResult,
    *,
    data: dict[str, Any] | None = None,
    prompt_content: str | None = None,
) -> BossModCliResult:
    """Return a shallow copy of a CLI result with updated fields."""
    return BossModCliResult(
        command=cli_result.command,
        ok=cli_result.ok,
        detail=cli_result.detail,
        prompt_content=prompt_content if prompt_content is not None else cli_result.prompt_content,
        kind=cli_result.kind,
        data=data if data is not None else cli_result.data,
        cwd=cli_result.cwd,
        approval_required=cli_result.approval_required,
        executor=cli_result.executor,
        exit_code=cli_result.exit_code,
        matched_rule_id=cli_result.matched_rule_id,
        approval_request_id=cli_result.approval_request_id,
    )


def _annotate_managed_writer_result(
    cli_result: BossModCliResult,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    chunks: int,
    byte_count: int,
    completed: bool,
    strategy: str,
    section_count: int,
) -> BossModCliResult:
    """Attach managed-writer metadata to a CLI result for diagnostics and UI."""
    data = dict(cli_result.data or {})
    data["managed_writer_attempted"] = True
    data["managed_writer_used"] = completed
    data["managed_writer_completed"] = completed
    data["managed_strategy"] = strategy
    data["managed_calls"] = chunks
    data["managed_chunks"] = chunks
    data["managed_sections"] = section_count
    data["managed_bytes"] = byte_count
    data["managed_prompt_tokens"] = prompt_tokens
    data["managed_completion_tokens"] = completion_tokens
    data["managed_total_tokens"] = total_tokens
    return _clone_cli_result(cli_result, data=data)


def _annotate_batch_writer_result(
    cli_result: BossModCliResult,
    *,
    completed: bool,
    file_count: int,
    files: list[dict[str, Any]] | None = None,
) -> BossModCliResult:
    """Attach batch-writer metadata to a CLI result for diagnostics and UI."""
    data = dict(cli_result.data or {})
    data["batch_writer_attempted"] = True
    data["batch_writer_used"] = completed
    data["batch_writer_completed"] = completed
    data["batch_file_count"] = file_count
    if files:
        data["batch_files"] = files
        data["paths"] = [str(item["path"]) for item in files if isinstance(item.get("path"), str)]
    return _clone_cli_result(cli_result, data=data)


def _managed_writer_error_result(
    command: str,
    message: str,
    *,
    cwd: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    chunks: int,
    byte_count: int,
    strategy: str,
    section_count: int,
) -> BossModCliResult:
    """Build a managed-writer error with explicit recovery guidance and metadata."""
    cli_result = error_result(command, message, cwd=cwd)
    guidance_lines = load_default_prompt("internal_managed_writer_error_guidance").splitlines()
    prompt_content = render_sections(
        command,
        [
            (
                "ERROR",
                [
                    message,
                    *guidance_lines,
                ],
            )
        ],
    )
    return _annotate_managed_writer_result(
        _clone_cli_result(cli_result, prompt_content=prompt_content),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=chunks,
        byte_count=byte_count,
        completed=False,
        strategy=strategy,
        section_count=section_count,
    )


def _finalize_managed_cli_result(
    *,
    agent: Agent,
    command: str,
    content: str | None,
    cwd_before: str,
    cli_result: BossModCliResult,
    trigger_type: str | None,
) -> BossModCliResult:
    """Attach artifacts and audit rows for runtime-managed CLI results."""
    parsed = parse_cli_command(command)
    policy = evaluate_parsed_command_policy(parsed, VIRTUAL_COMMANDS, agent_id=agent.id)

    artifact_ids = register_cli_artifacts(agent, cli_result)
    if artifact_ids:
        data = dict(cli_result.data or {})
        data["artifact_ids"] = artifact_ids
        cli_result = _clone_cli_result(cli_result, data=data)

    record_bm_cli_event(
        agent_id=agent.id,
        command=command,
        content=content,
        executor=policy.executor,
        cwd_before=cwd_before,
        cwd_after=cli_result.cwd,
        policy_tier=policy.tier,
        decision="allowed" if cli_result.ok else "denied",
        result=cli_result,
        trigger_type=trigger_type,
    )
    return cli_result


def _normalize_generated_text(text: str) -> str:
    """Normalize one model response for managed authoring."""
    return _strip_code_fences(text).replace("\r\n", "\n")


def _remove_control_tokens(text: str) -> str:
    """Remove managed-authoring control sentinels from a response."""
    cleaned = text.replace(_MANAGED_WRITE_DONE_SENTINEL, "")
    cleaned = cleaned.replace(_MANAGED_WRITE_PLAN_SENTINEL, "")
    return cleaned.strip()


def _strip_duplicate_heading(text: str, heading: str) -> str:
    """Drop one leading heading when the model repeats it despite instructions."""
    normalized = text.strip()
    heading_line = heading.strip()
    if normalized == heading_line:
        return ""
    if normalized.startswith(f"{heading_line}\n"):
        return normalized[len(heading_line):].lstrip("\n")
    return normalized


def _strip_code_fences(text: str) -> str:
    """Remove one outer fenced block when the model adds markdown fences."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.split("\n")
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1])
    return text

