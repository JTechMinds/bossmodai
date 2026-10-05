"""BossMod AI — Expand message attachments into model content parts.

This is the one seam where attachments become model input. It runs inside
``core.llm.client.completion`` because that is the only place the routed
model is known: context is built before model routing, and one turn can call
more than one model.

Everywhere else a message stays text-only and names its files by id under
the private :data:`ATTACHMENT_IDS_KEY`. Context snapshots, previews, token
counts and logs therefore never hold file bytes.

Only the triggering message carries the key (the context builder sets it);
earlier messages show a one-line manifest in their text instead, so images
are not resent on every turn. The agent can still open any file by its
``/projects/.attachments/...`` path, and a history manifest naming a
model-viewable image says to run ``view <path>`` to see it again (the
trigger's manifest does not: its images are attached).

What each attachment becomes:

- image in a format models accept (``MODEL_IMAGE_MIME_TYPES``: PNG, JPEG,
  GIF, WebP), and the model is flagged image-capable → an ``image_url`` part
  with a base64 data URL;
- image in any other format (SVG, TIFF, BMP) → a text reference line with
  its path, whatever the model;
- image, and the model is not flagged → an explicit text notice that the
  model cannot view it and should not try the CLI (which cannot show it
  an image either), with its path (and a warning log);
- text at or under ``bossmod.attach.inline_text_max_chars`` → a fenced text
  part labelled with the file name;
- longer text, non-UTF-8 text, documents and anything else → a text
  reference line with the path.

CLI result images travel the same way under the private
:data:`CLI_IMAGE_PATHS_KEY`, a list of image file paths on a CLI result
message. Any CLI command, core or extension, names them through
``BossModCliResult.image_paths``; the core ``view <path>`` command is one
such command. Only the LAST message carrying the key is expanded, so
context does not grow by one image per command:

- newest image, model flagged image-capable, file present → an
  ``image_url`` part whose MIME type comes from the file's extension;
- newest image, model not flagged → an explicit text notice that the
  model cannot view it (and a warning log);
- newest image, file missing → :data:`IMAGE_MISSING_TEXT` (and a warning
  log). The command that produced it may have deleted it since, so a
  resumed transcript naming it is told it is gone rather than failing;
- any earlier carrier → :data:`IMAGE_SUPERSEDED_TEXT`, and when it has a
  one-line summary under the private :data:`SUMMARY_KEY`, its text is
  replaced by that summary (still inside the CLI result delimiters), so the
  context does not grow by a full result per command. Each new image
  therefore changes exactly one earlier message.

Parts use the OpenAI chat content format, which litellm translates for each
provider.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from core import config
from core.attachments import MODEL_IMAGE_MIME_TYPES, detect_mime_type, virtual_path
from core.bm_cli.results import wrap_cli_text
from core.models import Attachment
from db import attachments as db_att
from db.model_capabilities import supports_images

logger = logging.getLogger(__name__)

# Private message key naming a message's attachment ids. Never sent to a model.
ATTACHMENT_IDS_KEY = "bm_attachment_ids"
# Private message key naming a CLI result's image files. Never sent to a model.
CLI_IMAGE_PATHS_KEY = "bm_cli_image_paths"
# Private message key: an image carrier's one-line stand-in text for when it
# is superseded. Never sent to a model.
SUMMARY_KEY = "bm_cli_summary"
IMAGE_SUPERSEDED_TEXT = "[image not resent — superseded by a newer image]"
IMAGE_MISSING_TEXT = "[image unavailable: its file no longer exists]"
# Appended to a history manifest naming a model-viewable image.
_VIEW_HINT = " — run `view <path>` to see an image again"
_PRIVATE_KEYS = frozenset({ATTACHMENT_IDS_KEY, CLI_IMAGE_PATHS_KEY, SUMMARY_KEY})


class AttachmentUnavailableError(Exception):
    """A message names an attachment whose row or file is missing.

    That is an integrity failure (a linked file was removed or never
    stored), so the turn fails loudly instead of the model silently seeing
    less than the operator sent.
    """


def expand_attachment_messages(
    messages: list[dict[str, Any]],
    *,
    model: str,
) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` ready for the model.

    Every message loses :data:`ATTACHMENT_IDS_KEY`,
    :data:`CLI_IMAGE_PATHS_KEY` and :data:`SUMMARY_KEY`. A message that
    carried ids or paths has its ``content`` replaced by content parts: its
    text first, then one part per attachment in the order the ids were
    given, then its CLI image parts (see the module doc: only the last
    carrier gets images, and an earlier carrier's text is its wrapped summary
    when it has one). The input list and its dicts are not modified.

    Args:
        messages: Chat messages; ``content`` is text on every message that
            carries attachment ids or CLI image paths.
        model: The RAW model string (before any provider prefix), which is
            how image support is keyed in ``model_capabilities``.

    Returns:
        A new list of new message dicts.

    Raises:
        AttachmentUnavailableError: A named attachment has no row or no file.
        core.config.ConfigError: ``bossmod.attach.inline_text_max_chars`` is
            missing or not an integer (read only when some message has ids).
        ValueError: The newest carrier names a CLI image whose extension is
            not a model image type (``MODEL_IMAGE_MIME_TYPES``).
    """
    expanded: list[dict[str, Any]] = []
    inline_cap: int | None = None
    vision: bool | None = None
    carriers = [index for index, message in enumerate(messages) if message.get(CLI_IMAGE_PATHS_KEY)]
    newest_images = carriers[-1] if carriers else None
    for index, message in enumerate(messages):
        copy = {key: value for key, value in message.items() if key not in _PRIVATE_KEYS}
        ids = message.get(ATTACHMENT_IDS_KEY)
        images = message.get(CLI_IMAGE_PATHS_KEY)
        if not ids and not images:
            expanded.append(copy)
            continue
        if vision is None:
            vision = supports_images(model)
        text = str(copy.get("content", ""))
        summary = message.get(SUMMARY_KEY)
        if images and index != newest_images and summary is not None:
            text = wrap_cli_text(str(summary))
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if ids:
            if inline_cap is None:
                inline_cap = config.require_int("bossmod.attach.inline_text_max_chars")
            for attachment_id in ids:
                parts.append(_attachment_part(_load(attachment_id), model=model, vision=vision, cap=inline_cap))
        if images:
            if index == newest_images:
                parts.extend(_cli_image_part(str(path), model=model, vision=vision) for path in images)
            else:
                parts.append({"type": "text", "text": IMAGE_SUPERSEDED_TEXT})
        copy["content"] = parts
        expanded.append(copy)
    return expanded


