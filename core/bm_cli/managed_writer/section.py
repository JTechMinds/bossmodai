"""BossMod AI — Runtime-managed markdown section rewrite entrypoint."""

from __future__ import annotations

from typing import Any

from core.bm_cli.document_tools import (
    find_markdown_section,
    get_markdown_section_body,
    parse_markdown_document,
    replace_markdown_section_body,
    render_markdown_outline_entries,
)
from core.bm_cli.fs_commands import write_virtual_text
from core.bm_cli.managed_writer.helpers import (
    _annotate_managed_writer_result,
    _emit_progress,
    _finalize_managed_cli_result,
    _managed_write_byte_limit,
    _managed_writer_error_result,
    _normalize_generated_text,
    _strip_duplicate_heading,
)
from core.bm_cli.managed_writer.prompts import _managed_section_rewrite_instruction
from core.bm_cli.managed_writer.types import (
    ManagedWriteOutcome,
    ManagedWriteProgress,
    ManagedWriteProgressCallback,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.results import success_result
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.virtual_fs import resolve_cli_path
from core.llm import client
from core.models import Agent, AgentState

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

