"""BossMod AI — Filesystem-style virtual BossMod CLI commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from core import config
from core.bm_cli.document_tools import (
    describe_markdown_section,
    find_markdown_section,
    parse_markdown_document,
    replace_markdown_section_body,
    render_markdown_outline_entries,
)
from core.bm_cli.results import error_result, success_result, trim
from core.bm_cli.session import set_cli_cwd
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path, virtual_root_entries
from core.bm_cli.workspace_git import auto_commit_workspace_change


@dataclass(frozen=True, slots=True)
class FileWriteOutcome:
    """Normalized result of one bounded virtual file write."""

    virtual_path: str
    chars: int
    commit_sha: str | None = None


_LINE_RANGE_RE = re.compile(r"^(\d+):(\d+)$")


def _max_write_bytes() -> int:
    return config.get_int("cli_max_write_bytes")


def _max_read_lines() -> int:
    return config.require_int("cli_max_read_lines")


def handle_pwd(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Return the current virtual CLI working directory."""
    if parsed.args:
        return error_result(parsed.raw, '"pwd" does not take any arguments.', cwd=context.cwd)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} checked the BossMod CLI working directory",
        kind="cwd",
        data={"cwd": context.cwd},
        sections=[("WORKING DIRECTORY", [context.cwd])],
        cwd=context.cwd,
    )


def handle_cd(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Change the persistent virtual CLI working directory."""
    if len(parsed.args) != 1:
        return error_result(parsed.raw, '"cd" requires exactly one path argument.', cwd=context.cwd)
    target = resolve_cli_path(context.agent.storage_key, context.cwd, parsed.args[0])
    if not target.exists or target.real_path is None or not target.real_path.is_dir():
        return error_result(parsed.raw, f"Directory not found: {parsed.args[0]}", cwd=context.cwd)
    new_cwd = set_cli_cwd(context.agent.id, target.virtual_path)
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} changed the BossMod CLI working directory",
        kind="cwd",
        data={"cwd": new_cwd},
        sections=[("WORKING DIRECTORY", [new_cwd])],
        cwd=new_cwd,
    )


def handle_ls(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """List entries in the current or target virtual directory."""
    if len(parsed.args) > 1:
        return error_result(parsed.raw, '"ls" accepts at most one path argument.', cwd=context.cwd)
    target = resolve_cli_path(context.agent.storage_key, context.cwd, parsed.args[0] if parsed.args else None)
    if target.mount == "root" and target.real_path is None:
        entries = virtual_root_entries()
        lines = [f"- {entry}" for entry in entries]
        return success_result(
            command=parsed.raw,
            detail=f"{context.agent.name} listed the BossMod CLI root",
            kind="listing",
            data={"cwd": context.cwd, "target": target.virtual_path, "entries": entries},
            sections=[("LISTING", lines)],
            cwd=context.cwd,
        )
    if not target.exists or target.real_path is None:
        return error_result(parsed.raw, f"Path not found: {parsed.args[0] if parsed.args else context.cwd}", cwd=context.cwd)
    if target.real_path.is_file():
        entry_name = Path(target.virtual_path).name
        return success_result(
            command=parsed.raw,
            detail=f"{context.agent.name} listed {target.virtual_path}",
            kind="listing",
            data={"cwd": context.cwd, "target": target.virtual_path, "entries": [entry_name]},
            sections=[("LISTING", [f"- {entry_name}"])],
            cwd=context.cwd,
        )
    entries = sorted(target.real_path.iterdir(), key=lambda item: item.name.lower())
    labels = [entry.name + ("/" if entry.is_dir() else "") for entry in entries]
    lines = [f"- {label}" for label in labels] or ["none"]
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} listed {target.virtual_path}",
        kind="listing",
        data={"cwd": context.cwd, "target": target.virtual_path, "entries": labels},
        sections=[("LISTING", lines)],
        cwd=context.cwd,
    )


def handle_cat(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Read a file from the bounded virtual filesystem."""
    if len(parsed.args) != 1:
        return error_result(parsed.raw, '"cat" requires exactly one path argument.', cwd=context.cwd)
    target = resolve_cli_path(context.agent.storage_key, context.cwd, parsed.args[0])
    if not target.exists or target.real_path is None:
        return error_result(parsed.raw, f"File not found: {parsed.args[0]}", cwd=context.cwd)
    if target.real_path.is_dir():
        return error_result(parsed.raw, f"Cannot read directory: {parsed.args[0]}", cwd=context.cwd)
    body = target.real_path.read_text(encoding="utf-8", errors="replace")
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} read {target.virtual_path}",
        kind="file",
        data={"cwd": context.cwd, "path": target.virtual_path, "content": body},
        sections=[(f"FILE: {target.virtual_path}", [trim(body, limit=4000)])],
        cwd=context.cwd,
    )


