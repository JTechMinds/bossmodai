"""Fail-closed steer when a turn emits prose or an invalid decision object.

Prose status and unknown top-level decision keys are not spinning actions
and must not loop. Soft-block behavior is unchanged — this only stops
parse-failure from feeding it.
"""

from __future__ import annotations

from typing import Any, Literal

ParseFailureKind = Literal["prose_status", "invalid_json", "invalid_decision"]

PROSE_STATUS_STEER = (
    "Emit the required JSON decision/action shape for this turn. "
    "Prose status is not a valid turn result. "
    "Do not park @Operator. Do not invent a desk deny."
)

INVALID_DECISION_STEER = (
    "Emit the required JSON decision/action shape for this turn. "
    "Do not invent approval fields. "
    "Approval is an in-thread Approve/Reject card, not a decision key. "
    "Do not park @Operator. Do not invent a desk deny."
)


def classify_json_parse_failure(raw_response: str) -> ParseFailureKind:
    """Return ``prose_status`` when the model emitted no JSON object."""
    text = _strip_fences(raw_response)
    if "{" not in text:
        return "prose_status"
    return "invalid_json"


def kind_for_schema_error(error: str) -> ParseFailureKind:
    """Unknown top-level keys are an invalid decision, not malformed JSON."""
    if "unexpected top-level keys" in (error or ""):
        return "invalid_decision"
    return "invalid_json"


def parse_failure_should_repair(
    *,
    kind: str,
    repair_attempts: int,
    max_repairs: int,
) -> bool:
    """Prose status and invalid decisions fail-close. Malformed JSON may repair."""
    if kind in {"prose_status", "invalid_decision"}:
        return False
    return repair_attempts < max_repairs


def parse_failure_steer(kind: str, snippet: str = "") -> str:
    """Return the fail-closed steer for a parse failure."""
    extra = (snippet or "").strip()
    if kind == "invalid_decision":
        base = INVALID_DECISION_STEER
    else:
        base = PROSE_STATUS_STEER
    if kind == "prose_status":
        return base
    if extra:
        return f"{base} Parser error: {extra}"
    return base


def parse_failed_payload(
    raw_response: str,
    *,
    decision: bool,
    snippet: str | None = None,
    kind: ParseFailureKind | None = None,
    thought: str = "",
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the structured ``_parse_failed`` payload used by both contracts."""
    resolved = kind or classify_json_parse_failure(raw_response)
    text = (snippet if snippet is not None else raw_response)[:200]
    if decision:
        payload: dict[str, Any] = {
            "decision": "_parse_failed",
            "thought": thought,
            "_raw_snippet": text,
            "_parse_kind": resolved,
        }
    else:
        if not thought:
            thought = (
                "No action in response" if resolved == "prose_status" else "Failed to parse response"
            )
        payload = {
            "action": "_parse_failed",
            "thought": thought,
            "_raw_snippet": text,
            "_parse_kind": resolved,
        }
    if candidate is not None:
        payload["_candidate_payload"] = candidate
    return payload


def _strip_fences(raw_response: str) -> str:
    text = (raw_response or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    lines = [line for line in lines if not line.strip().startswith("```")]
    return "\n".join(lines).strip()
