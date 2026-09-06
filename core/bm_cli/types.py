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
