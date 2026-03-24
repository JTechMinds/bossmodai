"""BossMod AI — Result rendering helpers for the virtual BossMod CLI."""

from __future__ import annotations

from typing import Any

from core.bm_cli.types import BossModCliResult


def success_result(
    *,
    command: str,
    detail: str,
    kind: str,
    data: dict[str, Any],
    sections: list[tuple[str, list[str]]],
    authoritative_note: str | None = None,
    cwd: str | None = None,
    executor: str = "virtual",
) -> BossModCliResult:
    """Build a successful CLI result with structured and prompt-ready output."""
    return BossModCliResult(
        command=command,
        ok=True,
        detail=detail,
        prompt_content=render_sections(command, sections, authoritative_note=authoritative_note),
        kind=kind,
        data=data,
        cwd=cwd,
        executor=executor,
        exit_code=0,
    )


def error_result(command: str, message: str, *, cwd: str | None = None, executor: str = "virtual") -> BossModCliResult:
    """Build an error result the model can recover from inside the same turn."""
    return BossModCliResult(
        command=command,
        ok=False,
        detail=f"BossMod CLI error: {message}",
        prompt_content=render_sections(
            command,
            [("ERROR", [message, "Pick a supported command or continue without BossMod CLI."])],
        ),
        kind="error",
        data={"error": message},
        cwd=cwd,
        executor=executor,
        exit_code=1,
    )


def approval_required_result(command: str, message: str, *, cwd: str | None = None, executor: str = "virtual") -> BossModCliResult:
    """Build an approval-required result for gated commands."""
    return BossModCliResult(
        command=command,
        ok=False,
        detail=f"BossMod CLI approval required: {message}",
        prompt_content=render_sections(
            command,
            [("APPROVAL REQUIRED", [message, "Choose a safer BossMod CLI command or continue without this operation."])],
        ),
        kind="approval_required",
        data={"approval_required": True, "message": message},
        cwd=cwd,
        approval_required=True,
        executor=executor,
        exit_code=126,
    )


def render_sections(
    command: str,
    sections: list[tuple[str, list[str]]],
    *,
    authoritative_note: str | None = None,
) -> str:
    """Render prompt-friendly CLI output from named sections."""
    lines = ["BOSSMOD CLI RESULT", f"command: {command}"]
    for title, content_lines in sections:
        lines.extend(["", f"{title}:"])
        lines.extend(content_lines or ["none"])
    if authoritative_note:
        lines.extend(["", authoritative_note])
    return "\n".join(lines)


def trim(text: str, *, limit: int = 240) -> str:
    """Trim long output for prompt inclusion without losing the main point."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
