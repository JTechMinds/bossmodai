"""BossMod AI — Managed file authoring behind BossMod CLI."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from core import config
from core.bm_cli.artifacts import register_cli_artifacts
from core.bm_cli.audit import record_bm_cli_event
from core.bm_cli.document_tools import (
    find_markdown_section,
    get_markdown_section_body,
    parse_markdown_document,
    replace_markdown_section_body,
    render_markdown_outline_entries,
)
from core.bm_cli.fs_commands import write_virtual_text
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.policies import evaluate_parsed_command_policy
from core.bm_cli.results import error_result, render_sections, success_result
from core.bm_cli.runtime import VIRTUAL_COMMANDS, execute_bm_cli
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.types import BossModCliResult
from core.bm_cli.virtual_fs import resolve_cli_path
from core.bm_cli.workspace_git import commit_workspace_changes
from core.default_prompts import load_default_prompt, render_default_prompt
from core.llm import client
from core.models import Agent, AgentState

_MANAGED_WRITE_DONE_SENTINEL = "<<BOSSMOD_FILE_DONE>>"
_MANAGED_WRITE_PLAN_SENTINEL = "<<BOSSMOD_PLAN_REQUIRED>>"

_MANAGED_WRITER_PROMPT_ALLOWED_PATHS = {
    "target_path",
    "file_goal",
    "done_sentinel",
    "plan_sentinel",
    "max_sections",
    "batch.is_batch",
    "batch.file_index",
    "batch.file_count",
    "section.heading",
    "section.goal",
    "section_index",
    "section_count",
    "outline",
    "section_heading",
    "rewrite_goal",
    "previous_heading",
    "next_heading",
    "current_body",
}


@dataclass(frozen=True, slots=True)
class ManagedWriteOutcome:
    """Result of one runtime-managed file authoring session."""

    cli_result: BossModCliResult
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    chunks: int


@dataclass(frozen=True, slots=True)
class ManagedBatchFileSpec:
    """One file request inside a batch-write manifest."""

    path: str
    goal: str


@dataclass(frozen=True, slots=True)
class ManagedGeneratedFile:
    """One generated file body prior to writing it to disk."""

    path: str
    goal: str
    content: str
    chars: int
    byte_count: int
    calls: int
    strategy: str
    section_count: int


@dataclass(frozen=True, slots=True)
class ManagedGenerationOutcome:
    """Generation outcome for one file body."""

    content: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    chunks: int
    byte_count: int
    strategy: str
    section_count: int
    cli_result: BossModCliResult | None = None


def _render_managed_writer_prompt(template_key: str, context: dict[str, Any]) -> str:
    """Render one file-backed managed-writer prompt template."""
    return render_default_prompt(
        template_key,
        context,
        allowed_paths=_MANAGED_WRITER_PROMPT_ALLOWED_PATHS,
    )


@dataclass(frozen=True, slots=True)
class ManagedDirectDraftOutcome:
    """Outcome of the initial direct authoring attempt."""

    content: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    byte_count: int
    needs_section_plan: bool
    cli_result: BossModCliResult | None = None


@dataclass(frozen=True, slots=True)
class ManagedSectionPlan:
    """One authored section in a planned long-form file."""

    heading: str
    goal: str


@dataclass(frozen=True, slots=True)
class ManagedWriteProgress:
    """One runtime-visible progress update emitted during managed authoring."""

    stage: str
    detail: str
    path: str | None = None
    file_index: int | None = None
    file_count: int | None = None
    section_index: int | None = None
    section_count: int | None = None
    strategy: str | None = None
    counts_as_progress: bool = False


ManagedWriteProgressCallback = Callable[[ManagedWriteProgress], Awaitable[None]]


def is_managed_write_request(command: str, content: str | None) -> bool:
    """Return whether a CLI call should use the runtime-managed single-file writer."""
    if content is not None and content.strip():
        return False
    return _is_command_name(command, "write", expected_args=1)


def is_managed_batch_write_request(command: str, content: str | None) -> bool:
    """Return whether a CLI call should use the runtime-managed batch writer."""
    if content is None or not content.strip():
        return False
    return _is_command_name(command, "bwrite", expected_args=0)


def is_managed_section_rewrite_request(command: str, content: str | None) -> bool:
    """Return whether a CLI call should use the runtime-managed section rewriter."""
    if content is None or not content.strip():
        return False
    return _is_command_name(command, "rewsect", expected_args=2)


def _is_command_name(command: str, expected_name: str, *, expected_args: int) -> bool:
    """Return whether the command parses to the expected name and arity."""
    try:
        parsed = parse_cli_command(command)
    except ValueError:
        return False
    return parsed.name == expected_name and len(parsed.args) == expected_args


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


async def run_managed_write(
    *,
    agent: Agent,
    state: AgentState,
    command: str,
    model: str,
    api_config: dict[str, Any],
    base_context: list[dict[str, str]],
    action_response: str,
    trigger_type: str | None,
    progress_callback: ManagedWriteProgressCallback | None = None,
) -> ManagedWriteOutcome:
    """Generate one file body with adaptive authoring, then write it once."""
    parsed = parse_cli_command(command)
    target_path = parsed.args[0]
    generation = await _generate_managed_file(
        target_path=target_path,
        file_goal=None,
        file_index=1,
        file_count=1,
        command=command,
        model=model,
        api_config=api_config,
        base_context=base_context,
        action_response=action_response,
        cwd=get_cli_cwd(agent.id),
        progress_callback=progress_callback,
    )
    if generation.cli_result is not None:
        return ManagedWriteOutcome(
            cli_result=generation.cli_result,
            prompt_tokens=generation.prompt_tokens,
            completion_tokens=generation.completion_tokens,
            total_tokens=generation.total_tokens,
            chunks=generation.chunks,
        )

    cli_result = execute_bm_cli(
        agent,
        state,
        command,
        generation.content,
        trigger_type=trigger_type,
    )
    if cli_result.ok:
        await _emit_progress(
            progress_callback,
            ManagedWriteProgress(
                stage="file_saved",
                detail=f"Saved {target_path}",
                path=target_path,
                file_index=1,
                file_count=1,
                strategy=generation.strategy,
                section_count=generation.section_count,
                counts_as_progress=True,
            ),
        )
    cli_result = _annotate_managed_writer_result(
        cli_result,
        prompt_tokens=generation.prompt_tokens,
        completion_tokens=generation.completion_tokens,
        total_tokens=generation.total_tokens,
        chunks=generation.chunks,
        byte_count=generation.byte_count,
        completed=True,
        strategy=generation.strategy,
        section_count=generation.section_count,
    )
    return ManagedWriteOutcome(
        cli_result=cli_result,
        prompt_tokens=generation.prompt_tokens,
        completion_tokens=generation.completion_tokens,
        total_tokens=generation.total_tokens,
        chunks=generation.chunks,
    )


async def run_managed_batch_write(
    *,
    agent: Agent,
    state: AgentState,
    command: str,
    content: str,
    model: str,
    api_config: dict[str, Any],
    base_context: list[dict[str, str]],
    action_response: str,
    trigger_type: str | None,
    progress_callback: ManagedWriteProgressCallback | None = None,
) -> ManagedWriteOutcome:
    """Generate multiple file bodies, write them, and commit the batch once."""
    del state
    cwd = get_cli_cwd(agent.id)
    parsed = parse_cli_command(command)
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    chunks = 0
    accumulated_bytes = 0

    if parsed.args:
        cli_result = _managed_writer_error_result(
            command,
            'Batch-write does not take path arguments. Put the file manifest in the body.',
            cwd=cwd,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            chunks=0,
            byte_count=0,
            strategy="batch",
            section_count=0,
        )
        cli_result = _annotate_batch_writer_result(cli_result, completed=False, file_count=0)
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)

    try:
        file_specs = _parse_batch_write_manifest(content)
    except ValueError as exc:
        cli_result = _managed_writer_error_result(
            command,
            str(exc),
            cwd=cwd,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            chunks=0,
            byte_count=0,
            strategy="batch",
            section_count=0,
        )
        cli_result = _annotate_batch_writer_result(cli_result, completed=False, file_count=0)
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)

    resolved_specs: list[ManagedBatchFileSpec] = []
    for spec in file_specs:
        try:
            resolved = resolve_cli_path(agent.storage_key, cwd, spec.path)
        except ValueError as exc:
            cli_result = _managed_writer_error_result(
                command,
                str(exc),
                cwd=cwd,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                chunks=0,
                byte_count=0,
                strategy="batch",
                section_count=0,
            )
            cli_result = _annotate_batch_writer_result(cli_result, completed=False, file_count=len(file_specs))
            cli_result = _finalize_managed_cli_result(
                agent=agent,
                command=command,
                content=content,
                cwd_before=cwd,
                cli_result=cli_result,
                trigger_type=trigger_type,
            )
            return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)
        if resolved.real_path is None:
            cli_result = _managed_writer_error_result(
                command,
                f"Cannot write the virtual root: {spec.path}",
                cwd=cwd,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                chunks=0,
                byte_count=0,
                strategy="batch",
                section_count=0,
            )
            cli_result = _annotate_batch_writer_result(cli_result, completed=False, file_count=len(file_specs))
            cli_result = _finalize_managed_cli_result(
                agent=agent,
                command=command,
                content=content,
                cwd_before=cwd,
                cli_result=cli_result,
                trigger_type=trigger_type,
            )
            return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)
        if resolved.exists and resolved.real_path.is_dir():
            cli_result = _managed_writer_error_result(
                command,
                f"Cannot write directory: {spec.path}",
                cwd=cwd,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                chunks=0,
                byte_count=0,
                strategy="batch",
                section_count=0,
            )
            cli_result = _annotate_batch_writer_result(cli_result, completed=False, file_count=len(file_specs))
            cli_result = _finalize_managed_cli_result(
                agent=agent,
                command=command,
                content=content,
                cwd_before=cwd,
                cli_result=cli_result,
                trigger_type=trigger_type,
            )
            return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)
        resolved_specs.append(ManagedBatchFileSpec(path=resolved.virtual_path, goal=spec.goal))

    await _emit_progress(
        progress_callback,
        ManagedWriteProgress(
            stage="batch_started",
            detail=f"Starting bwrite for {len(resolved_specs)} file{'s' if len(resolved_specs) != 1 else ''}",
            file_count=len(resolved_specs),
            strategy="batch",
        ),
    )

    generated_files: list[ManagedGeneratedFile] = []
    for index, spec in enumerate(resolved_specs, start=1):
        generation = await _generate_managed_file(
            target_path=spec.path,
            file_goal=spec.goal,
            file_index=index,
            file_count=len(resolved_specs),
            command=command,
            model=model,
            api_config=api_config,
            base_context=base_context,
            action_response=action_response,
            cwd=cwd,
            progress_callback=progress_callback,
        )
        prompt_tokens += generation.prompt_tokens
        completion_tokens += generation.completion_tokens
        total_tokens += generation.total_tokens
        chunks += generation.chunks
        accumulated_bytes += generation.byte_count
        if generation.cli_result is not None:
            cli_result = _annotate_batch_writer_result(
                generation.cli_result,
                completed=False,
                file_count=len(resolved_specs),
            )
            cli_result = _finalize_managed_cli_result(
                agent=agent,
                command=command,
                content=content,
                cwd_before=cwd,
                cli_result=cli_result,
                trigger_type=trigger_type,
            )
            return ManagedWriteOutcome(
                cli_result=cli_result,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=chunks,
            )
        generated_files.append(
            ManagedGeneratedFile(
                path=spec.path,
                goal=spec.goal,
                content=generation.content or "",
                chars=len(generation.content or ""),
                byte_count=generation.byte_count,
                calls=generation.chunks,
                strategy=generation.strategy,
                section_count=generation.section_count,
            )
        )

    file_rows: list[dict[str, Any]] = []
    for generated in generated_files:
        write_outcome = write_virtual_text(
            agent,
            cwd=cwd,
            raw_path=generated.path,
            content=generated.content,
            auto_commit=False,
        )
        file_rows.append(
            {
                "path": write_outcome.virtual_path,
                "goal": generated.goal,
                "chars": write_outcome.chars,
                "calls": generated.calls,
                "chunks": generated.calls,
                "strategy": generated.strategy,
                "sections": generated.section_count,
                "bytes": generated.byte_count,
            }
        )
        await _emit_progress(
            progress_callback,
            ManagedWriteProgress(
                stage="file_saved",
                detail=f"Saved {write_outcome.virtual_path}",
                path=write_outcome.virtual_path,
                file_index=len(file_rows),
                file_count=len(generated_files),
                strategy=generated.strategy,
                section_count=generated.section_count,
                counts_as_progress=True,
            ),
        )

    paths = [str(row["path"]) for row in file_rows]
    batch_strategies = sorted({str(row["strategy"]) for row in file_rows})
    batch_strategy = batch_strategies[0] if len(batch_strategies) == 1 else "mixed"
    commit_sha = commit_workspace_changes(
        agent,
        paths,
        reason=f"bm_cli bwrite {len(paths)} file{'s' if len(paths) != 1 else ''}",
    )
    total_chars = sum(int(row["chars"]) for row in file_rows)
    data: dict[str, Any] = {
        "cwd": cwd,
        "paths": paths,
        "files": file_rows,
        "chars": total_chars,
    }
    if commit_sha:
        data["git_commit"] = commit_sha
    batch_lines = [
        f"files: {len(paths)}",
        f"chars: {total_chars}",
    ]
    if commit_sha:
        batch_lines.append(f"git_commit: {commit_sha}")
    cli_result = success_result(
        command=command,
        detail=f"{agent.name} wrote {len(paths)} files",
        kind="batch-write",
        data=data,
        sections=[
            ("BATCH WRITE RESULT", batch_lines),
            (
                "FILES",
                [
                    (
                        f"- {row['path']} ({row['chars']} chars, {row['strategy']}, "
                        f"{row['calls']} call{'s' if int(row['calls']) != 1 else ''})"
                    )
                    for row in file_rows
                ],
            ),
        ],
        cwd=cwd,
        authoritative_note="The batch write succeeded inside the bounded BossMod artifact area.",
    )
    await _emit_progress(
        progress_callback,
        ManagedWriteProgress(
            stage="batch_saved",
            detail=f"Saved {len(paths)} batch-written file{'s' if len(paths) != 1 else ''}",
            file_count=len(paths),
            strategy=batch_strategy,
            counts_as_progress=True,
        ),
    )
    cli_result = _annotate_managed_writer_result(
        cli_result,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=chunks,
        byte_count=accumulated_bytes,
        completed=True,
        strategy=batch_strategy,
        section_count=sum(int(row["sections"]) for row in file_rows),
    )
    cli_result = _annotate_batch_writer_result(
        cli_result,
        completed=True,
        file_count=len(file_rows),
        files=file_rows,
    )
    cli_result = _finalize_managed_cli_result(
        agent=agent,
        command=command,
        content=content,
        cwd_before=cwd,
        cli_result=cli_result,
        trigger_type=trigger_type,
    )
    return ManagedWriteOutcome(
        cli_result=cli_result,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=chunks,
    )


async def run_managed_section_rewrite(
    *,
    agent: Agent,
    state: AgentState,
    command: str,
    content: str,
    model: str,
    api_config: dict[str, Any],
    base_context: list[dict[str, str]],
    action_response: str,
    trigger_type: str | None,
    progress_callback: ManagedWriteProgressCallback | None = None,
) -> ManagedWriteOutcome:
    """Rewrite one markdown section body through a single managed model call."""
    cwd = get_cli_cwd(agent.id)
    try:
        parsed = parse_cli_command(command)
    except ValueError as exc:
        cli_result = _managed_writer_error_result(
            command,
            str(exc),
            cwd=cwd,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            chunks=0,
            byte_count=0,
            strategy="section_rewrite",
            section_count=1,
        )
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)

    if len(parsed.args) != 2:
        cli_result = _managed_writer_error_result(
            command,
            'Rewrite-section requires a path and a quoted heading selector.',
            cwd=cwd,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            chunks=0,
            byte_count=0,
            strategy="section_rewrite",
            section_count=1,
        )
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)

    rewrite_goal = content.strip()
    if not rewrite_goal:
        cli_result = _managed_writer_error_result(
            command,
            "Rewrite-section requires a non-empty rewrite goal in the body.",
            cwd=cwd,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            chunks=0,
            byte_count=0,
            strategy="section_rewrite",
            section_count=1,
        )
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)

    target_path, selector = parsed.args
    resolved = resolve_cli_path(agent.storage_key, cwd, target_path)
    if not resolved.exists or resolved.real_path is None:
        cli_result = _managed_writer_error_result(
            command,
            f"File not found: {target_path}",
            cwd=cwd,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            chunks=0,
            byte_count=0,
            strategy="section_rewrite",
            section_count=1,
        )
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)
    if resolved.real_path.is_dir():
        cli_result = _managed_writer_error_result(
            command,
            f"Cannot rewrite a directory: {target_path}",
            cwd=cwd,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            chunks=0,
            byte_count=0,
            strategy="section_rewrite",
            section_count=1,
        )
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)

    source_text = resolved.real_path.read_text(encoding="utf-8", errors="replace")
    document = parse_markdown_document(source_text)
    if not document.sections:
        cli_result = _managed_writer_error_result(
            command,
            f"No markdown headings were found in {resolved.virtual_path}.",
            cwd=cwd,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            chunks=0,
            byte_count=0,
            strategy="section_rewrite",
            section_count=1,
        )
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)

    try:
        target_section = find_markdown_section(document, selector)
    except ValueError as exc:
        cli_result = _managed_writer_error_result(
            command,
            str(exc),
            cwd=cwd,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            chunks=0,
            byte_count=0,
            strategy="section_rewrite",
            section_count=1,
        )
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(cli_result=cli_result, prompt_tokens=0, completion_tokens=0, total_tokens=0, chunks=0)

    target_index = list(document.sections).index(target_section)
    previous_heading = document.sections[target_index - 1].display_heading if target_index > 0 else None
    next_heading = document.sections[target_index + 1].display_heading if target_index + 1 < len(document.sections) else None
    current_body = get_markdown_section_body(document, target_section)
    outline_lines = render_markdown_outline_entries(document)

    await _emit_progress(
        progress_callback,
        ManagedWriteProgress(
            stage="file_started",
            detail=f"Rewriting {target_section.display_heading} in {resolved.virtual_path}",
            path=resolved.virtual_path,
            file_index=1,
            file_count=1,
            section_index=1,
            section_count=1,
            strategy="section_rewrite",
        ),
    )

    response = await client.completion(
        model=model,
        messages=[
            *base_context,
            {"role": "assistant", "content": action_response},
            {
                "role": "system",
                "content": _managed_section_rewrite_instruction(
                    target_path=resolved.virtual_path,
                    section_heading=target_section.display_heading,
                    rewrite_goal=rewrite_goal,
                    current_body=current_body,
                    outline_lines=outline_lines,
                    previous_heading=previous_heading,
                    next_heading=next_heading,
                ),
            },
        ],
        api_base=api_config.get("api_base"),
        api_key=api_config.get("api_key"),
        extra_body=api_config.get("extra_body"),
    )

    prompt_tokens = response.prompt_tokens
    completion_tokens = response.completion_tokens
    total_tokens = response.total_tokens
    rewritten_body = _normalize_generated_text(response.content).strip()
    rewritten_body = _strip_duplicate_heading(rewritten_body, target_section.display_heading).strip()
    byte_count = len(rewritten_body.encode("utf-8"))
    byte_limit = _managed_write_byte_limit()

    if not rewritten_body:
        cli_result = _managed_writer_error_result(
            command,
            f'Managed writer produced an empty rewritten body for "{target_section.display_heading}".',
            cwd=cwd,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=1,
            byte_count=byte_count,
            strategy="section_rewrite",
            section_count=1,
        )
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(
            cli_result=cli_result,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=1,
        )

    if byte_count > byte_limit:
        cli_result = _managed_writer_error_result(
            command,
            f"Managed writer exceeded maximum write size ({byte_count:,} bytes > {byte_limit:,} byte limit).",
            cwd=cwd,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=1,
            byte_count=byte_count,
            strategy="section_rewrite",
            section_count=1,
        )
        cli_result = _finalize_managed_cli_result(
            agent=agent,
            command=command,
            content=content,
            cwd_before=cwd,
            cli_result=cli_result,
            trigger_type=trigger_type,
        )
        return ManagedWriteOutcome(
            cli_result=cli_result,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=1,
        )

    updated_text = replace_markdown_section_body(document, target_section, rewritten_body)
    write_outcome = write_virtual_text(
        agent,
        cwd=cwd,
        raw_path=resolved.virtual_path,
        content=updated_text,
        reason=f"bm_cli rewsect {resolved.virtual_path} {target_section.display_heading}",
        allow_empty=True,
    )
    data: dict[str, Any] = {
        "cwd": cwd,
        "path": write_outcome.virtual_path,
        "heading": target_section.display_heading,
        "line": target_section.heading_line,
        "chars": write_outcome.chars,
    }
    if write_outcome.commit_sha:
        data["git_commit"] = write_outcome.commit_sha
    cli_result = success_result(
        command=command,
        detail=f"{agent.name} rewrote {target_section.display_heading} in {write_outcome.virtual_path}",
        kind="rewrite-section",
        data=data,
        sections=[
            (
                "SECTION REWRITE RESULT",
                [
                    f"path: {write_outcome.virtual_path}",
                    f"section: {target_section.display_heading} (line {target_section.heading_line})",
                    f"chars: {write_outcome.chars}",
                    "preview:",
                    rewritten_body[:600] if len(rewritten_body) <= 600 else rewritten_body[:597] + "...",
                ],
            )
        ],
        cwd=cwd,
        authoritative_note="Only the targeted markdown section was rewritten and saved.",
    )
    await _emit_progress(
        progress_callback,
        ManagedWriteProgress(
            stage="file_saved",
            detail=f"Saved rewritten {target_section.display_heading} in {write_outcome.virtual_path}",
            path=write_outcome.virtual_path,
            file_index=1,
            file_count=1,
            section_index=1,
            section_count=1,
            strategy="section_rewrite",
            counts_as_progress=True,
        ),
    )
    cli_result = _annotate_managed_writer_result(
        cli_result,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=1,
        byte_count=byte_count,
        completed=True,
        strategy="section_rewrite",
        section_count=1,
    )
    cli_result = _finalize_managed_cli_result(
        agent=agent,
        command=command,
        content=content,
        cwd_before=cwd,
        cli_result=cli_result,
        trigger_type=trigger_type,
    )
    return ManagedWriteOutcome(
        cli_result=cli_result,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=1,
    )


async def _generate_managed_file(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
    command: str,
    model: str,
    api_config: dict[str, Any],
    base_context: list[dict[str, str]],
    action_response: str,
    cwd: str,
    progress_callback: ManagedWriteProgressCallback | None = None,
) -> ManagedGenerationOutcome:
    """Generate one file body using direct or planned section authoring."""
    await _emit_progress(
        progress_callback,
        ManagedWriteProgress(
            stage="file_started",
            detail=f"Writing {target_path}",
            path=target_path,
            file_index=file_index,
            file_count=file_count,
        ),
    )
    direct = await _generate_direct_file(
        target_path=target_path,
        file_goal=file_goal,
        file_index=file_index,
        file_count=file_count,
        command=command,
        model=model,
        api_config=api_config,
        base_context=base_context,
        action_response=action_response,
        cwd=cwd,
        progress_callback=progress_callback,
    )
    if direct.cli_result is not None:
        return ManagedGenerationOutcome(
            content=None,
            prompt_tokens=direct.prompt_tokens,
            completion_tokens=direct.completion_tokens,
            total_tokens=direct.total_tokens,
            chunks=1,
            byte_count=direct.byte_count,
            strategy="single_pass",
            section_count=0,
            cli_result=direct.cli_result,
        )
    if not direct.needs_section_plan:
        return ManagedGenerationOutcome(
            content=direct.content,
            prompt_tokens=direct.prompt_tokens,
            completion_tokens=direct.completion_tokens,
            total_tokens=direct.total_tokens,
            chunks=1,
            byte_count=direct.byte_count,
            strategy="single_pass",
            section_count=0,
        )

    sectioned = await _generate_sectioned_file(
        target_path=target_path,
        file_goal=file_goal,
        file_index=file_index,
        file_count=file_count,
        command=command,
        model=model,
        api_config=api_config,
        base_context=base_context,
        action_response=action_response,
        cwd=cwd,
        progress_callback=progress_callback,
    )
    return ManagedGenerationOutcome(
        content=sectioned.content,
        prompt_tokens=direct.prompt_tokens + sectioned.prompt_tokens,
        completion_tokens=direct.completion_tokens + sectioned.completion_tokens,
        total_tokens=direct.total_tokens + sectioned.total_tokens,
        chunks=1 + sectioned.chunks,
        byte_count=sectioned.byte_count,
        strategy=sectioned.strategy,
        section_count=sectioned.section_count,
        cli_result=sectioned.cli_result,
    )


async def _generate_direct_file(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
    command: str,
    model: str,
    api_config: dict[str, Any],
    base_context: list[dict[str, str]],
    action_response: str,
    cwd: str,
    progress_callback: ManagedWriteProgressCallback | None = None,
) -> ManagedDirectDraftOutcome:
    """Attempt to author the full file in one provider call."""
    del progress_callback
    byte_limit = _managed_write_byte_limit()
    response = await client.completion(
        model=model,
        messages=[
            *base_context,
            {"role": "assistant", "content": action_response},
            {
                "role": "system",
                "content": _managed_single_pass_instruction(
                    target_path=target_path,
                    file_goal=file_goal,
                    file_index=file_index,
                    file_count=file_count,
                ),
            },
        ],
        api_base=api_config.get("api_base"),
        api_key=api_config.get("api_key"),
        extra_body=api_config.get("extra_body"),
    )
    text = _normalize_generated_text(response.content)
    prompt_tokens = response.prompt_tokens
    completion_tokens = response.completion_tokens
    total_tokens = response.total_tokens

    if _MANAGED_WRITE_DONE_SENTINEL in text:
        content = _remove_control_tokens(text)
        byte_count = len(content.encode("utf-8"))
        if not content.strip():
            return ManagedDirectDraftOutcome(
                content=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                byte_count=byte_count,
                needs_section_plan=False,
                cli_result=_managed_writer_error_result(
                    command,
                    "Managed writer produced an empty file body.",
                    cwd=cwd,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    chunks=1,
                    byte_count=byte_count,
                    strategy="single_pass",
                    section_count=0,
                ),
            )
        if byte_count > byte_limit:
            return ManagedDirectDraftOutcome(
                content=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                byte_count=byte_count,
                needs_section_plan=False,
                cli_result=_managed_writer_error_result(
                    command,
                    f"Managed writer exceeded maximum write size ({byte_count:,} bytes > {byte_limit:,} byte limit).",
                    cwd=cwd,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    chunks=1,
                    byte_count=byte_count,
                    strategy="single_pass",
                    section_count=0,
                ),
            )
        return ManagedDirectDraftOutcome(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            byte_count=byte_count,
            needs_section_plan=False,
        )

    if _MANAGED_WRITE_PLAN_SENTINEL in text:
        return ManagedDirectDraftOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            byte_count=0,
            needs_section_plan=True,
        )

    if not text.strip():
        return ManagedDirectDraftOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            byte_count=0,
            needs_section_plan=False,
            cli_result=_managed_writer_error_result(
                command,
                "Managed writer returned an empty draft response.",
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=1,
                byte_count=0,
                strategy="single_pass",
                section_count=0,
            ),
        )

    return ManagedDirectDraftOutcome(
        content=None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        byte_count=len(text.encode("utf-8")),
        needs_section_plan=True,
    )


async def _generate_sectioned_file(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
    command: str,
    model: str,
    api_config: dict[str, Any],
    base_context: list[dict[str, str]],
    action_response: str,
    cwd: str,
    progress_callback: ManagedWriteProgressCallback | None = None,
) -> ManagedGenerationOutcome:
    """Generate a file from a planned section outline and local assembly."""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    call_count = 0

    plan_response = await client.completion(
        model=model,
        messages=[
            *base_context,
            {"role": "assistant", "content": action_response},
            {
                "role": "system",
                "content": _managed_section_plan_instruction(
                    target_path=target_path,
                    file_goal=file_goal,
                    file_index=file_index,
                    file_count=file_count,
                ),
            },
        ],
        api_base=api_config.get("api_base"),
        api_key=api_config.get("api_key"),
        extra_body=api_config.get("extra_body"),
    )
    prompt_tokens += plan_response.prompt_tokens
    completion_tokens += plan_response.completion_tokens
    total_tokens += plan_response.total_tokens
    call_count += 1

    try:
        sections = _parse_section_plan(plan_response.content)
    except ValueError as exc:
        return ManagedGenerationOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=call_count,
            byte_count=0,
            strategy="sectioned",
            section_count=0,
            cli_result=_managed_writer_error_result(
                command,
                str(exc),
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=call_count,
                byte_count=0,
                strategy="sectioned",
                section_count=0,
            ),
        )

    await _emit_progress(
        progress_callback,
        ManagedWriteProgress(
            stage="sections_planned",
            detail=f"Planned {len(sections)} section{'s' if len(sections) != 1 else ''} for {target_path}",
            path=target_path,
            file_index=file_index,
            file_count=file_count,
            section_count=len(sections),
            strategy="sectioned",
        ),
    )

    rendered_sections: list[str] = []
    outline_lines = _render_outline_lines(sections)
    for index, section in enumerate(sections, start=1):
        await _emit_progress(
            progress_callback,
            ManagedWriteProgress(
                stage="section_started",
                detail=f"Writing section {index}/{len(sections)} of {target_path}: {section.heading}",
                path=target_path,
                file_index=file_index,
                file_count=file_count,
                section_index=index,
                section_count=len(sections),
                strategy="sectioned",
                counts_as_progress=index > 1,
            ),
        )
        response = await client.completion(
            model=model,
            messages=[
                *base_context,
                {"role": "assistant", "content": action_response},
                {
                    "role": "system",
                    "content": _managed_section_instruction(
                        target_path=target_path,
                        file_goal=file_goal,
                        file_index=file_index,
                        file_count=file_count,
                        section=section,
                        section_index=index,
                        section_count=len(sections),
                        outline_lines=outline_lines,
                    ),
                },
            ],
            api_base=api_config.get("api_base"),
            api_key=api_config.get("api_key"),
            extra_body=api_config.get("extra_body"),
        )
        prompt_tokens += response.prompt_tokens
        completion_tokens += response.completion_tokens
        total_tokens += response.total_tokens
        call_count += 1

        section_body = _normalize_generated_text(response.content).strip()
        section_body = _strip_duplicate_heading(section_body, section.heading).strip()
        if not section_body:
            return ManagedGenerationOutcome(
                content=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=call_count,
                byte_count=sum(len(part.encode("utf-8")) for part in rendered_sections),
                strategy="sectioned",
                section_count=len(sections),
                cli_result=_managed_writer_error_result(
                    command,
                    f'Managed writer produced an empty section body for "{section.heading}".',
                    cwd=cwd,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    chunks=call_count,
                    byte_count=sum(len(part.encode("utf-8")) for part in rendered_sections),
                    strategy="sectioned",
                    section_count=len(sections),
                ),
            )
        rendered_sections.append(_assemble_section(section.heading, section_body))

    content = "\n\n".join(rendered_sections).strip()
    byte_count = len(content.encode("utf-8"))
    byte_limit = _managed_write_byte_limit()
    if byte_count > byte_limit:
        return ManagedGenerationOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=call_count,
            byte_count=byte_count,
            strategy="sectioned",
            section_count=len(sections),
            cli_result=_managed_writer_error_result(
                command,
                f"Managed writer exceeded maximum write size ({byte_count:,} bytes > {byte_limit:,} byte limit).",
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=call_count,
                byte_count=byte_count,
                strategy="sectioned",
                section_count=len(sections),
            ),
        )
    if not content.strip():
        return ManagedGenerationOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=call_count,
            byte_count=byte_count,
            strategy="sectioned",
            section_count=len(sections),
            cli_result=_managed_writer_error_result(
                command,
                "Managed writer produced an empty file body.",
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=call_count,
                byte_count=byte_count,
                strategy="sectioned",
                section_count=len(sections),
            ),
        )
    return ManagedGenerationOutcome(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=call_count,
        byte_count=byte_count,
        strategy="sectioned",
        section_count=len(sections),
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


def _parse_batch_write_manifest(content: str) -> list[ManagedBatchFileSpec]:
    """Parse a short batch-write manifest from JSON or line-based body text."""
    manifest = content.strip()
    if not manifest:
        raise ValueError("Batch-write requires a non-empty manifest body.")
    if manifest.startswith("{") or manifest.startswith("["):
        return _parse_batch_write_json_manifest(manifest)
    return _parse_batch_write_line_manifest(manifest)


def _parse_batch_write_json_manifest(content: str) -> list[ManagedBatchFileSpec]:
    """Parse a JSON batch-write manifest."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Batch-write manifest JSON parse error: {exc.msg}") from exc

    items = parsed
    if isinstance(parsed, dict):
        extra = set(parsed) - {"files"}
        if extra:
            raise ValueError(f'Batch-write manifest has unexpected keys: {", ".join(sorted(extra))}')
        items = parsed.get("files")
    if not isinstance(items, list) or not items:
        raise ValueError('Batch-write manifest must contain a non-empty "files" list.')

    normalized: list[ManagedBatchFileSpec] = []
    seen_paths: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each batch-write file entry must be an object.")
        extra = set(item) - {"path", "goal"}
        if extra:
            raise ValueError(f'Batch-write file entries have unexpected keys: {", ".join(sorted(extra))}')
        path = str(item.get("path") or "").strip()
        goal = str(item.get("goal") or "").strip()
        if not path:
            raise ValueError('Each batch-write file entry requires a non-empty "path".')
        if not goal:
            raise ValueError(f'Batch-write file "{path}" requires a non-empty "goal".')
        if path in seen_paths:
            raise ValueError(f"Batch-write manifest repeats the same file path: {path}")
        seen_paths.add(path)
        normalized.append(ManagedBatchFileSpec(path=path, goal=goal))
    _validate_batch_file_count(normalized)
    return normalized


