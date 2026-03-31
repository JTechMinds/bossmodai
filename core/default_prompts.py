"""BossMod AI — Default prompt templates loaded from markdown files."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from core.llm.template_engine import render_template


_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
_PROMPT_FILE_BY_KEY = {
    "system_prompt_template": "system_prompt.md",
    "runtime_contract_decision": "runtime_contract_decision.md",
    "runtime_contract_execution": "runtime_contract_execution.md",
    "runtime_block_conversation_envelope": "runtime_block_conversation_envelope.md",
    "runtime_block_file_deliverable_guidance": "runtime_block_file_deliverable_guidance.md",
    "runtime_block_communication_snapshot": "runtime_block_communication_snapshot.md",
    "runtime_block_trigger_event": "runtime_block_trigger_event.md",
    "internal_cli_result_wrapper": "internal/cli_result_wrapper.md",
    "internal_cli_approval_pause_note": "internal/cli_approval_pause_note.md",
    "internal_cli_authoritative_status": "internal/cli_authoritative_status.md",
    "internal_cli_authoritative_runtime": "internal/cli_authoritative_runtime.md",
    "internal_cli_authoritative_activity": "internal/cli_authoritative_activity.md",
    "internal_cli_authoritative_current_task": "internal/cli_authoritative_current_task.md",
    "internal_cli_authoritative_tasks": "internal/cli_authoritative_tasks.md",
    "internal_cli_authoritative_recent_work": "internal/cli_authoritative_recent_work.md",
    "internal_cli_authoritative_location": "internal/cli_authoritative_location.md",
    "internal_loop_approval_rejected_result": "internal/loop_approval_rejected_result.md",
    "internal_loop_approval_review_followup": "internal/loop_approval_review_followup.md",
    "internal_loop_execution_cli_followup": "internal/loop_execution_cli_followup.md",
    "internal_loop_decision_cli_followup": "internal/loop_decision_cli_followup.md",
    "internal_loop_decision_repair_primary": "internal/loop_decision_repair_primary.md",
    "internal_loop_decision_repair_preserve_intent": "internal/loop_decision_repair_preserve_intent.md",
    "internal_loop_decision_repair_keys": "internal/loop_decision_repair_keys.md",
    "internal_loop_execution_continue_work_missing_deliverable": "internal/loop_execution_continue_work_missing_deliverable.md",
    "internal_loop_execution_continue_move_to_desk": "internal/loop_execution_continue_move_to_desk.md",
    "internal_loop_execution_continue_work_generic": "internal/loop_execution_continue_work_generic.md",
    "internal_loop_execution_continue_meeting": "internal/loop_execution_continue_meeting.md",
    "internal_loop_execution_continue_conversation": "internal/loop_execution_continue_conversation.md",
    "internal_loop_execution_continue_break": "internal/loop_execution_continue_break.md",
    "internal_loop_execution_continue_generic": "internal/loop_execution_continue_generic.md",
    "internal_action_requires_workspace": "internal/action_requires_workspace.md",
    "internal_action_large_work_single_file_guidance": "internal/action_large_work_single_file_guidance.md",
    "internal_action_large_work_multi_file_guidance": "internal/action_large_work_multi_file_guidance.md",
    "internal_managed_writer_single_pass": "internal/managed_writer_single_pass.md",
    "internal_managed_writer_section_plan": "internal/managed_writer_section_plan.md",
    "internal_managed_writer_section": "internal/managed_writer_section.md",
    "internal_managed_writer_section_rewrite": "internal/managed_writer_section_rewrite.md",
    "internal_managed_writer_error_guidance": "internal/managed_writer_error_guidance.md",
}
_DEFAULT_PERSONALITY_FILES: tuple[tuple[str, str], ...] = (
    ("Research Analyst", "personalities/research_analyst.md"),
    ("Software Engineer", "personalities/software_engineer.md"),
    ("Growth Marketer", "personalities/growth_marketer.md"),
    ("UI/UX Designer", "personalities/ui_ux_designer.md"),
    ("Project Manager", "personalities/project_manager.md"),
    ("Data Analyst", "personalities/data_analyst.md"),
    ("QA Engineer", "personalities/qa_engineer.md"),
    ("Technical Writer", "personalities/technical_writer.md"),
    ("Creative Writer", "personalities/creative_writer.md"),
)
_DEFAULT_PERSONALITY_FILE_BY_NAME = dict(_DEFAULT_PERSONALITY_FILES)
_DEFAULT_ROLE_PROMPT_FILE = _PROMPTS_DIR / "personalities" / "default_role.md"


def prompt_file_path(setting_key: str) -> Path:
    """Return the on-disk markdown path for one seeded prompt setting."""
    filename = _PROMPT_FILE_BY_KEY.get(setting_key)
    if filename is None:
        raise KeyError(f"Unknown default prompt setting: {setting_key}")
    return _PROMPTS_DIR / filename


@lru_cache(maxsize=None)
def load_default_prompt(setting_key: str) -> str:
    """Load one default prompt template from the repo prompt files."""
    path = prompt_file_path(setting_key)
    return path.read_text(encoding="utf-8").rstrip("\n")


def personality_prompt_file_path(name: str) -> Path:
    """Return the on-disk markdown path for one seeded default personality."""
    filename = _DEFAULT_PERSONALITY_FILE_BY_NAME.get(name)
    if filename is None:
        raise KeyError(f"Unknown default personality prompt: {name}")
    return _PROMPTS_DIR / filename


@lru_cache(maxsize=None)
def load_default_personality_prompt(name: str) -> str:
    """Load one default personality template from the repo prompt files."""
    path = personality_prompt_file_path(name)
    return path.read_text(encoding="utf-8").rstrip("\n")


def iter_default_personality_prompts() -> tuple[tuple[str, str], ...]:
    """Return the seeded default personalities in stable order."""
    return tuple((name, load_default_personality_prompt(name)) for name, _ in _DEFAULT_PERSONALITY_FILES)


@lru_cache(maxsize=1)
def load_default_role_prompt() -> str:
    """Load the generic fallback personality prompt for agents without a custom template."""
    return _DEFAULT_ROLE_PROMPT_FILE.read_text(encoding="utf-8").rstrip("\n")


def render_default_prompt(
    setting_key: str,
    context: dict[str, Any],
    *,
    allowed_paths: set[str],
) -> str:
    """Render one file-backed prompt template against a bounded context."""
    return render_template(
        load_default_prompt(setting_key),
        context,
        allowed_paths=allowed_paths,
    )


SYSTEM_PROMPT_TEMPLATE = load_default_prompt("system_prompt_template")
RUNTIME_CONTRACT_DECISION_TEMPLATE = load_default_prompt("runtime_contract_decision")
RUNTIME_CONTRACT_EXECUTION_TEMPLATE = load_default_prompt("runtime_contract_execution")
RUNTIME_BLOCK_CONVERSATION_ENVELOPE_TEMPLATE = load_default_prompt("runtime_block_conversation_envelope")
RUNTIME_BLOCK_FILE_DELIVERABLE_GUIDANCE_TEMPLATE = load_default_prompt("runtime_block_file_deliverable_guidance")
RUNTIME_BLOCK_COMMUNICATION_SNAPSHOT_TEMPLATE = load_default_prompt("runtime_block_communication_snapshot")
RUNTIME_BLOCK_TRIGGER_EVENT_TEMPLATE = load_default_prompt("runtime_block_trigger_event")
