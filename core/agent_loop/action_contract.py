"""BossMod AI — Execution contract prompt access."""

from __future__ import annotations

from core import config
from core.default_prompts import load_default_prompt
from core.llm.template_engine import render_template

_CONTRACT_ALLOWED_PATHS = {"cli.shell_enabled"}


def default_action_contract_template() -> str:
    """Return the default authored execution contract template."""
    return load_default_prompt("runtime_contract_execution")


def render_action_contract() -> str:
    """Render the execution contract with current runtime-aware prompt values."""
    return render_template(
        default_action_contract_template(),
        _contract_render_context(),
        allowed_paths=_CONTRACT_ALLOWED_PATHS,
    )


def _contract_render_context() -> dict[str, object]:
    """Return the minimal template context needed by the execution contract."""
    return {
        "cli": {
            "shell_enabled": config.get("cli_shell_enabled") == "true",
        }
    }
