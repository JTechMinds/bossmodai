"""BossMod AI — Lint model-facing prompt surfaces for contract consistency."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Mapping

from core.default_prompts import load_default_prompt
from .runtime_prompt_registry import collect_runtime_prompt_texts, runtime_prompt_surface_map


PromptSeverity = Literal["warning", "error"]

_DISALLOWED_TOKEN_RULES: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (
        re.compile(r"\bbm_cli\b"),
        "error",
        "internal_bm_cli_name",
        'Use the model-facing act `cli`, not the internal runtime name `bm_cli`.',
    ),
    (
        re.compile(r"\bwalkTo\b"),
        "error",
        "internal_walkto_name",
        'Use the model-facing act `walk`, not the internal runtime action name `walkTo`.',
    ),
    (
        re.compile(r"\bdelegateTask\b"),
        "error",
        "internal_delegate_task_name",
        'Use the model-facing act `assign`, not the internal runtime action name `delegateTask`.',
    ),
    (
        re.compile(r"\bstartTask\b"),
        "error",
        "unsupported_start_task_name",
        'Use the compact decision/execution contract instead of `startTask` actions.',
    ),
    (
        re.compile(r"\bresumeTask\b"),
        "error",
        "unsupported_resume_task_name",
        'Use the compact execution contract instead of `resumeTask` actions.',
    ),
)

_LIFECYCLE_NAME_PATTERN = re.compile(
    r"complete\s*[/,]\s*blocked\s*[/,]\s*delegated\s*[/,]\s*abandoned",
    re.IGNORECASE,
)
_THOUGHT_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'[`"]thought[`"]'),
    re.compile(r'"th"\s+is\s+thought', re.IGNORECASE),
)
_SYSTEM_TASK_CONTEXT_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"##\s+Open Tasks\b", re.IGNORECASE),
        "open_tasks_section_reference",
        "Use a board-first task section instead of `Open Tasks`.",
    ),
    (
        re.compile(r"\{\{\s*pending_tasks\s*\}\}"),
        "pending_tasks_placeholder_reference",
        "Use the board-first prompt variable `task_board` instead of `pending_tasks`.",
    ),
    (
        re.compile(r"Treat\s+`Open Tasks`", re.IGNORECASE),
        "open_tasks_rule_reference",
        "System-prompt task guidance should refer to the task board, not `Open Tasks`.",
    ),
)


@dataclass(frozen=True, slots=True)
class PromptLintIssue:
    """One prompt-health finding."""

    surface_key: str
    surface_label: str
    severity: PromptSeverity
    code: str
    message: str

    def to_payload(self) -> dict[str, str]:
        """Serialize one issue for API/UI consumers."""
        return {
            "surface_key": self.surface_key,
            "surface_label": self.surface_label,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class PromptLintReport:
    """Aggregated prompt-health report."""

    issues: tuple[PromptLintIssue, ...]

    @property
    def ok(self) -> bool:
        return len(self.issues) == 0

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def status(self) -> str:
        if self.has_errors:
            return "error"
        if self.issues:
            return "warning"
        return "clean"

    def to_payload(self) -> dict[str, object]:
        """Serialize the report for API/UI consumers."""
        return {
            "ok": self.ok,
            "status": self.status,
            "issues": [issue.to_payload() for issue in self.issues],
        }


def lint_runtime_prompts(overrides: Mapping[str, str] | None = None) -> PromptLintReport:
    """Lint the current model-facing prompt surfaces or one override set."""
    surfaces = runtime_prompt_surface_map()
    issues: list[PromptLintIssue] = []

    resolved_texts = collect_runtime_prompt_texts(overrides)
    for surface_key, text in resolved_texts.items():
        surface = surfaces[surface_key]
        issues.extend(_lint_surface(surface_key, surface.label, text))
        if not overrides:
            issues.extend(_lint_saved_prompt_mismatch(surface_key, surface.label, surface.source_kind, text))

    return PromptLintReport(issues=tuple(issues))


def _lint_surface(surface_key: str, surface_label: str, text: str) -> list[PromptLintIssue]:
    issues: list[PromptLintIssue] = []

    for pattern, severity, code, message in _DISALLOWED_TOKEN_RULES:
        if pattern.search(text):
            issues.append(
                PromptLintIssue(
                    surface_key=surface_key,
                    surface_label=surface_label,
                    severity=severity,  # type: ignore[arg-type]
                    code=code,
                    message=message,
                )
            )

    if _LIFECYCLE_NAME_PATTERN.search(text):
        issues.append(
            PromptLintIssue(
                surface_key=surface_key,
                surface_label=surface_label,
                severity="error",
                code="terminal_act_name_mismatch",
                message='Use model-facing terminal acts `done`, `block`, `deleg`, and `drop` instead of internal lifecycle names.',
            )
        )

    if any(pattern.search(text) for pattern in _THOUGHT_KEY_PATTERNS):
        issues.append(
            PromptLintIssue(
                surface_key=surface_key,
                surface_label=surface_label,
                severity="error",
                code="thought_key_reference",
                message='Use the model-facing key `th` for the short admin note. Do not instruct the model to emit `thought`.',
            )
        )

    if surface_key == "system_prompt_template":
        for pattern, code, message in _SYSTEM_TASK_CONTEXT_PATTERNS:
            if pattern.search(text):
                issues.append(
                    PromptLintIssue(
                        surface_key=surface_key,
                        surface_label=surface_label,
                        severity="error",
                        code=code,
                        message=message,
                    )
                )

    if surface_key == "runtime_contract_decision" and '{"act":"cli"' in text and "OPTIONAL LOOKUP ACT FOR ANY DECISION TURN" not in text:
        issues.append(
            PromptLintIssue(
                surface_key=surface_key,
                surface_label=surface_label,
                severity="warning",
                code="decision_cli_lookup_clarity",
                message="Decision contract shows CLI lookup JSON without explicitly distinguishing it from the final conversation act list.",
            )
        )

    return issues


def _lint_saved_prompt_mismatch(
    surface_key: str,
    surface_label: str,
    source_kind: str,
    text: str,
) -> list[PromptLintIssue]:
    """Warn when one saved prompt surface no longer matches the shipped default file."""
    if source_kind != "setting":
        return []
    try:
        default_text = load_default_prompt(surface_key)
    except KeyError:
        return []
    if text == default_text:
        return []
    return [
        PromptLintIssue(
            surface_key=surface_key,
            surface_label=surface_label,
            severity="warning",
            code="saved_prompt_differs_from_default",
            message=(
                "Saved prompt text differs from the current shipped default. "
                "Review whether this customization is still intended."
            ),
        )
    ]
