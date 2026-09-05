"""CLI policy rules, approvals, and simulator."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.websocket import manager
from core.bm_cli.approvals import resume_cli_approval
from core.runtime import runtime_services
import db

router = APIRouter()


class CliPolicyRuleBody(BaseModel):
    tier: str
    pattern: str
    match_mode: str = "prefix"
    agent_id: str | None = None
    description: str | None = None
    category: str = "general"
    usage_syntax: str | None = None
    help_text: str | None = None
    enabled: bool = True
    priority: int = 0


class CliPolicyRuleUpdateBody(BaseModel):
    tier: str | None = None
    pattern: str | None = None
    match_mode: str | None = None
    agent_id: str | None = None
    description: str | None = None
    category: str | None = None
    usage_syntax: str | None = None
    help_text: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class CliPolicySimulateBody(BaseModel):
    command: str
    agent_id: str | None = None


class CliSimulatorExecuteBody(BaseModel):
    command: str
    agent_id: str
    content: str | None = None
    dry_run: bool = True
    execute: bool = False


class CliApprovalDecisionBody(BaseModel):
    decision_note: str | None = None


# ─── CLI Policy ───

@router.get("/cli-policy/virtual-commands")
async def list_virtual_commands():
    """Return the read-only virtual command registry for the UI."""
    from core.bm_cli.command_registry import (
        VIRTUAL_COMMAND_REGISTRY,
        VIRTUAL_CATEGORIES,
    )

    commands = [
        {
            "name": cmd.name,
            "category": cmd.category,
            "description": cmd.description,
            "usage_syntax": cmd.usage_syntax,
            "help_text": cmd.help_text,
        }
        for cmd in VIRTUAL_COMMAND_REGISTRY.values()
    ]

    categories = [
        {"name": k, "description": v}
        for k, v in VIRTUAL_CATEGORIES.items()
    ]

    return {"commands": commands, "categories": categories}


# Rules CRUD
@router.get("/cli-policy/rules")
async def list_cli_policy_rules(tier: str | None = None, agent_id: str | None = None):
    return db.list_cli_policy_rules(tier=tier, agent_id=agent_id)


@router.post("/cli-policy/rules", status_code=201)
async def create_cli_policy_rule(body: CliPolicyRuleBody):
    # Validate tier
    if body.tier not in ("never_allowed", "always_allowed", "approval_required"):
        raise HTTPException(400, f"Invalid tier: {body.tier}")
    if body.match_mode not in ("exact", "prefix", "glob"):
        raise HTTPException(400, f"Invalid match_mode: {body.match_mode}")
    if not body.pattern.strip():
        raise HTTPException(400, "Pattern cannot be empty")
    rule = db.create_cli_policy_rule(
        tier=body.tier,
        pattern=body.pattern.strip(),
        match_mode=body.match_mode,
        agent_id=body.agent_id,
        description=body.description,
        category=body.category,
        usage_syntax=body.usage_syntax,
        help_text=body.help_text,
        enabled=body.enabled,
        priority=body.priority,
    )
    # Invalidate policy engine cache
    from core.bm_cli.policy_engine import policy_engine
    policy_engine.reload()
    return rule


@router.put("/cli-policy/rules/{rule_id}")
async def update_cli_policy_rule(rule_id: str, body: CliPolicyRuleUpdateBody):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "tier" in fields and fields["tier"] not in ("never_allowed", "always_allowed", "approval_required"):
        raise HTTPException(400, f"Invalid tier: {fields['tier']}")
    if "match_mode" in fields and fields["match_mode"] not in ("exact", "prefix", "glob"):
        raise HTTPException(400, f"Invalid match_mode: {fields['match_mode']}")
    updated = db.update_cli_policy_rule(rule_id, **fields)
    if updated is None:
        raise HTTPException(404, "Rule not found")
    from core.bm_cli.policy_engine import policy_engine
    policy_engine.reload()
    return updated


@router.delete("/cli-policy/rules/{rule_id}")
async def delete_cli_policy_rule(rule_id: str):
    deleted = db.delete_cli_policy_rule(rule_id)
    if not deleted:
        raise HTTPException(404, "Rule not found")
    from core.bm_cli.policy_engine import policy_engine
    policy_engine.reload()
    return {"ok": True}


@router.post("/cli-policy/rules/seed-defaults")
async def seed_cli_policy_rules():
    # Delete all existing rules first, then re-seed. Existing approval
    # requests retain history but drop their matched_rule_id reference.
    with db.transaction():
        db.execute("UPDATE cli_approval_requests SET matched_rule_id = NULL WHERE matched_rule_id IS NOT NULL")
        db.execute("DELETE FROM cli_policy_rules")
        db.seed_default_cli_policy_rules()
    from core.bm_cli.policy_engine import policy_engine
    policy_engine.reload()
    return {"ok": True, "message": "Default rules re-seeded"}


# Approvals
@router.get("/cli-policy/approvals")
async def list_cli_approval_requests(status: str | None = None, agent_id: str | None = None, limit: int = 50):
    return db.list_cli_approval_requests(status=status, agent_id=agent_id, limit=min(limit, 200))


@router.post("/cli-policy/approvals/{request_id}/approve")
async def approve_cli_request(request_id: str):
    approval = await resume_cli_approval(
        request_id,
        approved=True,
        services=runtime_services,
    )
    if approval is None:
        raise HTTPException(404, "Approval request not found or already resolved")
    await manager.broadcast_activity(
        event="cli_approval_approved",
        detail=f"Command approved: {approval.command}",
    )
    return approval


@router.post("/cli-policy/approvals/{request_id}/reject")
async def reject_cli_request(request_id: str, body: CliApprovalDecisionBody | None = None):
    note = body.decision_note if body else None
    rejection = await resume_cli_approval(
        request_id,
        approved=False,
        note=note,
        services=runtime_services,
    )
    if rejection is None:
        raise HTTPException(404, "Approval request not found or already resolved")
    await manager.broadcast_activity(
        event="cli_approval_rejected",
        detail=f"Command rejected: {rejection.command}" + (f" — {note}" if note else ""),
    )
    return rejection


# Simulator — policy dry-run (lightweight check without execution)
@router.post("/cli-policy/simulate")
async def simulate_cli_policy(body: CliPolicySimulateBody):
    from core.bm_cli.policies import evaluate_command_policy
    from core.bm_cli.runtime import VIRTUAL_COMMANDS
    command = body.command.strip()
    if not command:
        raise HTTPException(400, "Command cannot be empty")
    try:
        decision = evaluate_command_policy(command, VIRTUAL_COMMANDS, body.agent_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    matched_rule = None
    if decision.matched_rule_id:
        matched_rule = db.get_cli_policy_rule(decision.matched_rule_id)
    return {
        "command": command,
        "agent_id": body.agent_id,
        "decision": {
            "allowed": decision.allowed,
            "tier": decision.tier,
            "executor": decision.executor,
            "approval_required": decision.approval_required,
            "message": decision.message,
            "matched_rule_id": decision.matched_rule_id,
        },
        "matched_rule": matched_rule,
    }


def _simulator_executes_for_real(body: CliSimulatorExecuteBody) -> bool:
    """Real execution requires an explicit execute=true (or dry_run=false)."""
    if body.execute:
        return True
    if body.dry_run is False:
        return True
    return False


# Simulator — default dry-run; explicit execute=true runs the real pipeline
@router.post("/cli-policy/simulator/execute")
async def simulator_execute(body: CliSimulatorExecuteBody):
    """Preview or execute a command through the BM_CLI pipeline as an agent.

    Default is dry-run (parse + policy only). Writes and shell require
    ``execute=true`` (or ``dry_run=false``). Approval-required commands still
    return the approval gate without creating a request on dry-run.
    """
    from core.bm_cli.runtime import execute_bm_cli, preview_bm_cli

    if not body.command.strip():
        raise HTTPException(400, "Command cannot be empty")

    agent = db.get_agent(body.agent_id)
    if agent is None:
        raise HTTPException(404, f"Agent not found: {body.agent_id}")

    state = db.get_agent_state(body.agent_id)
    if state is None:
        raise HTTPException(404, f"Agent state not found: {body.agent_id}")

    execute_for_real = _simulator_executes_for_real(body)
    if execute_for_real:
        cli_result = execute_bm_cli(
            agent,
            state,
            body.command.strip(),
            body.content,
            trigger_type="simulator",
        )
    else:
        cli_result = preview_bm_cli(
            agent,
            state,
            body.command.strip(),
            body.content,
        )

    return {
        "command": cli_result.command,
        "ok": cli_result.ok,
        "exit_code": cli_result.exit_code,
        "executor": cli_result.executor,
        "kind": cli_result.kind,
        "output": cli_result.prompt_content,
        "detail": cli_result.detail,
        "cwd": cli_result.cwd,
        "approval_required": cli_result.approval_required,
        "approval_request_id": cli_result.approval_request_id,
        "matched_rule_id": cli_result.matched_rule_id,
        "dry_run": not execute_for_real,
    }
