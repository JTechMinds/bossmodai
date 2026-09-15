"""Tool evidence in the operator log: a chat assertion is not a claim.

Done/CLEAR for tests or proof claims must show CLI (or equivalent) in the
log. An existing artifact path is already checkable; this gate covers
review-in-prose and tests evidence that never touched a tool.
"""

from __future__ import annotations

import re

import db

MISSING_TOOL_EVIDENCE_CODE = "missing_tool_evidence"

_TOOL_ACTIONS = frozenset({"cli", "bm_cli"})
_CHAIN_SPLIT = re.compile(r"[^a-z0-9_]+")


def missing_tool_evidence_message(*, auditor: bool) -> str:
    """Operator/model-facing rejection when Done has no log tool evidence."""
    if auditor:
        return (
            "Auditor CLEAR needs tool evidence in the log. "
            "A chat assertion is not a CLEAR."
        )
    return (
        "Done needs tool evidence in the log. "
        "A chat assertion is not a checkable claim."
    )


def has_log_tool_evidence(agent_id: str) -> bool:
    """Return True when this agent has CLI/tool evidence in the log."""
    token = (agent_id or "").strip()
    if not token:
        return False
    if db.list_bm_cli_events(agent_id=token, limit=1):
        return True
    for row in db.get_diagnostics(agent_id=token, limit=50):
        if _action_has_tool(str(row.get("action_name") or "")):
            return True
    return False


def _action_has_tool(name: str) -> bool:
    """Return whether a diagnostic action label includes a tool token."""
    tokens = [part for part in _CHAIN_SPLIT.split((name or "").lower()) if part]
    return any(part in _TOOL_ACTIONS for part in tokens)