def handle_outline(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """List markdown headings with line numbers for one file."""
    del content
    if len(parsed.args) != 1:
        return error_result(parsed.raw, '"ol" requires exactly one path argument.', cwd=context.cwd)
    target, body, result = _read_virtual_file(context, parsed, raw_path=parsed.args[0], command_name="ol")
    if result is not None:
        return result
    assert target is not None
    document = parse_markdown_document(body)
    outline_lines = render_markdown_outline_entries(document)
    data = {
        "cwd": context.cwd,
        "path": target.virtual_path,
        "sections": [
            {
                "heading": section.display_heading,
                "title": section.title,
                "level": section.level,
                "line": section.heading_line,
            }
            for section in document.sections
        ],
    }
    lines = [f"path: {target.virtual_path}"]
    if outline_lines:
        lines.append(f"sections: {len(outline_lines)}")
        lines.extend(outline_lines)
    else:
        lines.append("No markdown headings found.")
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} outlined {target.virtual_path}",
        kind="outline",
        data=data,
        sections=[("OUTLINE", lines)],
        cwd=context.cwd,
    )


def handle_read_range(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Read a bounded line range from one file with 1-based numbering."""
    del content
    if len(parsed.args) != 2:
        return error_result(parsed.raw, '"rr" requires a path plus a start:end line range.', cwd=context.cwd)
    try:
        start_line, end_line = _parse_line_range(parsed.args[1])
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)
    max_lines = _max_read_lines()
    requested_count = end_line - start_line + 1
    if requested_count > max_lines:
        return error_result(
            parsed.raw,
            f"Requested range is too large ({requested_count} lines > {max_lines}-line limit). Split it into smaller reads.",
            cwd=context.cwd,
        )

    target, body, result = _read_virtual_file(context, parsed, raw_path=parsed.args[0], command_name="rr")
    if result is not None:
        return result
    assert target is not None

    lines = body.splitlines()
    if not lines:
        if start_line != 1:
            return error_result(parsed.raw, f"Line {start_line} is outside the file (file is empty).", cwd=context.cwd)
        actual_end = 0
        rendered_lines = ["(empty file)"]
        payload_lines: list[dict[str, str | int]] = []
    else:
        if start_line > len(lines):
            return error_result(
                parsed.raw,
                f"Line {start_line} is outside the file (last line is {len(lines)}).",
                cwd=context.cwd,
            )
        actual_end = min(end_line, len(lines))
        payload_lines = [
            {"line": index, "text": lines[index - 1]}
            for index in range(start_line, actual_end + 1)
        ]
        rendered_lines = [f"{item['line']:>4} | {item['text']}" for item in payload_lines]

    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} read lines {start_line}:{actual_end} from {target.virtual_path}",
        kind="read-range",
        data={
            "cwd": context.cwd,
            "path": target.virtual_path,
            "start": start_line,
            "end": actual_end,
            "lines": payload_lines,
        },
        sections=[
            (
                "LINE RANGE",
                [
                    f"path: {target.virtual_path}",
                    f"range: {start_line}:{actual_end}",
                    *rendered_lines,
                ],
            )
        ],
        cwd=context.cwd,
    )


def handle_mkdir(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Create a directory inside the bounded virtual filesystem."""
    if len(parsed.args) != 1:
        return error_result(parsed.raw, '"mkdir" requires exactly one path argument.', cwd=context.cwd)
    target = resolve_cli_path(context.agent.storage_key, context.cwd, parsed.args[0])
    if target.real_path is None:
        return error_result(parsed.raw, "Cannot create the virtual root.", cwd=context.cwd)
    if target.exists and target.real_path.is_file():
        return error_result(parsed.raw, f"Cannot create directory over file: {parsed.args[0]}", cwd=context.cwd)
    target.real_path.mkdir(parents=True, exist_ok=True)
    commit_sha = auto_commit_workspace_change(
        context.agent,
        target.virtual_path,
        reason=f"bm_cli mkdir {target.virtual_path}",
    )
    data = {"cwd": context.cwd, "path": target.virtual_path}
    if commit_sha:
        data["git_commit"] = commit_sha
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} created directory {target.virtual_path}",
        kind="mkdir",
        data=data,
        sections=[("DIRECTORY CREATED", [target.virtual_path])],
        cwd=context.cwd,
        authoritative_note="The directory now exists inside the bounded BossMod artifact area.",
    )


