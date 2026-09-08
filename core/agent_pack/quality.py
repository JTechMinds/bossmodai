"""Senior pack quality checks beyond the bare ``bossmod.agent_pack/v1`` schema.

Catalog import and fixture CI run these rules so a pack is a hire contract
with Mission, scope, fail examples, and a handoff — not a one-liner.
Export of a casual live hire may still serialize a schema-valid snapshot
that does not meet this bar.
"""

from __future__ import annotations

from core.agent_pack.schema import AgentPack, AgentPackError
from core.agent_pack.sections import (
    DESCRIPTION_SECTION_KEYS,
    extract_labeled_sections,
)

# Floors keep catalog packs from shipping as a single vague sentence.
MISSION_MIN_LEN = 40
SCOPE_MIN_LEN = 24
HANDOFF_MIN_LEN = 24
DONE_MIN_LEN = 40
FAIL_EXAMPLES_MIN_LEN = 40
DESCRIPTION_MIN_LEN = 160
DONE_TOTAL_MIN_LEN = 80

_SECTION_MIN_LEN: dict[str, int] = {
    "mission": MISSION_MIN_LEN,
    "in_scope": SCOPE_MIN_LEN,
    "out_of_scope": SCOPE_MIN_LEN,
    "handoff": HANDOFF_MIN_LEN,
}

_SECTION_TITLES: dict[str, str] = {
    "mission": "Mission",
    "in_scope": "In scope",
    "out_of_scope": "Out of scope",
    "handoff": "Handoff",
}


def validate_pack_quality(pack: AgentPack) -> None:
    """Require structured senior sections on an already schema-valid pack."""
    _, desc_sections = extract_labeled_sections(pack.description)
    done_preamble, done_sections = extract_labeled_sections(pack.what_done_looks_like)

    missing = [
        _SECTION_TITLES[key]
        for key in DESCRIPTION_SECTION_KEYS
        if not (desc_sections.get(key) or "").strip()
    ]
    if missing:
        raise AgentPackError(
            "Pack description is missing required sections: " + ", ".join(missing) + ".",
            code="pack_quality",
        )

    for key in DESCRIPTION_SECTION_KEYS:
        body = desc_sections[key].strip()
        minimum = _SECTION_MIN_LEN[key]
        if len(body) < minimum:
            raise AgentPackError(
                f"Pack section {_SECTION_TITLES[key]!r} is too short "
                f"(minimum {minimum} characters).",
                code="pack_quality",
            )

    if len(pack.description.strip()) < DESCRIPTION_MIN_LEN:
        raise AgentPackError(
            f"Pack description is too short (minimum {DESCRIPTION_MIN_LEN} characters).",
            code="pack_quality",
        )

    success = (done_preamble or done_sections.get("done") or "").strip()
    fail = (done_sections.get("fail_examples") or "").strip()
    if not fail:
        raise AgentPackError(
            "Pack what_done_looks_like must include a Fail examples section.",
            code="pack_quality",
        )
    if len(success) < DONE_MIN_LEN:
        raise AgentPackError(
            f"Pack what_done_looks_like success bar is too short "
            f"(minimum {DONE_MIN_LEN} characters).",
            code="pack_quality",
        )
    if len(fail) < FAIL_EXAMPLES_MIN_LEN:
        raise AgentPackError(
            f"Pack Fail examples section is too short "
            f"(minimum {FAIL_EXAMPLES_MIN_LEN} characters).",
            code="pack_quality",
        )
    if len(pack.what_done_looks_like.strip()) < DONE_TOTAL_MIN_LEN:
        raise AgentPackError(
            f"Pack what_done_looks_like is too short "
            f"(minimum {DONE_TOTAL_MIN_LEN} characters).",
            code="pack_quality",
        )
    for item in pack.tools_hint:
        if any(ch.isspace() for ch in item):
            raise AgentPackError(
                "Pack field 'tools_hint' must be a list of short tool names, not prose.",
                code="pack_quality",
            )
