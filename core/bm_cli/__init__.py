"""BossMod AI — Controlled BossMod CLI surface for agents."""

from core.bm_cli.contract import BossModCliCall, maybe_parse_bm_cli_call, render_bm_cli_guidance
from core.bm_cli.runtime import BossModCliResult, execute_bm_cli

__all__ = [
    "BossModCliCall",
    "BossModCliResult",
    "execute_bm_cli",
    "maybe_parse_bm_cli_call",
    "render_bm_cli_guidance",
]
