"""BossMod AI — Direct and sectioned managed-file generation."""

from __future__ import annotations

import json
from typing import Any

from core.bm_cli.managed_writer.helpers import (
    _emit_progress,
    _managed_max_sections_per_file,
    _managed_write_byte_limit,
    _managed_writer_error_result,
    _normalize_generated_text,
    _remove_control_tokens,
    _strip_duplicate_heading,
)
from core.bm_cli.managed_writer.prompts import (
    _assemble_section,
    _managed_section_instruction,
    _managed_section_plan_instruction,
    _managed_single_pass_instruction,
    _render_outline_lines,
)
from core.bm_cli.managed_writer.types import (
    ManagedDirectDraftOutcome,
    ManagedGenerationOutcome,
    ManagedSectionPlan,
    ManagedWriteProgress,
    ManagedWriteProgressCallback,
    _MANAGED_WRITE_DONE_SENTINEL,
    _MANAGED_WRITE_PLAN_SENTINEL,
)
from core.llm import client

async def _generate_managed_file(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
    command: str,
    model: str,
    api_config: dict[str, Any],
    base_context: list[dict[str, str]],
    action_response: str,
    cwd: str,
    progress_callback: ManagedWriteProgressCallback | None = None,
) -> ManagedGenerationOutcome:
    """Generate one file body using direct or planned section authoring."""
    await _emit_progress(
        progress_callback,
        ManagedWriteProgress(
            stage="file_started",
            detail=f"Writing {target_path}",
            path=target_path,
            file_index=file_index,
            file_count=file_count,
        ),
    )
    direct = await _generate_direct_file(
        target_path=target_path,
        file_goal=file_goal,
        file_index=file_index,
        file_count=file_count,
        command=command,
        model=model,
        api_config=api_config,
        base_context=base_context,
        action_response=action_response,
        cwd=cwd,
        progress_callback=progress_callback,
    )
    if direct.cli_result is not None:
        return ManagedGenerationOutcome(
            content=None,
            prompt_tokens=direct.prompt_tokens,
            completion_tokens=direct.completion_tokens,
            total_tokens=direct.total_tokens,
            chunks=1,
            byte_count=direct.byte_count,
            strategy="single_pass",
            section_count=0,
            cli_result=direct.cli_result,
        )
    if not direct.needs_section_plan:
        return ManagedGenerationOutcome(
            content=direct.content,
            prompt_tokens=direct.prompt_tokens,
            completion_tokens=direct.completion_tokens,
            total_tokens=direct.total_tokens,
            chunks=1,
            byte_count=direct.byte_count,
            strategy="single_pass",
            section_count=0,
        )

    sectioned = await _generate_sectioned_file(
        target_path=target_path,
        file_goal=file_goal,
        file_index=file_index,
        file_count=file_count,
        command=command,
        model=model,
        api_config=api_config,
        base_context=base_context,
        action_response=action_response,
        cwd=cwd,
        progress_callback=progress_callback,
    )
    return ManagedGenerationOutcome(
        content=sectioned.content,
        prompt_tokens=direct.prompt_tokens + sectioned.prompt_tokens,
        completion_tokens=direct.completion_tokens + sectioned.completion_tokens,
        total_tokens=direct.total_tokens + sectioned.total_tokens,
        chunks=1 + sectioned.chunks,
        byte_count=sectioned.byte_count,
        strategy=sectioned.strategy,
        section_count=sectioned.section_count,
        cli_result=sectioned.cli_result,
    )


