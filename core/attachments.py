"""BossMod AI — Attachment core logic (pure, no I/O).

Provides storage path derivation, file name sanitization, preview tier
detection, MIME type detection, and blocklist enforcement.
"""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Literal

# The two real conversation kinds an upload can belong to: an agent DM and a
# shared thread (channel).
ContextType = Literal["direct", "thread"]
_CONTEXT_TYPES: frozenset[str] = frozenset({"direct", "thread"})

# Dot-prefixed so floor tooling that skips hidden entries (floor moves,
# listings) leaves it alone, like ``.archived-floors``.
ATTACHMENTS_DIRNAME = ".attachments"

# ---------------------------------------------------------------------------
# Blocklist
# ---------------------------------------------------------------------------

BLOCKLIST: frozenset[str] = frozenset({
    ".exe", ".msi", ".bat", ".cmd", ".sh", ".ps1",
    ".app", ".dmg", ".deb", ".rpm", ".apk",
})

# ---------------------------------------------------------------------------
# Preview tier extension maps
# ---------------------------------------------------------------------------

_IMAGE_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg",
})

_TEXT_EXTS: frozenset[str] = frozenset({
    ".txt", ".log", ".md", ".json", ".yaml", ".yml", ".csv",
    ".py", ".js", ".ts", ".html", ".css", ".xml", ".toml", ".ini",
    ".cfg", ".conf", ".env",
})

_DOCUMENT_EXTS: frozenset[str] = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf",
})

# The image formats providers broadly accept as model image input. Other
# image-tier files (SVG, TIFF, BMP) still preview in the UI but reach the
# model as a path reference, since most providers reject them outright.
MODEL_IMAGE_MIME_TYPES: frozenset[str] = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})

# ---------------------------------------------------------------------------
# MIME overrides (extensions where mimetypes stdlib is unreliable)
# ---------------------------------------------------------------------------

_MIME_OVERRIDES: dict[str, str] = {
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".tiff": "image/tiff",
    ".md": "text/markdown",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".toml": "application/toml",
    ".csv": "text/csv",
    ".json": "application/json",
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_NAME_LEN = 255


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def storage_dir(floor_root: Path, context_type: ContextType, context_id: str) -> Path:
    """Return the directory that holds one conversation's attachments.

    Layout: ``<floor_root>/.attachments/<context_type>/<context_id>``. The
    dot folder sits inside the floor, which is the agent's ``/projects``, so
    the files are readable through the agent CLI at :func:`virtual_path`.

    Args:
        floor_root: The conversation's floor folder (``floor_root(floor_id)``).
        context_type: ``"direct"`` for an agent DM, ``"thread"`` for a channel.
        context_id: The agent id or channel id. Callers must have matched it
            to a real row first: it becomes a path component here.

    Returns:
        The directory path. It is not created.

    Raises:
        ValueError: ``context_type`` is not a known conversation kind.
    """
    if context_type not in _CONTEXT_TYPES:
        raise ValueError(f"Invalid context_type: {context_type!r}")
    return floor_root / ATTACHMENTS_DIRNAME / context_type / context_id


def virtual_path(context_type: ContextType, context_id: str, disk_name: str) -> str:
    """Return the agent-visible CLI path of one stored attachment.

    Mirrors :func:`storage_dir` under the agent's ``/projects`` mount.

    Args:
        context_type: ``"direct"`` or ``"thread"``.
        context_id: The agent id or channel id.
        disk_name: The stored file name (``<uuid>_<safe_name>``).

    Returns:
        ``/projects/.attachments/<context_type>/<context_id>/<disk_name>``.

    Raises:
        ValueError: ``context_type`` is not a known conversation kind.
    """
    if context_type not in _CONTEXT_TYPES:
        raise ValueError(f"Invalid context_type: {context_type!r}")
    return f"/projects/{ATTACHMENTS_DIRNAME}/{context_type}/{context_id}/{disk_name}"


def sanitize_file_name(name: str) -> str:
    """Strip path separators, null bytes, colons, and truncate to 255 chars.

    - Replaces '/' and '\\' with '_'
    - Replaces ':' with '_' (Windows drive letters)
    - Removes null bytes
    - Truncates to MAX_FILE_NAME_LEN
    - If result is empty, returns 'unnamed'
    """
    # Remove null bytes
    name = name.replace("\x00", "")
    # Replace path separators and colons
    name = name.replace("/", "_").replace("\\", "_").replace(":", "_")
    # Collapse multiple underscores from traversal patterns
    name = re.sub(r"_+", "_", name)
    # Strip leading/trailing dots and underscores
    name = name.strip("._")
    # Truncate
    if len(name) > MAX_FILE_NAME_LEN:
        name = name[:MAX_FILE_NAME_LEN]
    if not name:
        name = "unnamed"
    return name


def detect_preview_tier(extension: str) -> str:
    """Return 'image', 'text', 'document', or 'other' based on extension.

    Extension should include the leading dot (e.g. '.png').
    """
    ext = extension.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _TEXT_EXTS:
        return "text"
    if ext in _DOCUMENT_EXTS:
        return "document"
    return "other"


def detect_mime_type(extension: str) -> str:
    """Map file extension to MIME type.

    Uses a hand-rolled override map first, then falls back to mimetypes stdlib.
    """
    ext = extension.lower()
    if ext in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[ext]
    mime, _ = mimetypes.guess_type(f"file{ext}")
    return mime or "application/octet-stream"


def is_blocklisted(extension: str) -> bool:
    """Return True if the file extension is in the blocklist."""
    return extension.lower() in BLOCKLIST


def get_file_extension(filename: str) -> str:
    """Extract the lowercased extension (including dot) from a filename."""
    _, ext = os.path.splitext(filename)
    return ext.lower()
