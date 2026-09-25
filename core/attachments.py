"""BossMod AI — Attachment core logic (pure, no I/O).

Provides storage path derivation, file name sanitization, preview tier
detection, MIME type detection, and blocklist enforcement.
"""

from __future__ import annotations

import mimetypes
import os
import re

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

def derive_storage_root(context_type: str, context_id: str, base_dir: str) -> str:
    """Return the absolute directory where attachments for this context live.

    - thread   → {base_dir}/projects/{context_id}/attachments
    - direct   → {base_dir}/agents/{context_id}/attachments
    - channel  → {base_dir}/projects/{project_id}/channels/{channel_id}/attachments
    - unscoped → {base_dir}/shared/attachments

    For channel context, context_id is expected to be "project_id/channel_id".
    """
    if context_type == "thread":
        return os.path.join(base_dir, "projects", context_id, "attachments")
    elif context_type == "direct":
        return os.path.join(base_dir, "agents", context_id, "attachments")
    elif context_type == "channel":
        # context_id format: "project_id/channel_id"
        parts = context_id.split("/", 1)
        if len(parts) == 2:
            project_id, channel_id = parts
            return os.path.join(base_dir, "projects", project_id, "channels", channel_id, "attachments")
        return os.path.join(base_dir, "projects", context_id, "attachments")
    elif context_type == "unscoped":
        return os.path.join(base_dir, "shared", "attachments")
    else:
        raise ValueError(f"Invalid context_type: {context_type!r}")


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