def handle_write(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Write a full file body into the bounded virtual filesystem."""
    if len(parsed.args) != 1:
        return error_result(parsed.raw, '"write" requires exactly one path argument.', cwd=context.cwd)
    if content is None or not content.strip():
        return error_result(parsed.raw, 'Write commands require a non-empty "content" field.', cwd=context.cwd)
    try:
        outcome = write_virtual_text(
            context.agent,
            cwd=context.cwd,
            raw_path=parsed.args[0],
            content=content,
            reason=f"bm_cli write {parsed.args[0]}",
        )
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)

    data = {"cwd": context.cwd, "path": outcome.virtual_path, "chars": outcome.chars}
    if outcome.commit_sha:
        data["git_commit"] = outcome.commit_sha
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} wrote {outcome.virtual_path}",
        kind="write",
        data=data,
        sections=[
            ("WRITE RESULT", [
                f"path: {outcome.virtual_path}",
                f"chars: {outcome.chars}",
                "preview:",
                trim(normalize_write_content(content), limit=600),
            ])
        ],
        cwd=context.cwd,
        authoritative_note="The file write succeeded inside the bounded BossMod artifact area.",
    )


def handle_append(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Append text to a bounded file in the virtual filesystem."""
    if len(parsed.args) != 1:
        return error_result(parsed.raw, '"append" requires exactly one path argument.', cwd=context.cwd)
    if content is None or not content.strip():
        return error_result(parsed.raw, 'Append commands require a non-empty "content" field.', cwd=context.cwd)
    try:
        outcome = write_virtual_text(
            context.agent,
            cwd=context.cwd,
            raw_path=parsed.args[0],
            content=content,
            append=True,
            reason=f"bm_cli append {parsed.args[0]}",
        )
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)

    data = {"cwd": context.cwd, "path": outcome.virtual_path, "chars": outcome.chars}
    if outcome.commit_sha:
        data["git_commit"] = outcome.commit_sha
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} appended {outcome.virtual_path}",
        kind="append",
        data=data,
        sections=[
            ("APPEND RESULT", [
                f"path: {outcome.virtual_path}",
                f"chars: {outcome.chars}",
                "preview:",
                trim(normalize_write_content(content), limit=600),
            ])
        ],
        cwd=context.cwd,
        authoritative_note="The file append succeeded inside the bounded BossMod artifact area.",
    )


