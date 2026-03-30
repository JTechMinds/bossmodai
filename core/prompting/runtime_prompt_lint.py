"""BossMod AI — Lint model-facing prompt surfaces for contract drift."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Mapping

from .runtime_prompt_registry import collect_runtime_prompt_texts, runtime_prompt_surface_map


PromptSeverity = Literal["warning", "error"]

_LEGACY_TOKEN_RULES: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (
        re.compile(r"\bbm_cli\b"),
        "error",
        "legacy_bm_cli",
        'Use the model-facing act `cli`, not the internal runtime name `bm_cli`.',
    ),
    (
        re.compile(r"\bwalkTo\b"),
        "error",
        "legacy_walkto",
        'Use the model-facing act `walk`, not the legacy runtime action name `walkTo`.',
    ),
    (
        re.compile(r"\bdelegateTask\b"),
        "error",
        "legacy_delegate_task",
        'Use the model-facing act `assign`, not the legacy runtime action name `delegateTask`.',
    ),
    (
        re.compile(r"\bstartTask\b"),
        "error",
        "legacy_start_task",
        'Use the compact decision/execution contract instead of legacy `startTask` actions.',
    ),
    (
        re.compile(r"\bresumeTask\b"),
        "error",
        "legacy_resume_task",
        'Use the compact execution contract instead of legacy `resumeTask` actions.',
    ),
)

_LEGACY_LIFECYCLE_PATTERN = re.compile(
    r"complete\s*[/,]\s*blocked\s*[/,]\s*delegated\s*[/,]\s*abandoned",
    re.IGNORECASE,
)
_LEGACY_THOUGHT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'[`"]thought[`"]'),
    re.compile(r'"th"\s+is\s+thought', re.IGNORECASE),
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

    for surface_key, text in collect_runtime_prompt_texts(overrides).items():
        surface = surfaces[surface_key]
        issues.extend(_lint_surface(surface_key, surface.label, text))

    return PromptLintReport(issues=tuple(issues))


def _lint_surface(surface_key: str, surface_label: str, text: str) -> list[PromptLintIssue]:
    issues: list[PromptLintIssue] = []

    for pattern, severity, code, message in _LEGACY_TOKEN_RULES:
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

    if _LEGACY_LIFECYCLE_PATTERN.search(text):
        issues.append(
            PromptLintIssue(
                surface_key=surface_key,
                surface_label=surface_label,
                severity="error",
                code="legacy_terminal_acts",
                message='Use model-facing terminal acts `done`, `block`, `deleg`, and `drop` instead of legacy lifecycle names.',
            )
        )

    if any(pattern.search(text) for pattern in _LEGACY_THOUGHT_PATTERNS):
        issues.append(
            PromptLintIssue(
                surface_key=surface_key,
                surface_label=surface_label,
                severity="error",
                code="legacy_thought_key",
                message='Use the model-facing key `th` for the short admin note. Do not instruct the model to emit `thought`.',
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
