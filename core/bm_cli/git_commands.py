"""BossMod AI — Safe Git history commands for per-agent workspaces."""

from __future__ import annotations

from core.bm_cli.results import error_result, success_result, trim
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.bm_cli.workspace_git import (
    build_git_diff,
    build_git_log,
    build_git_show,
    build_git_status,
    repo_relative_path_for_virtual_path,
    restore_git_revision,
)


def handle_git(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Handle a bounded subset of Git commands inside the agent workspace repo."""
    if not parsed.args:
        return error_result(parsed.raw, 'Supported git commands: status, log, diff, show, restore.', cwd=context.cwd)

    subcommand = parsed.args[0]
    args = parsed.args[1:]
    try:
        if subcommand == "status":
            path = _optional_repo_relative_path(context, args[0]) if args else None
            output = build_git_status(context.agent, relative_path=path) or "Workspace is clean."
            return success_result(
                command=parsed.raw,
                detail=f"{context.agent.name} checked workspace Git status",
                kind="git_status",
                data={"cwd": context.cwd, "output": output, "path": path},
                sections=[("GIT STATUS", [trim(output, limit=4000)])],
                cwd=context.cwd,
            )

        if subcommand == "log":
            limit = 10
            if args:
                try:
                    limit = int(args[0])
                except ValueError as exc:
                    raise ValueError('git log accepts an optional numeric limit.') from exc
            output = build_git_log(context.agent, limit=limit) or "No commits yet."
            return success_result(
                command=parsed.raw,
                detail=f"{context.agent.name} checked workspace Git history",
                kind="git_log",
                data={"cwd": context.cwd, "output": output, "limit": limit},
                sections=[("GIT LOG", [trim(output, limit=4000)])],
                cwd=context.cwd,
            )

        if subcommand == "diff":
            path = _optional_repo_relative_path(context, args[0]) if args else None
            output = build_git_diff(context.agent, relative_path=path) or "No working-tree diff."
            return success_result(
                command=parsed.raw,
                detail=f"{context.agent.name} checked workspace Git diff",
                kind="git_diff",
                data={"cwd": context.cwd, "output": output, "path": path},
                sections=[("GIT DIFF", [trim(output, limit=4000)])],
                cwd=context.cwd,
            )

        if subcommand == "show":
            if not args:
                raise ValueError('git show requires a revision, optionally followed by a /me path.')
            revision = args[0]
            path = _optional_repo_relative_path(context, args[1]) if len(args) > 1 else None
            output = build_git_show(context.agent, revision, relative_path=path)
            title = f"GIT SHOW {revision}" if path is None else f"GIT SHOW {revision}:{path}"
            return success_result(
                command=parsed.raw,
                detail=f"{context.agent.name} inspected workspace Git revision {revision}",
                kind="git_show",
                data={"cwd": context.cwd, "output": output, "revision": revision, "path": path},
                sections=[(title, [trim(output, limit=4000)])],
                cwd=context.cwd,
            )

        if subcommand == "restore":
            revision = None
            path_arg = None
            index = 0
            while index < len(args):
                token = args[index]
                if token == "--source":
                    if index + 1 >= len(args):
                        raise ValueError('git restore --source requires a revision.')
                    revision = args[index + 1]
                    index += 2
                    continue
                if path_arg is not None:
                    raise ValueError('git restore accepts exactly one /me path.')
                path_arg = token
                index += 1
            if path_arg is None:
                raise ValueError('git restore requires a /me path.')
            relative_path = repo_relative_path_for_virtual_path(context.agent, context.cwd, path_arg)
            commit_sha = restore_git_revision(
                context.agent,
                revision=revision,
                relative_path=relative_path,
                reason=f"bm_cli git restore {path_arg}" if revision is None else f"bm_cli git restore --source {revision} {path_arg}",
            )
            restored_from = revision or "HEAD"
            lines = [
                f"path: {path_arg}",
                f"restored_from: {restored_from}",
            ]
            if commit_sha:
                lines.append(f"commit: {commit_sha}")
            else:
                lines.append("commit: none (workspace already matched requested revision)")
            return success_result(
                command=parsed.raw,
                detail=f"{context.agent.name} restored {path_arg} from workspace Git history",
                kind="git_restore",
                data={
                    "cwd": context.cwd,
                    "path": path_arg,
                    "revision": restored_from,
                    "commit": commit_sha,
                },
                sections=[("GIT RESTORE", lines)],
                cwd=context.cwd,
                authoritative_note="The requested file now reflects the restored workspace revision.",
            )
    except ValueError as exc:
        return error_result(parsed.raw, str(exc), cwd=context.cwd)

    return error_result(
        parsed.raw,
        'Supported git commands: status, log, diff, show, restore.',
        cwd=context.cwd,
    )


def _optional_repo_relative_path(context: CliExecutionContext, raw_path: str) -> str:
    return repo_relative_path_for_virtual_path(context.agent, context.cwd, raw_path)
