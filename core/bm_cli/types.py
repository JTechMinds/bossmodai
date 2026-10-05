"""BossMod AI — Shared BossMod CLI runtime types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.models import Agent, AgentState


@dataclass(frozen=True, slots=True)
class BossModCliResult:
    """Turn-local result of a BossMod CLI command."""

    command: str
    ok: bool
    detail: str
    prompt_content: str
    kind: str = "generic"
    data: dict[str, Any] | None = None
    cwd: str | None = None
    approval_required: bool = False
    consent_required: bool = False
    executor: str = "virtual"
    exit_code: int = 0
    matched_rule_id: str | None = None
    approval_request_id: str | None = None
    consent_request_id: str | None = None
    # Image files to show the model with this result (see
    # core.llm.attachment_parts.CLI_IMAGE_PATHS_KEY). Paths, never bytes.
    image_paths: tuple[str, ...] = ()
    # One line that stands in for this result's text once a newer CLI result
    # image supersedes it (core.llm.attachment_parts.SUMMARY_KEY). Only kept on
    # results that carry image_paths.
    summary: str | None = None
    # A command on the no-retry list reached its handler (core.bm_cli.retry_policy).
    blocks_retry: bool = False


@dataclass(frozen=True, slots=True)
class ParsedCliCommand:
    """Parsed shell-like BossMod CLI command."""

    raw: str
    name: str
    args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CliExecutionContext:
    """Execution context shared across BossMod CLI handlers."""

    agent: Agent
    state: AgentState
    cwd: str
