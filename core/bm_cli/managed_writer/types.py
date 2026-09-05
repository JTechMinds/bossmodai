"""BossMod AI — Managed-writer types, sentinels, and prompt path set."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.bm_cli.types import BossModCliResult

_MANAGED_WRITE_DONE_SENTINEL = "<<BOSSMOD_FILE_DONE>>"


_MANAGED_WRITE_PLAN_SENTINEL = "<<BOSSMOD_PLAN_REQUIRED>>"


_MANAGED_WRITER_PROMPT_ALLOWED_PATHS = {
    "target_path",
    "file_goal",
    "done_sentinel",
    "plan_sentinel",
    "max_sections",
    "batch.is_batch",
    "batch.file_index",
    "batch.file_count",
    "section.heading",
    "section.goal",
    "section_index",
    "section_count",
    "outline",
    "section_heading",
    "rewrite_goal",
    "previous_heading",
    "next_heading",
    "current_body",
}


@dataclass(frozen=True, slots=True)
class ManagedWriteOutcome:
    """Result of one runtime-managed file authoring session."""

    cli_result: BossModCliResult
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    chunks: int


@dataclass(frozen=True, slots=True)
class ManagedBatchFileSpec:
    """One file request inside a batch-write manifest."""

    path: str
    goal: str


@dataclass(frozen=True, slots=True)
class ManagedGeneratedFile:
    """One generated file body prior to writing it to disk."""

    path: str
    goal: str
    content: str
    chars: int
    byte_count: int
    calls: int
    strategy: str
    section_count: int


@dataclass(frozen=True, slots=True)
class ManagedGenerationOutcome:
    """Generation outcome for one file body."""

    content: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    chunks: int
    byte_count: int
    strategy: str
    section_count: int
    cli_result: BossModCliResult | None = None


@dataclass(frozen=True, slots=True)
class ManagedDirectDraftOutcome:
    """Outcome of the initial direct authoring attempt."""

    content: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    byte_count: int
    needs_section_plan: bool
    cli_result: BossModCliResult | None = None


@dataclass(frozen=True, slots=True)
class ManagedSectionPlan:
    """One authored section in a planned long-form file."""

    heading: str
    goal: str


@dataclass(frozen=True, slots=True)
class ManagedWriteProgress:
    """One runtime-visible progress update emitted during managed authoring."""

    stage: str
    detail: str
    path: str | None = None
    file_index: int | None = None
    file_count: int | None = None
    section_index: int | None = None
    section_count: int | None = None
    strategy: str | None = None
    counts_as_progress: bool = False


ManagedWriteProgressCallback = Callable[[ManagedWriteProgress], Awaitable[None]]

