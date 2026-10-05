"""BossMod AI — Virtual CLI command that shows an image file to the agent.

``view <path>`` loads one image for the model: the result names the file
under ``BossModCliResult.image_paths``, and the completion seam
(:mod:`core.llm.attachment_parts`) attaches the newest such image to the
agent's next model call. Loading an image for the model is its own concern,
separate from the text file I/O in :mod:`core.bm_cli.fs_commands`.
"""

from __future__ import annotations

import dataclasses

from core import config
from core.attachments import MODEL_IMAGE_MIME_TYPES, detect_mime_type
from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.bm_cli.virtual_fs import resolve_cli_path

_ACCEPTED_FORMATS = "PNG, JPEG, GIF or WebP"


def handle_view(context: CliExecutionContext, parsed: ParsedCliCommand, content: str | None = None) -> BossModCliResult:
    """Show one image file to the agent on its next model call.

    The path resolves through the same jail as ``cat``
    (:func:`core.bm_cli.virtual_fs.resolve_cli_path`): ``/me``,
    ``/projects`` (thread attachments live under ``/projects/.attachments``)
    and operator-allowed host roots.

    Args:
        context: The calling agent and its working directory.
        parsed: The parsed command; exactly one path argument.
        content: Unused; ``view`` takes no body.

    Returns:
        On success, a ``kind="image"`` result whose ``image_paths`` names the
        file's real path and whose ``summary`` is ``view <virtual path>``.
        An error result when the argument count is wrong, the path does not
        exist or is a directory, the format is not one models accept
        (``MODEL_IMAGE_MIME_TYPES``), or the file is larger than
        ``bossmod.attach.max_size_mb``.

    Raises:
        core.bm_cli.host_roots.PathOutsideRootsError: The path is outside
            every allowed root (the runtime turns it into a denial or a
            host-path consent request, as for ``cat``).
        ValueError: A relative path escapes its root.
        core.config.ConfigError: ``bossmod.attach.max_size_mb`` is missing
            or not an integer.
    """
    if len(parsed.args) != 1:
        return error_result(parsed.raw, '"view" requires exactly one path argument.', cwd=context.cwd)
    arg = parsed.args[0]
    target = resolve_cli_path(context.agent.storage_key, context.cwd, arg)
    if not target.exists or target.real_path is None:
        return error_result(parsed.raw, f"File not found: {arg}", cwd=context.cwd)
    if target.real_path.is_dir():
        return error_result(parsed.raw, f"Cannot view a directory: {arg}", cwd=context.cwd)
    mime_type = detect_mime_type(target.real_path.suffix)
    if mime_type not in MODEL_IMAGE_MIME_TYPES:
        return error_result(
            parsed.raw,
            f"Cannot view {arg}: its type is {mime_type}; view accepts {_ACCEPTED_FORMATS} images.",
            cwd=context.cwd,
        )
    max_mb = config.require_int("bossmod.attach.max_size_mb")
    size = target.real_path.stat().st_size
    if size > max_mb * 1024 * 1024:
        return error_result(
            parsed.raw,
            f"Cannot view {arg}: it is {_human_size(size)}, over the {max_mb} MB image limit.",
            cwd=context.cwd,
        )
    result = success_result(
        command=parsed.raw,
        detail=f"{context.agent.name} viewed {target.virtual_path}",
        kind="image",
        data={"cwd": context.cwd, "path": target.virtual_path, "mime_type": mime_type, "size": size},
        sections=[("IMAGE", [
            f"path: {target.virtual_path}",
            f"type: {mime_type}",
            f"size: {_human_size(size)}",
        ])],
        cwd=context.cwd,
    )
    return dataclasses.replace(
        result,
        image_paths=(str(target.real_path),),
        summary=f"view {target.virtual_path}",
    )


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