def handle_batch_write(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Explain that batch-write is handled by the runtime-managed authoring path."""
    if parsed.args:
        return error_result(parsed.raw, '"bwrite" does not take path arguments. Put the file manifest in the body.', cwd=context.cwd)
    return error_result(
        parsed.raw,
        'Bwrite is runtime-managed. Provide a short manifest body and let the runtime author each file. Use "learn bwrite" for the manifest format.',
        cwd=context.cwd,
    )


def handle_replace_section(
    context: CliExecutionContext,
    parsed: ParsedCliCommand,
    content: str | None = None,
) -> BossModCliResult:
    """Replace one markdown section body with literal body text."""
    if len(parsed.args) != 2:
        return error_result(
            parsed.raw,
            '"repsect" requires a path and a quoted heading selector.',
            cwd=context.cwd,
        )
    if content is None:
        return error_result(
            parsed.raw,
            'Repsect requires a body containing the literal new section text.',
            cwd=context.cwd,
        )

    target, body, result = _read_virtual_file(context, parsed, raw_path=parsed.args[0], command_name="repsect")
    if result is not None:
        return result
    assert target is not None
    document = parse_markdown_document(body)
    if not document.sections:
        return error_result(
            parsed.raw,
            f"No markdown headings were found in {target.virtual_path}.",
            cwd=context.cwd,
        )
    try:
        section = find_markdown_section(document, parsed.args[1])
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)

    updated_text = replace_markdown_section_body(document, section, content)
    try:
        outcome = write_virtual_text(
            context.agent,
            cwd=context.cwd,
            raw_path=parsed.args[0],
            content=updated_text,
            reason=f"bm_cli repsect {target.virtual_path} {section.display_heading}",
            allow_empty=True,
        )
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)

    data = {
        "cwd": context.cwd,
        "path": outcome.virtual_path,
        "heading": section.display_heading,
        "line": section.heading_line,
        "chars": outcome.chars,
    }
    if outcome.commit_sha:
        data["git_commit"] = outcome.commit_sha
    preview = trim(content, limit=600) if content.strip() else "(section cleared)"
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} replaced {section.display_heading} in {outcome.virtual_path}",
        kind="replace-section",
        data=data,
        sections=[
            (
                "SECTION REPLACED",
                [
                    f"path: {outcome.virtual_path}",
                    f"section: {describe_markdown_section(section)}",
                    f"chars: {outcome.chars}",
                    "preview:",
                    preview,
                ],
            )
        ],
        cwd=context.cwd,
        authoritative_note="Only the targeted markdown section body was replaced.",
    )


def handle_rewrite_section(
    context: CliExecutionContext,
    parsed: ParsedCliCommand,
    content: str | None = None,
) -> BossModCliResult:
    """Explain that rewrite-section is handled by the runtime-managed edit path."""
    if len(parsed.args) != 2:
        return error_result(
            parsed.raw,
            '"rewsect" requires a path and a quoted heading selector.',
            cwd=context.cwd,
        )
    if content is None or not content.strip():
        return error_result(
            parsed.raw,
            'Rewsect is runtime-managed. Provide a short rewrite goal in the body. Use "learn rewsect" for the format.',
            cwd=context.cwd,
        )
    return error_result(
        parsed.raw,
        'Rewsect is runtime-managed. Provide a short rewrite goal in the body and let the runtime rewrite only that section. Use "learn rewsect" for the format.',
        cwd=context.cwd,
    )


def normalize_write_content(content: str) -> str:
    """Apply the bounded CLI's normalized text-file write convention."""
    return content.rstrip() + "\n"


def write_virtual_text(
    agent,
    *,
    cwd: str,
    raw_path: str,
    content: str,
    append: bool = False,
    auto_commit: bool = True,
    reason: str | None = None,
    allow_empty: bool = False,
) -> FileWriteOutcome:
    """Write or append one bounded text file and optionally commit it."""
    if content is None or (not allow_empty and not content.strip()):
        raise ValueError('Write commands require a non-empty "content" field.')

    content_bytes = len(content.encode("utf-8"))
    limit = _max_write_bytes()
    if content_bytes > limit:
        raise ValueError(f"Content exceeds maximum write size ({content_bytes:,} bytes > {limit:,} byte limit).")

    target = resolve_cli_path(agent.storage_key, cwd, raw_path)
    if target.real_path is None:
        if append:
            raise ValueError("Cannot append to the virtual root.")
        raise ValueError("Cannot write the virtual root.")
    if target.exists and target.real_path.is_dir():
        if append:
            raise ValueError(f"Cannot append directory: {raw_path}")
        raise ValueError(f"Cannot write directory: {raw_path}")

    target.real_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_write_content(content)
    if append:
        with target.real_path.open("a", encoding="utf-8") as handle:
            handle.write(normalized)
    else:
        target.real_path.write_text(normalized, encoding="utf-8")

    commit_sha = None
    if auto_commit:
        commit_reason = reason or f"bm_cli {'append' if append else 'write'} {target.virtual_path}"
        commit_sha = auto_commit_workspace_change(agent, target.virtual_path, reason=commit_reason)
    return FileWriteOutcome(
        virtual_path=target.virtual_path,
        chars=len(normalized),
        commit_sha=commit_sha,
    )


def _read_virtual_file(
    context: CliExecutionContext,
    parsed: ParsedCliCommand,
    *,
    raw_path: str,
    command_name: str,
) -> tuple[object | None, str, BossModCliResult | None]:
    """Resolve and read one virtual file, returning an early error result when needed."""
    target = resolve_cli_path(context.agent.storage_key, context.cwd, raw_path)
    if not target.exists or target.real_path is None:
        return None, "", error_result(parsed.raw, f"File not found: {raw_path}", cwd=context.cwd)
    if target.real_path.is_dir():
        return None, "", error_result(parsed.raw, f'Cannot use "{command_name}" on a directory: {raw_path}', cwd=context.cwd)
    return target, target.real_path.read_text(encoding="utf-8", errors="replace"), None


def _parse_line_range(raw_range: str) -> tuple[int, int]:
    """Parse a 1-based inclusive line range like 10:25."""
    match = _LINE_RANGE_RE.match(raw_range.strip())
    if match is None:
        raise ValueError('Line range must use "start:end" with positive integers.')
    start_line = int(match.group(1))
    end_line = int(match.group(2))
    if start_line < 1 or end_line < 1:
        raise ValueError("Line numbers must be 1 or greater.")
    if end_line < start_line:
        raise ValueError("Line range end must be greater than or equal to the start.")
    return start_line, end_line