async def _generate_direct_file(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
    command: str,
    model: str,
    api_config: dict[str, Any],
    base_context: list[dict[str, str]],
    action_response: str,
    cwd: str,
    progress_callback: ManagedWriteProgressCallback | None = None,
) -> ManagedDirectDraftOutcome:
    """Attempt to author the full file in one provider call."""
    del progress_callback
    byte_limit = _managed_write_byte_limit()
    response = await client.completion(
        model=model,
        messages=[
            *base_context,
            {"role": "assistant", "content": action_response},
            {
                "role": "system",
                "content": _managed_single_pass_instruction(
                    target_path=target_path,
                    file_goal=file_goal,
                    file_index=file_index,
                    file_count=file_count,
                ),
            },
        ],
        api_base=api_config.get("api_base"),
        api_key=api_config.get("api_key"),
        extra_body=api_config.get("extra_body"),
    )
    text = _normalize_generated_text(response.content)
    prompt_tokens = response.prompt_tokens
    completion_tokens = response.completion_tokens
    total_tokens = response.total_tokens

    if _MANAGED_WRITE_DONE_SENTINEL in text:
        content = _remove_control_tokens(text)
        byte_count = len(content.encode("utf-8"))
        if not content.strip():
            return ManagedDirectDraftOutcome(
                content=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                byte_count=byte_count,
                needs_section_plan=False,
                cli_result=_managed_writer_error_result(
                    command,
                    "Managed writer produced an empty file body.",
                    cwd=cwd,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    chunks=1,
                    byte_count=byte_count,
                    strategy="single_pass",
                    section_count=0,
                ),
            )
        if byte_count > byte_limit:
            return ManagedDirectDraftOutcome(
                content=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                byte_count=byte_count,
                needs_section_plan=False,
                cli_result=_managed_writer_error_result(
                    command,
                    f"Managed writer exceeded maximum write size ({byte_count:,} bytes > {byte_limit:,} byte limit).",
                    cwd=cwd,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    chunks=1,
                    byte_count=byte_count,
                    strategy="single_pass",
                    section_count=0,
                ),
            )
        return ManagedDirectDraftOutcome(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            byte_count=byte_count,
            needs_section_plan=False,
        )

    if _MANAGED_WRITE_PLAN_SENTINEL in text:
        return ManagedDirectDraftOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            byte_count=0,
            needs_section_plan=True,
        )

    if not text.strip():
        return ManagedDirectDraftOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            byte_count=0,
            needs_section_plan=False,
            cli_result=_managed_writer_error_result(
                command,
                "Managed writer returned an empty draft response.",
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=1,
                byte_count=0,
                strategy="single_pass",
                section_count=0,
            ),
        )

    return ManagedDirectDraftOutcome(
        content=None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        byte_count=len(text.encode("utf-8")),
        needs_section_plan=True,
    )