def _parse_batch_write_line_manifest(content: str) -> list[ManagedBatchFileSpec]:
    """Parse a simple line-based batch-write manifest."""
    normalized: list[ManagedBatchFileSpec] = []
    seen_paths: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "::" not in line:
            raise ValueError('Each batch-write manifest line must use "<path> :: <goal>".')
        path_part, goal_part = line.split("::", 1)
        path = path_part.lstrip("- ").strip()
        goal = goal_part.strip()
        if not path or not goal:
            raise ValueError('Each batch-write manifest line must use "<path> :: <goal>".')
        if path in seen_paths:
            raise ValueError(f"Batch-write manifest repeats the same file path: {path}")
        seen_paths.add(path)
        normalized.append(ManagedBatchFileSpec(path=path, goal=goal))
    if not normalized:
        raise ValueError("Batch-write manifest must include at least one file.")
    _validate_batch_file_count(normalized)
    return normalized


def _validate_batch_file_count(files: list[ManagedBatchFileSpec]) -> None:
    """Reject overly large batch manifests before starting generation."""
    max_files = _managed_max_batch_files()
    if len(files) > max_files:
        raise ValueError(
            f"Batch-write currently supports at most {max_files} files per request. Split larger jobs into smaller batches."
        )


def _parse_section_plan(raw_response: str) -> list[ManagedSectionPlan]:
    """Parse the section-plan JSON returned by the planner call."""
    normalized = _normalize_generated_text(raw_response).strip()
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Managed writer section plan JSON parse error: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Managed writer section plan must be a JSON object.")
    extra = set(parsed) - {"sections"}
    if extra:
        raise ValueError(f'Managed writer section plan has unexpected keys: {", ".join(sorted(extra))}')
    items = parsed.get("sections")
    if not isinstance(items, list) or not items:
        raise ValueError('Managed writer section plan must contain a non-empty "sections" list.')
    max_sections = _managed_max_sections_per_file()
    if len(items) > max_sections:
        raise ValueError(
            f"Managed writer section plan exceeds the {max_sections}-section limit. Narrow the document scope."
        )

    sections: list[ManagedSectionPlan] = []
    seen_headings: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Managed writer section plan entries must be objects.")
        extra_item = set(item) - {"heading", "goal"}
        if extra_item:
            raise ValueError(f'Managed writer section entries have unexpected keys: {", ".join(sorted(extra_item))}')
        heading = str(item.get("heading") or "").strip()
        goal = str(item.get("goal") or "").strip()
        if not heading or not goal:
            raise ValueError('Managed writer section entries require non-empty "heading" and "goal" values.')
        if heading in seen_headings:
            raise ValueError(f"Managed writer section plan repeats the same heading: {heading}")
        seen_headings.add(heading)
        sections.append(ManagedSectionPlan(heading=heading, goal=goal))
    return sections


