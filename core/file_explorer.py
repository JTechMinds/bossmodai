"""BossMod AI — Cross-platform file explorer launcher.

Detects the system default file manager and opens directories in a new window.
Designed for dependency injection into API routes — no global state, no hardcoded
settings reads. Callers pass the opener preference; this module handles the rest.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# ─── Registry of known Linux file managers ───
# (binary, display_label, supports_new_window)

_LINUX_FILE_MANAGERS: tuple[tuple[str, str, bool], ...] = (
    ("dolphin", "Dolphin", True),
    ("nautilus", "Nautilus", True),
    ("nemo", "Nemo", True),
    ("thunar", "Thunar", True),
    ("caja", "Caja", True),
    ("pcmanfm", "PCManFM", False),
    ("konqueror", "Konqueror", False),
    ("lxqt-filemanager", "LXQt File Manager", False),
)


def detect_default_file_manager() -> str | None:
    """Return the binary name of the user's default file manager, or None.

    On Linux, queries ``xdg-mime`` for the default ``inode/directory`` handler
    and extracts the executable from the ``.desktop`` file name.
    On macOS/Windows, returns the platform opener directly.
    """
    if sys.platform.startswith("darwin"):
        return "open"
    if sys.platform.startswith("win"):
        return "explorer"

    # Linux: ask xdg-mime for the default inode/directory handler
    if not shutil.which("xdg-mime"):
        return None
    try:
        result = subprocess.run(
            ["xdg-mime", "query", "default", "inode/directory"],
            capture_output=True, text=True, timeout=5,
        )
        desktop_entry = result.stdout.strip()  # e.g. "org.kde.dolphin.desktop"
        if not desktop_entry:
            return None
        # Extract binary: last segment before .desktop, lowercased
        name = desktop_entry.rsplit(".", 2)
        if len(name) >= 2 and name[-1] == "desktop":
            binary = name[-2].lower()
            if shutil.which(binary):
                return binary
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def build_command(path: Path, opener: str) -> list[str]:
    """Build the shell command to open *path* in a file explorer.

    Parameters
    ----------
    path:
        The directory to open.
    opener:
        Either ``"auto"`` (detect + new-window), a specific binary name,
        or ``"system"`` (legacy ``xdg-open`` passthrough).

    Raises
    ------
    OSError
        If no suitable opener can be found.
    """
    opener = opener.strip()
    if not opener or opener == "auto":
        return _build_auto_command(path)
    if opener == "system":
        return _build_system_command(path)
    return _build_explicit_command(path, opener)


def launch(path: Path, opener: str) -> None:
    """Open *path* in the platform file explorer (fire-and-forget)."""
    subprocess.Popen(build_command(path, opener))


def available_options() -> list[dict[str, str]]:
    """Return detected file manager choices for the settings UI."""
    options: list[dict[str, str]] = [
        {
            "value": "auto",
            "label": "Auto-detect (new window)",
            "description": "Detect the default file manager and open in a new window.",
        },
    ]

    if sys.platform.startswith("darwin"):
        return options  # macOS: auto is the only sensible choice
    if sys.platform.startswith("win"):
        return options  # Windows: auto is the only sensible choice

    # Linux: add detected file managers
    for binary, label, _ in _LINUX_FILE_MANAGERS:
        if shutil.which(binary):
            options.append({
                "value": binary,
                "label": label,
                "description": f"Open folders with {label}.",
            })
    if shutil.which("xdg-open"):
        options.append({
            "value": "system",
            "label": "System Default (may reuse tab)",
            "description": "Use xdg-open — may open as a tab in an existing window.",
        })
    return options


# ─── Private builders ───

def _build_auto_command(path: Path) -> list[str]:
    """Auto-detect the file manager and prefer --new-window."""
    if sys.platform.startswith("darwin"):
        return ["open", str(path)]
    if sys.platform.startswith("win"):
        return ["explorer", str(path)]

    binary = detect_default_file_manager()
    if binary:
        return _command_with_new_window(binary, path)

    # Fallback: probe known managers
    for fm_binary, _, _ in _LINUX_FILE_MANAGERS:
        if shutil.which(fm_binary):
            return _command_with_new_window(fm_binary, path)

    if shutil.which("xdg-open"):
        return ["xdg-open", str(path)]

    raise OSError("No file manager found on this system")


def _build_system_command(path: Path) -> list[str]:
    """Legacy xdg-open passthrough (may reuse existing window/tab)."""
    if sys.platform.startswith("darwin"):
        return ["open", str(path)]
    if sys.platform.startswith("win"):
        return ["explorer", str(path)]
    if shutil.which("xdg-open"):
        return ["xdg-open", str(path)]
    raise OSError("System default opener is unavailable on this machine")


def _build_explicit_command(path: Path, binary: str) -> list[str]:
    """Use a specific binary, with --new-window if supported."""
    if sys.platform.startswith("win") and binary.lower() == "explorer":
        return ["explorer", str(path)]
    if sys.platform.startswith("darwin") and binary == "open":
        return ["open", str(path)]
    if shutil.which(binary):
        return _command_with_new_window(binary, path)
    raise OSError(f'Configured folder opener "{binary}" was not found on PATH')


def _command_with_new_window(binary: str, path: Path) -> list[str]:
    """Return the command list, adding --new-window for managers that support it."""
    supports = {b for b, _, nw in _LINUX_FILE_MANAGERS if nw}
    if binary in supports:
        return [binary, "--new-window", str(path)]
    return [binary, str(path)]
