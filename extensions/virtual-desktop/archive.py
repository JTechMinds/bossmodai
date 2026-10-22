"""Virtual Desktop — the tar streams that carry ``vm upload`` and ``vm download``.

An upload is packed so everything lands owned by the desktop's ``agent``
account. A download comes from a desktop the agent fully controls, so it is
untrusted: it is checked member by member before anything is written, only
regular files and folders are written, and never over an existing name.
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path
from typing import IO

from .desktop_types import USER

# The desktop's `agent` account (image/Dockerfile).
AGENT_UID = 1000


class UnsafeArchive(Exception):
    """A downloaded tar holds something that must not be written; the message names it."""


def pack_for_agent(fileobj: IO[bytes], source: Path, name: str) -> tuple[int, int]:
    """Write ``source`` (a file or folder) as a tar named ``name``, every member owned by ``agent``.

    Symbolic links inside a folder are stored as links, never followed, so
    nothing outside ``source`` is read.

    Returns:
        ``(regular files, their bytes)``.

    Raises:
        OSError: ``source`` cannot be read.
    """
    counts = [0, 0]

    def owned_by_agent(member: tarfile.TarInfo) -> tarfile.TarInfo:
        member.uid = member.gid = AGENT_UID
        member.uname = member.gname = USER
        if member.isfile():
            counts[0] += 1
            counts[1] += member.size
        return member

    with tarfile.open(fileobj=fileobj, mode="w") as tar:
        tar.add(source, arcname=name, filter=owned_by_agent)
    return counts[0], counts[1]


def checked_members(members: list[tarfile.TarInfo]) -> list[tarfile.TarInfo]:
    """Accept only one top-level file or folder made of regular files and folders.

    Raises:
        UnsafeArchive: Naming the first offending member: an absolute name,
            a ``..`` part, a symbolic or hard link, a device or other special
            file, a second top-level name, a repeated name; or no members.
    """
    if not members:
        raise UnsafeArchive("the copy from the desktop was empty")
    top: str | None = None
    seen: set[tuple[str, ...]] = set()
    for member in members:
        name = member.name
        parts = _parts(name)
        if name.startswith("/") or not parts:
            raise UnsafeArchive(f"{name!r} is not a relative name")
        if ".." in parts:
            raise UnsafeArchive(f"{name} leaves its folder (..)")
        if member.issym():
            raise UnsafeArchive(f"{name} is a symbolic link; only regular files and folders can be copied")
        if member.islnk():
            raise UnsafeArchive(f"{name} is a hard link; only regular files and folders can be copied")
        if not (member.isfile() or member.isdir()):
            raise UnsafeArchive(f"{name} is a device or special file; only regular files and folders can be copied")
        if top is None:
            top = parts[0]
        elif parts[0] != top:
            raise UnsafeArchive(f"{name} is outside {top}")
        if parts in seen:
            raise UnsafeArchive(f"{name} appears twice")
        seen.add(parts)
    return members


def top_name(members: list[tarfile.TarInfo]) -> str:
    """The one top-level name of members ``checked_members`` accepted."""
    return _parts(members[0].name)[0]


def unique_path(folder: Path, name: str) -> Path:
    """Return ``folder/name``, or ``name (n).ext`` when that already exists (as Browser Vision's downloads)."""
    candidate = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while candidate.exists():
        candidate = folder / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def extract(tar: tarfile.TarFile, members: list[tarfile.TarInfo], target: Path) -> int:
    """Write checked members under ``target``, which takes the place of their top-level name.

    Returns:
        The bytes written.

    Raises:
        OSError: Writing failed; everything written under ``target`` is removed first.
    """
    total = 0
    try:
        for member in members:
            out = target.joinpath(*_parts(member.name)[1:])
            if member.isdir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise OSError(f"{member.name} has no content in the archive")
            # "x": a name that already exists is never replaced.
            with source, out.open("xb") as written:
                shutil.copyfileobj(source, written)
            total += member.size
    except OSError:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        raise
    return total


def _parts(name: str) -> tuple[str, ...]:
    """A member name's path parts, without empty and ``.`` parts."""
    return tuple(part for part in name.split("/") if part not in ("", "."))
