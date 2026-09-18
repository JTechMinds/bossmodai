"""Fail-closed steer when a turn emits prose instead of decision JSON.

Prose status is not a spinning action and must not loop. Soft-block
behavior is unchanged — this only stops parse-failure from feeding it.
"""

from __future__ import annotations

from typing import Literal

ParseFailureKind = Literal["prose_status", "invalid_json"]

PROSE_STATUS_STEER = (
    "Emit the required JSON decision/action shape for this turn. "
    "Prose status is not a valid turn result. "
    "Do not park @Operator. Do not invent a desk deny."
)


def classify_json_parse_failure(raw_response: str) -> ParseFailureKind:
    """Return ``prose_status`` when the model emitted no JSON object."""
    text = _strip_fences(raw_response)
    if "{" not in text:
        return "prose_status"
    return "invalid_json"


def parse_failure_should_repair(
    *,
    kind: str,
    repair_attempts: int,
    max_repairs: int,
) -> bool:
    """Prose status fail-closes immediately. Malformed JSON may repair."""
    if kind == "prose_status":
        return False
    return repair_attempts < max_repairs


def parse_failure_steer(kind: str, snippet: str = "") -> str:
    """Return the fail-closed steer for a parse failure."""
    extra = (snippet or "").strip()
    if kind == "prose_status":
        return PROSE_STATUS_STEER
    if extra:
        return f"{PROSE_STATUS_STEER} Parser error: {extra}"
    return PROSE_STATUS_STEER


def parse_failed_payload(
    raw_response: str,
    *,
    decision: bool,
    snippet: str | None = None,
) -> dict[str, str]:
    """Build the structured ``_parse_failed`` payload used by both contracts."""
    kind = classify_json_parse_failure(raw_response)
    text = (snippet if snippet is not None else raw_response)[:200]
    if decision:
        return {
            "decision": "_parse_failed",
            "thought": "",
            "_raw_snippet": text,
            "_parse_kind": kind,
        }
    thought = "No action in response" if kind == "prose_status" else "Failed to parse response"
    return {
        "action": "_parse_failed",
        "thought": thought,
        "_raw_snippet": text,
        "_parse_kind": kind,
    }


def _strip_fences(raw_response: str) -> str:
    text = (raw_response or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    lines = [line for line in lines if not line.strip().startswith("```")]
    return "\n".join(lines).strip()
