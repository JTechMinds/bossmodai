"""BossMod AI — Filesystem-style virtual BossMod CLI commands."""

from __future__ import annotations

from pathlib import Path

from core import config
from core.bm_cli.results import error_result, success_result, trim
from core.bm_cli.session import set_cli_cwd
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path, virtual_root_entries
from core.bm_cli.workspace_git import auto_commit_workspace_change

def _max_write_bytes() -> int:
    return config.get_int("cli_max_write_bytes")


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
    content_bytes = len(content.encode("utf-8"))
    limit = _max_write_bytes()
    if content_bytes > limit:
        return error_result(
            parsed.raw,
            f"Content exceeds maximum write size ({content_bytes:,} bytes > {limit:,} byte limit).",
            cwd=context.cwd,
        )
    target = resolve_cli_path(context.agent.storage_key, context.cwd, parsed.args[0])
    if target.real_path is None:
        return error_result(parsed.raw, "Cannot write the virtual root.", cwd=context.cwd)
    if target.exists and target.real_path.is_dir():
        return error_result(parsed.raw, f"Cannot write directory: {parsed.args[0]}", cwd=context.cwd)
    target.real_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.rstrip() + "\n"
    target.real_path.write_text(normalized, encoding="utf-8")
    commit_sha = auto_commit_workspace_change(
        context.agent,
        target.virtual_path,
        reason=f"bm_cli write {target.virtual_path}",
    )
    data = {"cwd": context.cwd, "path": target.virtual_path, "chars": len(normalized)}
    if commit_sha:
        data["git_commit"] = commit_sha
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} wrote {target.virtual_path}",
        kind="write",
        data=data,
        sections=[
            ("WRITE RESULT", [
                f"path: {target.virtual_path}",
                f"chars: {len(normalized)}",
                "preview:",
                trim(normalized, limit=600),
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
    content_bytes = len(content.encode("utf-8"))
    limit = _max_write_bytes()
    if content_bytes > limit:
        return error_result(
            parsed.raw,
            f"Content exceeds maximum write size ({content_bytes:,} bytes > {limit:,} byte limit).",
            cwd=context.cwd,
        )
    target = resolve_cli_path(context.agent.storage_key, context.cwd, parsed.args[0])
    if target.real_path is None:
        return error_result(parsed.raw, "Cannot append to the virtual root.", cwd=context.cwd)
    if target.exists and target.real_path.is_dir():
        return error_result(parsed.raw, f"Cannot append directory: {parsed.args[0]}", cwd=context.cwd)
    target.real_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.rstrip() + "\n"
    with target.real_path.open("a", encoding="utf-8") as handle:
        handle.write(normalized)
    commit_sha = auto_commit_workspace_change(
        context.agent,
        target.virtual_path,
        reason=f"bm_cli append {target.virtual_path}",
    )
    data = {"cwd": context.cwd, "path": target.virtual_path, "chars": len(normalized)}
    if commit_sha:
        data["git_commit"] = commit_sha
    return success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} appended {target.virtual_path}",
        kind="append",
        data=data,
        sections=[
            ("APPEND RESULT", [
                f"path: {target.virtual_path}",
                f"chars: {len(normalized)}",
                "preview:",
                trim(normalized, limit=600),
            ])
        ],
        cwd=context.cwd,
        authoritative_note="The file append succeeded inside the bounded BossMod artifact area.",
    )
