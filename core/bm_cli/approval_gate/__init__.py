"""Approval gate for ``approval_required`` shell commands.

``facts`` computes what code can know about a command, ``context`` gathers
the work around it, ``review`` asks System AI, and ``gate`` orchestrates
them behind the invariants. Callers use only the names re-exported here.
"""

from core.bm_cli.approval_gate.gate import (
    AUDIT_PREFIX,
    CLI_AUTO_APPROVED_EVENT,
    GLOBAL_AUTO_APPROVE_SETTING,
    SYSTEM_DECISION_BY,
    AutoApprovePlan,
    attach_system_audit,
    audit_line,
    auto_approve_effective,
    global_auto_approve_enabled,
    log_system_auto_approve,
    plan_auto_approve,
)

__all__ = [
    "AUDIT_PREFIX",
    "CLI_AUTO_APPROVED_EVENT",
    "GLOBAL_AUTO_APPROVE_SETTING",
    "SYSTEM_DECISION_BY",
    "AutoApprovePlan",
    "attach_system_audit",
    "audit_line",
    "auto_approve_effective",
    "global_auto_approve_enabled",
    "log_system_auto_approve",
    "plan_auto_approve",
]