def mark_trigger_attachments(
    message: dict[str, Any],
    attachment_ids: list[str] | None,
) -> dict[str, Any]:
    """Mark the trigger message with its attachments for :func:`expand_attachment_messages`.

    Args:
        message: The trigger message; ``content`` is text.
        attachment_ids: The trigger payload's ``attachment_ids``; None or
            empty when the triggering message had no files.

    Returns:
        ``message`` unchanged when there are no ids; otherwise a copy whose
        text gains the manifest line and which names the ids under
        :data:`ATTACHMENT_IDS_KEY`.

    Raises:
        AttachmentUnavailableError: An id has no row, or its file is missing.
    """
    ids = [str(item) for item in (attachment_ids or [])]
    if not ids:
        return message
    manifest = format_attachment_manifest([_load(attachment_id) for attachment_id in ids])
    return {**message, "content": f"{message['content']}\n{manifest}", ATTACHMENT_IDS_KEY: ids}


def history_manifests(history: list[dict[str, Any]]) -> dict[str, str]:
    """Manifest lines for the history entries that had attachments.

    One batched lookup. Entries without an ``id`` (synthetic ones such as
    fade summaries) and messages without files are simply absent. A line
    naming at least one model-viewable image (image tier, a type in
    ``MODEL_IMAGE_MIME_TYPES``) ends with the ``view`` hint, since history
    images are not resent and ``view`` is how the agent sees one again.

    Args:
        history: Prompt history entries, each normally carrying its message ``id``.

    Returns:
        ``{message_id: manifest line}`` for messages with attachments only.
    """
    ids = [str(entry["id"]) for entry in history if entry.get("id")]
    by_message = db_att.get_attachments_for_messages(ids)
    return {
        message_id: format_attachment_manifest(atts) + (_VIEW_HINT if any(map(_model_viewable, atts)) else "")
        for message_id, atts in by_message.items()
        if atts
    }


def attachment_route_line(attachment_ids: list[str]) -> str:
    """One line telling the thread router that files came with the message.

    Names and tiers only, no paths: the router decides who speaks, and only
    needs to know a message is not empty just because its text is.

    Args:
        attachment_ids: The source message's attachment ids, in send order.

    Returns:
        e.g. ``Attachments: shot.png (image), spec.pdf (document)``.

    Raises:
        AttachmentUnavailableError: An id names no attachment row.
    """
    names = []
    for attachment_id in attachment_ids:
        att = db_att.get_attachment_by_id(attachment_id)
        if att is None:
            raise AttachmentUnavailableError(f"Attachment {attachment_id} no longer exists")
        names.append(f"{att.file_name} ({att.preview_tier})")
    return "Attachments: " + ", ".join(names)


