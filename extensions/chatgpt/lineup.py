"""Model lines and versions, read from catalog slugs. Pure: no I/O, no host imports.

A catalog slug such as ``fx-6.2-alpha`` names a line (``fx-alpha``) and a
version (6.2). Lines are read from slugs only, never from display names, which
the catalog spells inconsistently. A slug that does not read as a line (no
version, a dated snapshot, any other shape) is never treated as older than
anything: when unsure, the model stays on.

The operator's on/off choice per slug is decided once, the first time a slug
is seen (``decide``); every later choice is the operator's.
"""
import re
from collections.abc import Collection

# prefix-version[-suffix…]: lowercase letters, a dotted number, then any
# number of word parts that start with a letter. A part that starts with a
# digit (a date) does not match, so a dated snapshot is not a line.
_SLUG = re.compile(r"(?P<prefix>[a-z]+)-(?P<version>\d+(?:\.\d+)*)(?P<suffix>(?:-[a-z][a-z0-9]*)*)")


def model_line(slug: str) -> tuple[str, tuple[int, ...]] | None:
    """The line and version a catalog slug names.

    Args:
        slug: A catalog slug.

    Returns:
        ``(line, version)``: the line is the prefix and suffix
        (``fx-6.2-alpha`` → ``fx-alpha``; a suffix is part of the line, so
        ``fx-6.2-alpha-mini`` is its own line ``fx-alpha-mini``), and the
        version is its numbers with trailing zeros stripped, so ``fx-6-alpha``
        and ``fx-6.0-alpha`` are both ``(6,)``. None when the slug does not
        read as a line.
    """
    match = _SLUG.fullmatch(slug)
    if match is None:
        return None
    version = [int(part) for part in match["version"].split(".")]
    while version and version[-1] == 0:
        version.pop()
    return match["prefix"] + match["suffix"], tuple(version)


def newest_names(models: list[dict]) -> dict[str, str]:
    """Which catalog models a newer version of their own line supersedes.

    Args:
        models: Stored catalog entries, each with ``slug`` and ``display_name``.

    Returns:
        Each slug with a strictly newer same-line slug in ``models`` → the
        display name of that line's newest model. Equal versions supersede
        nothing; a slug that does not read as a line is never listed.
    """
    lines: dict[str, tuple[str, tuple[int, ...]]] = {}
    newest: dict[str, tuple[tuple[int, ...], str]] = {}
    for model in models:
        parsed = model_line(model["slug"])
        if parsed is None:
            continue
        lines[model["slug"]] = parsed
        line, version = parsed
        if line not in newest or version > newest[line][0]:
            newest[line] = (version, model["display_name"])
    return {slug: newest[line][1] for slug, (line, version) in lines.items() if version < newest[line][0]}


def decide(models: list[dict], choices: dict[str, bool], in_use: Collection[str]) -> dict[str, bool]:
    """Decide every slug seen for the first time, and what its arrival turns off.

    A slug with no stored choice is "fresh". Each fresh slug is on unless the
    catalog already holds a newer version of its line. Then each fresh slug
    turns off, once, every older slug of its line that was stored on. A slug
    in ``in_use`` is never turned off either way.

    Args:
        models: The stored catalog entries, each with ``slug`` and
            ``display_name``.
        choices: Slug → on, as stored; never mutated. Choices for slugs that
            have left the catalog are kept, so a model that comes back is not
            treated as new.
        in_use: Slugs whose connections something still uses.

    Returns:
        A new map with a choice for every catalog slug.
    """
    decided = dict(choices)
    newer = newest_names(models)
    fresh = [model["slug"] for model in models if model["slug"] not in choices]
    for slug in fresh:
        decided[slug] = slug not in newer or slug in in_use
    for slug in fresh:
        parsed = model_line(slug)
        if parsed is None:
            continue
        line, version = parsed
        for stored, on in choices.items():
            older = model_line(stored)
            if on and stored not in in_use and older is not None and older[0] == line and older[1] < version:
                decided[stored] = False
    return decided


def has_fresh(models: list[dict], choices: dict[str, bool]) -> bool:
    """Whether any catalog slug has no stored choice yet.

    Lets publication skip reading what is in use when nothing is to be decided.
    """
    return any(model["slug"] not in choices for model in models)
