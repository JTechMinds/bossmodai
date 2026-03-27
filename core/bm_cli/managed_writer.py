"""BossMod AI — Managed long-form file writing behind BossMod CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core import config
from core.bm_cli.parser import parse_cli_command
from core.bm_cli.results import error_result, render_sections
from core.bm_cli.runtime import execute_bm_cli
from core.bm_cli.session import get_cli_cwd
from core.bm_cli.types import BossModCliResult
from core.llm import client
from core.models import Agent, AgentState

_MANAGED_WRITE_DONE_SENTINEL = "<<BOSSMOD_FILE_DONE>>"
_MANAGED_WRITE_MAX_CHUNKS = 12
_MANAGED_WRITE_TAIL_CHARS = 1200
_MANAGED_WRITE_TARGET_CHARS = "1200-2400"


@dataclass(frozen=True, slots=True)
class ManagedWriteOutcome:
    """Result of one runtime-managed file write session."""

    cli_result: BossModCliResult
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    chunks: int


def is_managed_write_request(command: str, content: str | None) -> bool:
    """Return whether a CLI call should use the runtime-managed writer."""
    if content is not None and content.strip():
        return False
    try:
        parsed = parse_cli_command(command)
    except ValueError:
        return False
    return parsed.name == "write" and len(parsed.args) == 1


def _annotate_managed_writer_result(
    cli_result: BossModCliResult,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    chunks: int,
    byte_count: int,
    completed: bool,
) -> BossModCliResult:
    """Attach managed-writer metadata to a CLI result for diagnostics and UI."""
    data = dict(cli_result.data or {})
    data["managed_writer_attempted"] = True
    data["managed_writer_used"] = completed
    data["managed_writer_completed"] = completed
    data["managed_chunks"] = chunks
    data["managed_bytes"] = byte_count
    data["managed_prompt_tokens"] = prompt_tokens
    data["managed_completion_tokens"] = completion_tokens
    data["managed_total_tokens"] = total_tokens
    return BossModCliResult(
        command=cli_result.command,
        ok=cli_result.ok,
        detail=cli_result.detail,
        prompt_content=cli_result.prompt_content,
        kind=cli_result.kind,
        data=data,
        cwd=cli_result.cwd,
        approval_required=cli_result.approval_required,
        executor=cli_result.executor,
        exit_code=cli_result.exit_code,
    )


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
) -> BossModCliResult:
    """Build a managed-writer error with explicit recovery guidance and metadata."""
    cli_result = error_result(command, message, cwd=cwd)
    prompt_content = render_sections(
        command,
        [
            (
                "ERROR",
                [
                    message,
                    "Do not paste a long file body into CLI JSON.",
                    "Retry with a narrower section, or save a shorter outline/status update first.",
                ],
            )
        ],
    )
    return _annotate_managed_writer_result(
        BossModCliResult(
            command=cli_result.command,
            ok=cli_result.ok,
            detail=cli_result.detail,
            prompt_content=prompt_content,
            kind=cli_result.kind,
            data=cli_result.data,
            cwd=cli_result.cwd,
            approval_required=cli_result.approval_required,
            executor=cli_result.executor,
            exit_code=cli_result.exit_code,
        ),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=chunks,
        byte_count=byte_count,
        completed=False,
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
) -> ManagedWriteOutcome:
    """Generate a long file body across multiple completions, then write once."""
    parsed = parse_cli_command(command)
    target_path = parsed.args[0]
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    chunks = 0
    accumulated = ""
    cwd = get_cli_cwd(agent.id)
    base_messages = [*base_context, {"role": "assistant", "content": action_response}]
    byte_limit = config.get_int("cli_max_write_bytes") or 262_144
    last_chunk = ""

    for chunk_index in range(1, _MANAGED_WRITE_MAX_CHUNKS + 1):
        response = await client.completion(
            model=model,
            messages=[
                *base_messages,
                {
                    "role": "system",
                    "content": _managed_write_instruction(
                        target_path=target_path,
                        chunk_index=chunk_index,
                        chars_written=len(accumulated),
                        tail=_tail_excerpt(last_chunk or accumulated),
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
        chunk_text, done = _extract_chunk(response.content)
        if not chunk_text and not done:
            return ManagedWriteOutcome(
                cli_result=_managed_writer_error_result(
                    command,
                    "Managed writer returned an empty chunk before the file was finished.",
                    cwd=cwd,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    chunks=chunks,
                    byte_count=len(accumulated.encode("utf-8")),
                ),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=chunks,
            )
        accumulated += chunk_text
        last_chunk = chunk_text
        chunks += 1
        accumulated_bytes = len(accumulated.encode("utf-8"))
        if accumulated_bytes > byte_limit:
            return ManagedWriteOutcome(
                cli_result=_managed_writer_error_result(
                    command,
                    f"Managed writer exceeded maximum write size ({accumulated_bytes:,} bytes > {byte_limit:,} byte limit).",
                    cwd=cwd,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    chunks=chunks,
                    byte_count=accumulated_bytes,
                ),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=chunks,
            )
        if done:
            break
    else:
        return ManagedWriteOutcome(
            cli_result=_managed_writer_error_result(
                command,
                "Managed writer did not finish before the chunk limit. Try again with a narrower deliverable.",
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=chunks,
                byte_count=len(accumulated.encode("utf-8")),
            ),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=chunks,
        )

    if not accumulated.strip():
        return ManagedWriteOutcome(
            cli_result=_managed_writer_error_result(
                command,
                "Managed writer produced an empty file body.",
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=chunks,
                byte_count=len(accumulated.encode("utf-8")),
            ),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=chunks,
        )

    cli_result = execute_bm_cli(
        agent,
        state,
        command,
        accumulated,
        trigger_type=trigger_type,
    )
    cli_result = _annotate_managed_writer_result(
        cli_result,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=chunks,
        byte_count=len(accumulated.encode("utf-8")),
        completed=True,
    )
    return ManagedWriteOutcome(
        cli_result=cli_result,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=chunks,
    )


def _managed_write_instruction(
    *,
    target_path: str,
    chunk_index: int,
    chars_written: int,
    tail: str,
) -> str:
    """Return the runtime instruction for one managed file-write chunk."""
    if chars_written == 0:
        progress = "This is chunk 1. Start at the beginning of the file."
    else:
        progress = (
            f"This is chunk {chunk_index}. Continue exactly where the prior chunk ended. "
            f"The file currently contains {chars_written} characters."
        )
    tail_note = (
        f"Current file tail:\n{tail}"
        if tail
        else "No file content has been written yet."
    )
    return "\n".join(
        [
            f"You are in a managed BossMod file-writer session for {target_path}.",
            progress,
            "Output only the next chunk of the file body as plain UTF-8 text.",
            f"Aim for a substantial chunk of roughly {_MANAGED_WRITE_TARGET_CHARS} characters unless you are finishing the file.",
            "Do not return JSON.",
            "Do not add commentary or explanations.",
            "Do not wrap the output in code fences.",
            "Do not restart or repeat earlier content.",
            f"When the file is complete, append { _MANAGED_WRITE_DONE_SENTINEL } on its own line at the end of the final chunk.",
            tail_note,
        ]
    )


def _extract_chunk(raw_response: str) -> tuple[str, bool]:
    """Normalize one managed-writer chunk and detect the done sentinel."""
    text = _strip_code_fences(raw_response).replace("\r\n", "\n")
    done = _MANAGED_WRITE_DONE_SENTINEL in text
    if done:
        text = text.replace(f"\n{_MANAGED_WRITE_DONE_SENTINEL}", "")
        text = text.replace(_MANAGED_WRITE_DONE_SENTINEL, "")
    return text, done


def _strip_code_fences(text: str) -> str:
    """Remove one outer fenced block when the model adds markdown fences."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.split("\n")
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1])
    return text


def _tail_excerpt(text: str) -> str:
    """Return a short tail excerpt to anchor continuation prompts."""
    if not text:
        return ""
    return text[-_MANAGED_WRITE_TAIL_CHARS:]
