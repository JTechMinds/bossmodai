"""BossMod AI — Model matrix routing.

Selects the appropriate LLM model for an agent based on activation
mode. Resolution order:
  1. Agent's per-mode override (e.g. ``agent.model_work``)
  2. Global setting from settings table (e.g. ``default_model_work``)
  3. None — agent cannot be activated without a configured model

No hardcoded model names. Users must configure their models.
"""

from __future__ import annotations

import logging
from typing import Literal

from core import config
from core.models import Agent

logger = logging.getLogger(__name__)

ActivationMode = Literal["social", "work", "reasoning", "extraction", "self_queue"]

# Maps activation mode → Agent model field name
_AGENT_FIELD: dict[ActivationMode, str] = {
    "social": "model_social",
    "work": "model_work",
    "reasoning": "model_reasoning",
    "extraction": "model_extraction",
    "self_queue": "model_self_queue",
}

# Maps activation mode → settings table key
_SETTINGS_KEY: dict[ActivationMode, str] = {
    "social": "default_model_social",
    "work": "default_model_work",
    "reasoning": "default_model_reasoning",
    "extraction": "default_model_extraction",
    "self_queue": "default_model_self_queue",
}


def select_model(agent: Agent, mode: ActivationMode) -> str | None:
    """Return the model identifier for the given agent and activation mode.

    Returns ``None`` if no model is configured at any level — the caller
    should skip the turn rather than guess a provider.
    """
    # 1. Agent-level override
    field = _AGENT_FIELD[mode]
    agent_model = getattr(agent, field, None)
    if agent_model:
        return agent_model

    # 2. Global setting
    settings_key = _SETTINGS_KEY[mode]
    global_model = config.get(settings_key)
    if global_model:
        return global_model

    # 3. No model configured
    logger.debug(
        "No model configured for %s/%s — agent cannot activate in this mode",
        agent.name, mode,
    )
    return None


def select_model_with_source(
    agent: Agent, mode: ActivationMode
) -> tuple[str | None, str]:
    """Return (model, source) where source is 'agent', 'global', or 'none'."""
    field = _AGENT_FIELD[mode]
    agent_model = getattr(agent, field, None)
    if agent_model:
        return agent_model, "agent"

    settings_key = _SETTINGS_KEY[mode]
    global_model = config.get(settings_key)
    if global_model:
        return global_model, "global"

    return None, "none"


def get_api_config(agent: Agent) -> dict[str, str | None]:
    """Return per-agent API configuration overrides.

    Passed to litellm to support per-agent providers.
    """
    return {
        "api_base": agent.api_base_url,
        "api_key": agent.api_key,
        "extra_body": agent.extra_body,
    }
