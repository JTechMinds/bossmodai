"""BossMod AI — Runtime-managed batch-write entrypoint and manifest parse."""

from __future__ import annotations

import json
from typing import Any

from core.bm_cli.fs_commands import write_virtual_text
from core.bm_cli.managed_writer.generate import _generate_managed_file
from core.bm_cli.managed_writer.helpers import (
    _annotate_batch_writer_result,
    _annotate_managed_writer_result,
    _emit_progress,
    _finalize_managed_cli_result,
    _managed_max_batch_files,
    _managed_writer_error_result,
)
from core.bm_cli.managed_writer.types import (
    ManagedBatchFileSpec,
    ManagedGeneratedFile,
    ManagedWriteOutcome,
    ManagedWriteProgress,
    ManagedWriteProgressCallback,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.results import success_result
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.virtual_fs import resolve_cli_path
from core.bm_cli.workspace_git import commit_workspace_changes
from core.models import Agent, AgentState

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