def format_attachment_manifest(atts: list[Attachment]) -> str:
    """One text line naming each attachment, its tier, size and CLI path.

    Example: ``Attachments: a.png (image, 2.1 KB) at /projects/.attachments/direct/<id>/<file>``.
    This is what history messages carry instead of their files (see module doc).
    """
    entries = [
        f"{att.file_name} ({att.preview_tier}, {_human_size(att.file_size)}) at {_path(att)}"
        for att in atts
    ]
    return "Attachments: " + "; ".join(entries)


def _model_viewable(att: Attachment) -> bool:
    return att.preview_tier == "image" and att.mime_type in MODEL_IMAGE_MIME_TYPES


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _load(attachment_id: str) -> Attachment:
    att = db_att.get_attachment_by_id(attachment_id)
    if att is None:
        raise AttachmentUnavailableError(f"Attachment {attachment_id} no longer exists")
    if not Path(att.storage_path).is_file():
        raise AttachmentUnavailableError(
            f"Attachment {att.file_name} ({att.id}) has no file on disk"
        )
    return att


def _attachment_part(att: Attachment, *, model: str, vision: bool, cap: int) -> dict[str, Any]:
    if att.preview_tier == "image":
        if att.mime_type not in MODEL_IMAGE_MIME_TYPES:
            return _reference_part(att, "its format is not one models accept as an image")
        if vision:
            return _image_part(Path(att.storage_path), att.mime_type)
        logger.warning(
            "Model %s is not marked image-capable; image %s sent as a notice", model, att.id,
        )
        return {
            "type": "text",
            "text": (
                f"[Image {att.file_name} attached, but your model cannot view images. "
                "Don't try to open it with the CLI; tell the operator you can't see it. "
                f"It is saved at {_path(att)} if you need to move or reference the file.]"
            ),
        }
    if att.preview_tier == "text":
        return _text_part(att, cap)
    return _reference_part(att, "it is not inlined")


def _image_part(path: Path, mime_type: str) -> dict[str, Any]:
    """An ``image_url`` part carrying the file as a base64 data URL."""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
    }


def _cli_image_part(path: str, *, model: str, vision: bool) -> dict[str, Any]:
    """The newest CLI result image as an image part, or the notice saying why not.

    Args:
        path: Real path of the image file a CLI result named.
        model: Raw model string, for the warning log.
        vision: Whether the model is flagged image-capable.

    Returns:
        An ``image_url`` part, or a text notice when the model cannot view
        images or the file no longer exists.

    Raises:
        ValueError: The file's extension maps to a type outside
            ``MODEL_IMAGE_MIME_TYPES``. Commands that name images (``view``)
            refuse such files first, so this is an integrity failure.
    """
    mime_type = detect_mime_type(Path(path).suffix)
    if mime_type not in MODEL_IMAGE_MIME_TYPES:
        raise ValueError(f"CLI result image {path} has type {mime_type}, which models do not accept")
    if not vision:
        logger.warning("Model %s is not marked image-capable; CLI image %s sent as a notice", model, path)
        return {
            "type": "text",
            "text": (
                "[An image was loaded, but your model cannot view images. "
                "Tell the operator you can't see it.]"
            ),
        }
    file = Path(path)
    if not file.is_file():
        logger.warning("CLI image %s is missing; sent as a notice", path)
        return {"type": "text", "text": IMAGE_MISSING_TEXT}
    return _image_part(file, mime_type)


def _text_part(att: Attachment, cap: int) -> dict[str, Any]:
    """The file's text in a fenced block, or a reference when it cannot inline."""
    try:
        text = Path(att.storage_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("Attachment %s is not UTF-8 text; sent as a reference", att.id)
        return _reference_part(att, "it is not UTF-8 text, so it is not inlined")
    if len(text) > cap:
        return _reference_part(att, f"it is longer than the {cap}-character inline limit")
    fence = "```"
    # A fence inside the file would close ours early; out-fence it.
    while fence in text:
        fence += "`"
    return {
        "type": "text",
        "text": f"Attached file {att.file_name}:\n{fence}\n{text}\n{fence}",
    }


def _reference_part(att: Attachment, reason: str) -> dict[str, Any]:
    """A text line naming the file and where the agent can open it."""
    return {
        "type": "text",
        "text": (
            f"[File {att.file_name} attached ({att.mime_type}, {att.file_size} bytes): "
            f"{reason}. It is saved at {_path(att)}.]"
        ),
    }


def _path(att: Attachment) -> str:
    return virtual_path(att.context_type, att.context_id, Path(att.storage_path).name)
