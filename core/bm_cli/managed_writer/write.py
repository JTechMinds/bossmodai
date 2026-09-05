"""BossMod AI — Runtime-managed single-file write entrypoint."""

from __future__ import annotations

from typing import Any

from core.bm_cli.managed_writer.generate import _generate_managed_file
from core.bm_cli.managed_writer.helpers import (
    _annotate_managed_writer_result,
    _emit_progress,
)
from core.bm_cli.managed_writer.types import (
    ManagedWriteOutcome,
    ManagedWriteProgress,
    ManagedWriteProgressCallback,
)
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.session import get_cli_cwd
from core.models import Agent, AgentState

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

