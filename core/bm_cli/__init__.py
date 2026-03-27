"""BossMod AI — Controlled BossMod CLI surface for agents."""

from core.bm_cli.contract import BossModCliCall, maybe_parse_bm_cli_call, render_bm_cli_guidance
from core.bm_cli.policy_engine import CommandPolicyDecision, policy_engine
from core.bm_cli.runtime import VIRTUAL_COMMANDS, execute_approved_command, execute_bm_cli
from core.bm_cli.types import BossModCliResult

__all__ = [
    "BossModCliCall",
    "BossModCliResult",
    "CommandPolicyDecision",
    "VIRTUAL_COMMANDS",
    "execute_approved_command",
    "execute_bm_cli",
    "maybe_parse_bm_cli_call",
    "policy_engine",
    "render_bm_cli_guidance",
]
