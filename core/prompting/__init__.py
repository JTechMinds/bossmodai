"""BossMod AI — Prompt surface registry and lint helpers."""

from .runtime_prompt_lint import PromptLintIssue, PromptLintReport, lint_runtime_prompts
from .runtime_prompt_registry import (
    RUNTIME_CONTRACT_SETTING_KEYS,
    RuntimePromptSurface,
    collect_runtime_prompt_texts,
    resolve_runtime_prompt_text,
    runtime_prompt_surface_map,
    runtime_prompt_surfaces,
)

__all__ = [
    "PromptLintIssue",
    "PromptLintReport",
    "RUNTIME_CONTRACT_SETTING_KEYS",
    "RuntimePromptSurface",
    "collect_runtime_prompt_texts",
    "lint_runtime_prompts",
    "resolve_runtime_prompt_text",
    "runtime_prompt_surface_map",
    "runtime_prompt_surfaces",
]
