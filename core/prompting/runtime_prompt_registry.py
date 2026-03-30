"""BossMod AI — Model-facing prompt surface registry."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Mapping

from core import config
from core.default_prompts import load_default_prompt


PromptSourceKind = Literal["setting", "default_file"]
PromptCategory = Literal["system", "runtime_contract", "runtime_block", "internal_loop"]


@dataclass(frozen=True, slots=True)
class RuntimePromptSurface:
    """One model-facing prompt surface that contributes to agent behavior."""

    key: str
    label: str
    source_kind: PromptSourceKind
    category: PromptCategory


RUNTIME_CONTRACT_SETTING_KEYS = (
    "runtime_contract_decision",
    "runtime_contract_execution",
    "runtime_block_trigger_event",
    "runtime_block_conversation_envelope",
    "runtime_block_file_deliverable_guidance",
    "runtime_block_communication_snapshot",
)

_RUNTIME_PROMPT_SURFACES: tuple[RuntimePromptSurface, ...] = (
    RuntimePromptSurface("system_prompt_template", "System Prompt Template", "setting", "system"),
    RuntimePromptSurface("runtime_contract_decision", "Decision Runtime Contract", "setting", "runtime_contract"),
    RuntimePromptSurface("runtime_contract_execution", "Execution Runtime Contract", "setting", "runtime_contract"),
    RuntimePromptSurface("runtime_block_trigger_event", "Trigger Event Block", "setting", "runtime_block"),
    RuntimePromptSurface(
        "runtime_block_conversation_envelope",
        "Conversation Envelope Block",
        "setting",
        "runtime_block",
    ),
    RuntimePromptSurface(
        "runtime_block_file_deliverable_guidance",
        "File Deliverable Guidance Block",
        "setting",
        "runtime_block",
    ),
    RuntimePromptSurface(
        "runtime_block_communication_snapshot",
        "Communication Snapshot Block",
        "setting",
        "runtime_block",
    ),
    RuntimePromptSurface("internal_loop_decision_cli_followup", "Internal Decision CLI Follow-up", "default_file", "internal_loop"),
    RuntimePromptSurface("internal_loop_decision_repair_primary", "Internal Decision Repair Primary", "default_file", "internal_loop"),
    RuntimePromptSurface(
        "internal_loop_decision_repair_preserve_intent",
        "Internal Decision Repair Preserve Intent",
        "default_file",
        "internal_loop",
    ),
    RuntimePromptSurface("internal_loop_decision_repair_keys", "Internal Decision Repair Keys", "default_file", "internal_loop"),
    RuntimePromptSurface("internal_loop_execution_cli_followup", "Internal Execution CLI Follow-up", "default_file", "internal_loop"),
    RuntimePromptSurface(
        "internal_loop_execution_continue_work_missing_deliverable",
        "Internal Execution Missing Deliverable Follow-up",
        "default_file",
        "internal_loop",
    ),
    RuntimePromptSurface(
        "internal_loop_execution_continue_move_to_desk",
        "Internal Execution Move-to-Desk Follow-up",
        "default_file",
        "internal_loop",
    ),
    RuntimePromptSurface(
        "internal_loop_execution_continue_work_generic",
        "Internal Execution Work Follow-up",
        "default_file",
        "internal_loop",
    ),
    RuntimePromptSurface("internal_loop_execution_continue_meeting", "Internal Execution Meeting Follow-up", "default_file", "internal_loop"),
    RuntimePromptSurface(
        "internal_loop_execution_continue_conversation",
        "Internal Execution Conversation Follow-up",
        "default_file",
        "internal_loop",
    ),
    RuntimePromptSurface("internal_loop_execution_continue_break", "Internal Execution Break Follow-up", "default_file", "internal_loop"),
    RuntimePromptSurface("internal_loop_execution_continue_generic", "Internal Execution Generic Follow-up", "default_file", "internal_loop"),
)


def runtime_prompt_surfaces() -> tuple[RuntimePromptSurface, ...]:
    """Return the ordered list of model-facing prompt surfaces."""
    return _RUNTIME_PROMPT_SURFACES


@lru_cache(maxsize=1)
def runtime_prompt_surface_map() -> dict[str, RuntimePromptSurface]:
    """Return prompt surfaces keyed by config/default prompt key."""
    return {surface.key: surface for surface in _RUNTIME_PROMPT_SURFACES}


def resolve_runtime_prompt_text(prompt_key: str, overrides: Mapping[str, str] | None = None) -> str:
    """Resolve one model-facing prompt surface from overrides, settings, or defaults."""
    if overrides and prompt_key in overrides:
        return str(overrides[prompt_key])
    surface = runtime_prompt_surface_map().get(prompt_key)
    if surface is None:
        raise KeyError(f"Unknown runtime prompt surface: {prompt_key}")
    if surface.source_kind == "setting":
        return config.require(prompt_key)
    return load_default_prompt(prompt_key)


def collect_runtime_prompt_texts(
    overrides: Mapping[str, str] | None = None,
    *,
    keys: tuple[str, ...] | None = None,
) -> dict[str, str]:
    """Collect resolved prompt text for the requested model-facing surfaces."""
    selected_keys = keys or tuple(surface.key for surface in _RUNTIME_PROMPT_SURFACES)
    return {key: resolve_runtime_prompt_text(key, overrides) for key in selected_keys}
