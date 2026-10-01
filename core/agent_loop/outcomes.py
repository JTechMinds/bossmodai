"""BossMod AI — Structured turn outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


TriggerExecutionStatus = Literal["completed", "failed", "skipped"]
DiagnosticStatus = Literal["success", "error", "skipped"]


@dataclass(slots=True)
class TurnOutcome:
    """Structured result of a trigger execution.

    ``retryable`` only matters on a failure: the dispatcher retries a failed
    trigger only when it is ``True``, i.e. when a retry cannot replay side
    effects the failed turn already had.
    """

    result: dict[str, Any] = field(default_factory=dict)
    trigger_status: TriggerExecutionStatus = "completed"
    diagnostic_status: DiagnosticStatus = "success"
    diagnostic_error: str | None = None
    action: dict[str, Any] | None = None
    action_summary: str = ""
    raw_response: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)
    retryable: bool = True

    @classmethod
    def success(
        cls,
        *,
        result: dict[str, Any],
        action: dict[str, Any] | None,
        action_summary: str,
        raw_response: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        steps: list[dict[str, Any]] | None = None,
    ) -> "TurnOutcome":
        return cls(
            result=result,
            trigger_status="completed",
            diagnostic_status="success",
            action=action,
            action_summary=action_summary,
            raw_response=raw_response,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            steps=list(steps or []),
        )

    @classmethod
    def failure(
        cls,
        *,
        retryable: bool,
        result: dict[str, Any],
        error: str,
        action: dict[str, Any] | None,
        action_summary: str,
        raw_response: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        steps: list[dict[str, Any]] | None = None,
    ) -> "TurnOutcome":
        """A failed turn; the trigger is retried only when ``retryable``.

        Args:
            retryable: Required, so every failure site decides: ``True`` only
                when a retry cannot replay external side effects (nothing
                ran yet, or the transcript was frozen for resumption).
            result: The event result broadcast for the failure.
            error: The diagnostic error text.
            action: The last parsed action, if any.
            action_summary: The turn's action chain.
            raw_response: The last LLM response.
            prompt_tokens: Prompt tokens spent this turn.
            completion_tokens: Completion tokens spent this turn.
            total_tokens: Total tokens spent this turn.
            steps: The step traces.

        Returns:
            A ``failed``/``error`` outcome.
        """
        return cls(
            result=result,
            trigger_status="failed",
            diagnostic_status="error",
            diagnostic_error=error,
            action=action,
            action_summary=action_summary,
            raw_response=raw_response,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            steps=list(steps or []),
            retryable=retryable,
        )

    @classmethod
    def skipped(
        cls,
        *,
        result: dict[str, Any],
        error: str,
        steps: list[dict[str, Any]] | None = None,
    ) -> "TurnOutcome":
        return cls(
            result=result,
            trigger_status="skipped",
            diagnostic_status="skipped",
            diagnostic_error=error,
            action_summary="skipped",
            steps=list(steps or []),
        )
