"""BossMod AI — Managed-writer prompt rendering and section assembly."""

from __future__ import annotations

from typing import Any

from core.bm_cli.managed_writer.helpers import _managed_max_sections_per_file
from core.bm_cli.managed_writer.types import (
    ManagedSectionPlan,
    _MANAGED_WRITE_DONE_SENTINEL,
    _MANAGED_WRITE_PLAN_SENTINEL,
    _MANAGED_WRITER_PROMPT_ALLOWED_PATHS,
)
from core.default_prompts import render_default_prompt

def _render_managed_writer_prompt(template_key: str, context: dict[str, Any]) -> str:
    """Render one file-backed managed-writer prompt template."""
    return render_default_prompt(
        template_key,
        context,
        allowed_paths=_MANAGED_WRITER_PROMPT_ALLOWED_PATHS,
    )


def _managed_single_pass_instruction(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
) -> str:
    """Return the runtime instruction for one direct file-generation attempt."""
    return _render_managed_writer_prompt(
        "internal_managed_writer_single_pass",
        {
            "target_path": target_path,
            "file_goal": file_goal or "",
            "done_sentinel": _MANAGED_WRITE_DONE_SENTINEL,
            "plan_sentinel": _MANAGED_WRITE_PLAN_SENTINEL,
            "batch": {
                "is_batch": file_count > 1,
                "file_index": file_index,
                "file_count": file_count,
            },
        },
    )


def _managed_section_plan_instruction(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
) -> str:
    """Return the runtime instruction for planning a large document."""
    return _render_managed_writer_prompt(
        "internal_managed_writer_section_plan",
        {
            "target_path": target_path,
            "file_goal": file_goal or "",
            "max_sections": _managed_max_sections_per_file(),
            "batch": {
                "is_batch": file_count > 1,
                "file_index": file_index,
                "file_count": file_count,
            },
        },
    )


def _managed_section_instruction(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
    section: ManagedSectionPlan,
    section_index: int,
    section_count: int,
    outline_lines: list[str],
) -> str:
    """Return the runtime instruction for one section body."""
    return _render_managed_writer_prompt(
        "internal_managed_writer_section",
        {
            "target_path": target_path,
            "file_goal": file_goal or "",
            "section_index": section_index,
            "section_count": section_count,
            "outline": "\n".join(outline_lines),
            "section": {
                "heading": section.heading,
                "goal": section.goal,
            },
            "batch": {
                "is_batch": file_count > 1,
                "file_index": file_index,
                "file_count": file_count,
            },
        },
    )


def _managed_section_rewrite_instruction(
    *,
    target_path: str,
    section_heading: str,
    rewrite_goal: str,
    current_body: str,
    outline_lines: list[str],
    previous_heading: str | None,
    next_heading: str | None,
) -> str:
    """Return the runtime instruction for rewriting one existing section body."""
    return _render_managed_writer_prompt(
        "internal_managed_writer_section_rewrite",
        {
            "target_path": target_path,
            "section_heading": section_heading,
            "rewrite_goal": rewrite_goal,
            "outline": "\n".join(outline_lines),
            "previous_heading": previous_heading or "",
            "next_heading": next_heading or "",
            "current_body": current_body,
        },
    )


def _render_outline_lines(sections: list[ManagedSectionPlan]) -> list[str]:
    """Render section headings/goals into compact instruction lines."""
    return [f"- {section.heading} :: {section.goal}" for section in sections]


def _assemble_section(heading: str, body: str) -> str:
    """Assemble one section deterministically from heading and generated body."""
    clean_body = body.strip()
    return f"{heading}\n\n{clean_body}" if clean_body else heading