def _managed_single_pass_instruction(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
) -> str:
    """Return the runtime instruction for one direct file-generation attempt."""
    return _render_managed_writer_prompt(
        "internal_managed_writer_single_pass",
        {
            "target_path": target_path,
            "file_goal": file_goal or "",
            "done_sentinel": _MANAGED_WRITE_DONE_SENTINEL,
            "plan_sentinel": _MANAGED_WRITE_PLAN_SENTINEL,
            "batch": {
                "is_batch": file_count > 1,
                "file_index": file_index,
                "file_count": file_count,
            },
        },
    )


def _managed_section_plan_instruction(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
) -> str:
    """Return the runtime instruction for planning a large document."""
    return _render_managed_writer_prompt(
        "internal_managed_writer_section_plan",
        {
            "target_path": target_path,
            "file_goal": file_goal or "",
            "max_sections": _managed_max_sections_per_file(),
            "batch": {
                "is_batch": file_count > 1,
                "file_index": file_index,
                "file_count": file_count,
            },
        },
    )


def _managed_section_instruction(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
    section: ManagedSectionPlan,
    section_index: int,
    section_count: int,
    outline_lines: list[str],
) -> str:
    """Return the runtime instruction for one section body."""
    return _render_managed_writer_prompt(
        "internal_managed_writer_section",
        {
            "target_path": target_path,
            "file_goal": file_goal or "",
            "section_index": section_index,
            "section_count": section_count,
            "outline": "\n".join(outline_lines),
            "section": {
                "heading": section.heading,
                "goal": section.goal,
            },
            "batch": {
                "is_batch": file_count > 1,
                "file_index": file_index,
                "file_count": file_count,
            },
        },
    )


def _managed_section_rewrite_instruction(
    *,
    target_path: str,
    section_heading: str,
    rewrite_goal: str,
    current_body: str,
    outline_lines: list[str],
    previous_heading: str | None,
    next_heading: str | None,
) -> str:
    """Return the runtime instruction for rewriting one existing section body."""
    return _render_managed_writer_prompt(
        "internal_managed_writer_section_rewrite",
        {
            "target_path": target_path,
            "section_heading": section_heading,
            "rewrite_goal": rewrite_goal,
            "outline": "\n".join(outline_lines),
            "previous_heading": previous_heading or "",
            "next_heading": next_heading or "",
            "current_body": current_body,
        },
    )


def _render_outline_lines(sections: list[ManagedSectionPlan]) -> list[str]:
    """Render section headings/goals into compact instruction lines."""
    return [f"- {section.heading} :: {section.goal}" for section in sections]


def _assemble_section(heading: str, body: str) -> str:
    """Assemble one section deterministically from heading and generated body."""
    clean_body = body.strip()
    return f"{heading}\n\n{clean_body}" if clean_body else heading


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
