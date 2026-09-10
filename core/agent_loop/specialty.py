"""Pure specialty-family and finish-line inference over plain strings.

This module exists to break an import cycle, and must stay a leaf to keep it
broken. ``core.agent_pack.schema`` needs exactly one thing from the role
contracts — ``suggest_finish_line`` — but ``core.agent_loop.role_contracts``
also reaches ``core.agent_loop.deliverables`` -> ``db`` -> ``core.bm_cli`` ->
``core.tasking.board``, which imports back into ``role_contracts``. Importing
``core.agent_pack`` from a cold interpreter therefore failed unless ``db``
happened to be imported first. Pulling the string-only half down here removes
``core.agent_pack``'s edge into that cycle entirely.

So: **do not merge this back into** ``role_contracts`` **and do not import
anything from** ``db``, ``core.agent_loop``, ``core.tasking`` **or**
``core.bm_cli`` **here.** Everything in this module is a pure function of the
strings passed to it — no ``Agent``, no ``Task``, no database, no filesystem.
Anything needing those belongs in ``role_contracts`` instead.

``role_contracts`` re-exports every public name below, so existing importers
(and ``tests/test_role_contracts.py``) keep working unchanged.
"""

from __future__ import annotations

import re
from typing import Literal

SpecialtyFamily = Literal["write", "review", "implement", "research", "design", "coordinate"]

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_SPECIALTY_TOKENS: dict[SpecialtyFamily, frozenset[str]] = {
    "write": frozenset({
        "writer", "writing", "copy", "copywriter", "docs", "documentation",
        "documenter", "author", "editor", "draft",
    }),
    "review": frozenset({
        "reviewer", "review", "auditor", "audit", "qa", "tester", "test",
        "testing", "inspector", "inspect",
    }),
    "implement": frozenset({
        "engineer", "eng", "developer", "dev", "coder", "programmer",
        "implement", "implementation", "code", "coding", "builder",
    }),
    "research": frozenset({
        "researcher", "research", "analyst", "analysis", "investigate",
    }),
    "design": frozenset({
        "designer", "design", "ux", "ui", "mockup",
    }),
    "coordinate": frozenset({
        "pm", "product", "manager", "lead", "coordinator", "owner", "director",
    }),
}

_WORK_KIND_TOKENS: dict[SpecialtyFamily, frozenset[str]] = {
    "write": frozenset({"write", "writing", "draft", "document", "docs", "copy", "author", "edit", "edits"}),
    "review": frozenset({"review", "audit", "auditing", "qa", "test", "tests", "testing", "inspect"}),
    "implement": frozenset({"implement", "code", "coding", "build", "fix", "debug", "develop"}),
    "research": frozenset({"research", "analyze", "analysis", "investigate"}),
    "design": frozenset({"design", "mockup", "wireframe", "ux"}),
}

# Operator-facing finish-line defaults. Empty done stays blocked by the
# checkable-claim rules even when the stored bar is blank.
_DEFAULT_FINISH_LINES: dict[SpecialtyFamily, str] = {
    "write": "A named draft or document exists. Empty done does not count.",
    "review": "A checkable allow/deny (or tests/artifact) exists. Empty done does not count.",
    "implement": "Tests evidence or a named artifact exists. Empty done does not count.",
    "research": "A named findings note exists. Empty done does not count.",
    "design": "A named mockup or design file exists. Empty done does not count.",
    "coordinate": "A named plan or status note exists. Empty done does not count.",
}
_FALLBACK_FINISH_LINE = (
    "A checkable claim exists (tests, artifact, or allow/deny). Empty done does not count."
)


def tokenize(text: str | None) -> set[str]:
    """Lowercase alphanumeric tokens from free text.

    Args:
        text: Any free-form string, or ``None``.

    Returns:
        The set of lowercased ``[a-z0-9]+`` runs in ``text``; an empty set for
        ``None`` or an empty/punctuation-only string.
    """
    if not text:
        return set()
    return set(_TOKEN_RE.findall(text.lower()))


def specialty_family(role: str | None) -> SpecialtyFamily | None:
    """Map a hire specialty (``Agent.role``) onto one v1 family, if clear.

    Args:
        role: The one-line hire specialty text, or ``None``.

    Returns:
        The single matching family, or ``None`` when the text is empty, matches
        no family, or matches more than one specific family. Ambiguity is
        reported as ``None`` rather than resolved by guessing, because callers
        treat an unknown family as "no opinion" instead of acting on it.
        ``"coordinate"`` only wins when nothing more specific matched.
    """
    tokens = tokenize(role)
    if not tokens:
        return None
    hits = [
        family
        for family, family_tokens in _SPECIALTY_TOKENS.items()
        if tokens & family_tokens
    ]
    specific = [family for family in hits if family != "coordinate"]
    if len(specific) == 1:
        return specific[0]
    if len(specific) > 1:
        return None
    if hits == ["coordinate"]:
        return "coordinate"
    return None


def infer_work_kind(
    title: str | None,
    description: str | None = None,
    *,
    requested_specialty: str | None = None,
) -> SpecialtyFamily | None:
    """Infer work kind from an explicit requested specialty or title/description.

    An explicit ``requested_specialty`` wins outright unless it resolves to
    ``"coordinate"``, which is too broad to describe a unit of work.

    Args:
        title: Task title, or ``None``.
        description: Task description, or ``None``.
        requested_specialty: Specialty the caller asked for, if any.

    Returns:
        The inferred family, or ``None`` when there is no signal or when two
        families tie on token overlap.
    """
    requested = specialty_family(requested_specialty)
    if requested and requested != "coordinate":
        return requested
    tokens = tokenize(title) | tokenize(description)
    if not tokens:
        return requested
    scores: dict[SpecialtyFamily, int] = {}
    for family, family_tokens in _WORK_KIND_TOKENS.items():
        overlap = tokens & family_tokens
        if overlap:
            scores[family] = len(overlap)
    if not scores:
        return requested
    best = max(scores.values())
    winners = [family for family, score in scores.items() if score == best]
    if len(winners) == 1:
        return winners[0]
    return None


def suggest_finish_line(
    specialty: str | None,
    description: str | None = None,
) -> str:
    """Suggest a default finish line from specialty, using description when useful.

    Does not persist or rewrite stored text. Blank ``done_fail_bar`` stays valid;
    empty done is still rejected by the checkable-claim rules.

    Args:
        specialty: The hire specialty text, or ``None``.
        description: Free-text description used only when ``specialty`` does not
            resolve to a family on its own.

    Returns:
        Always a non-empty operator-facing line: the family default, or the
        generic checkable-claim fallback when no family could be inferred.
    """
    family = specialty_family(specialty)
    if family is None:
        family = infer_work_kind(None, description)
    if family is None:
        return _FALLBACK_FINISH_LINE
    return _DEFAULT_FINISH_LINES[family]