async def _generate_sectioned_file(
    *,
    target_path: str,
    file_goal: str | None,
    file_index: int,
    file_count: int,
    command: str,
    model: str,
    api_config: dict[str, Any],
    base_context: list[dict[str, str]],
    action_response: str,
    cwd: str,
    progress_callback: ManagedWriteProgressCallback | None = None,
) -> ManagedGenerationOutcome:
    """Generate a file from a planned section outline and local assembly."""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    call_count = 0

    plan_response = await client.completion(
        model=model,
        messages=[
            *base_context,
            {"role": "assistant", "content": action_response},
            {
                "role": "system",
                "content": _managed_section_plan_instruction(
                    target_path=target_path,
                    file_goal=file_goal,
                    file_index=file_index,
                    file_count=file_count,
                ),
            },
        ],
        api_base=api_config.get("api_base"),
        api_key=api_config.get("api_key"),
        extra_body=api_config.get("extra_body"),
    )
    prompt_tokens += plan_response.prompt_tokens
    completion_tokens += plan_response.completion_tokens
    total_tokens += plan_response.total_tokens
    call_count += 1

    try:
        sections = _parse_section_plan(plan_response.content)
    except ValueError as exc:
        return ManagedGenerationOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=call_count,
            byte_count=0,
            strategy="sectioned",
            section_count=0,
            cli_result=_managed_writer_error_result(
                command,
                str(exc),
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=call_count,
                byte_count=0,
                strategy="sectioned",
                section_count=0,
            ),
        )

    await _emit_progress(
        progress_callback,
        ManagedWriteProgress(
            stage="sections_planned",
            detail=f"Planned {len(sections)} section{'s' if len(sections) != 1 else ''} for {target_path}",
            path=target_path,
            file_index=file_index,
            file_count=file_count,
            section_count=len(sections),
            strategy="sectioned",
        ),
    )

    rendered_sections: list[str] = []
    outline_lines = _render_outline_lines(sections)
    for index, section in enumerate(sections, start=1):
        await _emit_progress(
            progress_callback,
            ManagedWriteProgress(
                stage="section_started",
                detail=f"Writing section {index}/{len(sections)} of {target_path}: {section.heading}",
                path=target_path,
                file_index=file_index,
                file_count=file_count,
                section_index=index,
                section_count=len(sections),
                strategy="sectioned",
                counts_as_progress=index > 1,
            ),
        )
        response = await client.completion(
            model=model,
            messages=[
                *base_context,
                {"role": "assistant", "content": action_response},
                {
                    "role": "system",
                    "content": _managed_section_instruction(
                        target_path=target_path,
                        file_goal=file_goal,
                        file_index=file_index,
                        file_count=file_count,
                        section=section,
                        section_index=index,
                        section_count=len(sections),
                        outline_lines=outline_lines,
                    ),
                },
            ],
            api_base=api_config.get("api_base"),
            api_key=api_config.get("api_key"),
            extra_body=api_config.get("extra_body"),
        )
        prompt_tokens += response.prompt_tokens
        completion_tokens += response.completion_tokens
        total_tokens += response.total_tokens
        call_count += 1

        section_body = _normalize_generated_text(response.content).strip()
        section_body = _strip_duplicate_heading(section_body, section.heading).strip()
        if not section_body:
            return ManagedGenerationOutcome(
                content=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=call_count,
                byte_count=sum(len(part.encode("utf-8")) for part in rendered_sections),
                strategy="sectioned",
                section_count=len(sections),
                cli_result=_managed_writer_error_result(
                    command,
                    f'Managed writer produced an empty section body for "{section.heading}".',
                    cwd=cwd,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    chunks=call_count,
                    byte_count=sum(len(part.encode("utf-8")) for part in rendered_sections),
                    strategy="sectioned",
                    section_count=len(sections),
                ),
            )
        rendered_sections.append(_assemble_section(section.heading, section_body))

    content = "\n\n".join(rendered_sections).strip()
    byte_count = len(content.encode("utf-8"))
    byte_limit = _managed_write_byte_limit()
    if byte_count > byte_limit:
        return ManagedGenerationOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=call_count,
            byte_count=byte_count,
            strategy="sectioned",
            section_count=len(sections),
            cli_result=_managed_writer_error_result(
                command,
                f"Managed writer exceeded maximum write size ({byte_count:,} bytes > {byte_limit:,} byte limit).",
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=call_count,
                byte_count=byte_count,
                strategy="sectioned",
                section_count=len(sections),
            ),
        )
    if not content.strip():
        return ManagedGenerationOutcome(
            content=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            chunks=call_count,
            byte_count=byte_count,
            strategy="sectioned",
            section_count=len(sections),
            cli_result=_managed_writer_error_result(
                command,
                "Managed writer produced an empty file body.",
                cwd=cwd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                chunks=call_count,
                byte_count=byte_count,
                strategy="sectioned",
                section_count=len(sections),
            ),
        )
    return ManagedGenerationOutcome(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        chunks=call_count,
        byte_count=byte_count,
        strategy="sectioned",
        section_count=len(sections),
    )


def _parse_section_plan(raw_response: str) -> list[ManagedSectionPlan]:
    """Parse the section-plan JSON returned by the planner call."""
    normalized = _normalize_generated_text(raw_response).strip()
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Managed writer section plan JSON parse error: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Managed writer section plan must be a JSON object.")
    extra = set(parsed) - {"sections"}
    if extra:
        raise ValueError(f'Managed writer section plan has unexpected keys: {", ".join(sorted(extra))}')
    items = parsed.get("sections")
    if not isinstance(items, list) or not items:
        raise ValueError('Managed writer section plan must contain a non-empty "sections" list.')
    max_sections = _managed_max_sections_per_file()
    if len(items) > max_sections:
        raise ValueError(
            f"Managed writer section plan exceeds the {max_sections}-section limit. Narrow the document scope."
        )

    sections: list[ManagedSectionPlan] = []
    seen_headings: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Managed writer section plan entries must be objects.")
        extra_item = set(item) - {"heading", "goal"}
        if extra_item:
            raise ValueError(f'Managed writer section entries have unexpected keys: {", ".join(sorted(extra_item))}')
        heading = str(item.get("heading") or "").strip()
        goal = str(item.get("goal") or "").strip()
        if not heading or not goal:
            raise ValueError('Managed writer section entries require non-empty "heading" and "goal" values.')
        if heading in seen_headings:
            raise ValueError(f"Managed writer section plan repeats the same heading: {heading}")
        seen_headings.add(heading)
        sections.append(ManagedSectionPlan(heading=heading, goal=goal))
    return sections

