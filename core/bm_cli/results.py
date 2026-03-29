"""BossMod AI — Result rendering helpers for the BossMod CLI."""

from __future__ import annotations

from typing import Any

from core.bm_cli.types import BossModCliResult
from core.default_prompts import load_default_prompt, render_default_prompt


_CLI_RESULT_PROMPT_ALLOWED_PATHS = {"command", "sections", "authoritative_note"}


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
            [("ERROR", [message])],
        ),
        kind="error",
        data={"error": message},
        cwd=cwd,
        executor=executor,
        exit_code=1,
    )


def approval_required_result(
    command: str,
    message: str,
    *,
    cwd: str | None = None,
    executor: str = "virtual",
    matched_rule_id: str | None = None,
    approval_request_id: str | None = None,
) -> BossModCliResult:
    """Build an approval-required result for gated commands."""
    pause_note = load_default_prompt("internal_cli_approval_pause_note")
    return BossModCliResult(
        command=command,
        ok=False,
        detail=f"BossMod CLI approval required: {message}",
        prompt_content=render_sections(
            command,
            [("APPROVAL REQUIRED", [message, pause_note])],
        ),
        kind="approval_required",
        data={"approval_required": True, "message": message},
        cwd=cwd,
        approval_required=True,
        executor=executor,
        exit_code=126,
        matched_rule_id=matched_rule_id,
        approval_request_id=approval_request_id,
    )


def shell_result(
    *,
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    timed_out: bool,
    duration_ms: int,
    cwd: str | None = None,
    matched_rule_id: str | None = None,
) -> BossModCliResult:
    """Build a result from a native shell command execution."""
    ok = exit_code == 0 and not timed_out
    sections: list[tuple[str, list[str]]] = []

    if timed_out:
        sections.append(("TIMEOUT", [f"Command timed out after {duration_ms}ms"]))
    if stdout.strip():
        sections.append(("STDOUT", [stdout.strip()]))
    if stderr.strip():
        sections.append(("STDERR", [stderr.strip()]))
    if not sections:
        sections.append(("OUTPUT", [f"(exit code {exit_code}, no output)"]))

    return BossModCliResult(
        command=command,
        ok=ok,
        detail=f"Shell command {'timed out' if timed_out else 'completed'} (exit {exit_code}, {duration_ms}ms)",
        prompt_content=render_sections(command, sections),
        kind="shell",
        data={
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
        },
        cwd=cwd,
        executor="shell",
        exit_code=exit_code,
        matched_rule_id=matched_rule_id,
    )


def render_sections(
    command: str,
    sections: list[tuple[str, list[str]]],
    *,
    authoritative_note: str | None = None,
) -> str:
    """Render prompt-friendly CLI output from named sections."""
    section_lines: list[str] = []
    for title, content_lines in sections:
        section_lines.extend(["", f"{title}:"])
        section_lines.extend(content_lines or ["none"])
    return render_default_prompt(
        "internal_cli_result_wrapper",
        {
            "command": command,
            "sections": "\n".join(section_lines),
            "authoritative_note": authoritative_note or "",
        },
        allowed_paths=_CLI_RESULT_PROMPT_ALLOWED_PATHS,
    )


def trim(text: str, *, limit: int = 240) -> str:
    """Trim long output for prompt inclusion without losing the main point."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
