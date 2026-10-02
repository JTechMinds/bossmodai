"""BossMod AI — Model routing.

An agent has one AI connection (``agent.connection_id``), read live on every
turn so the request uses exactly what the Connections screen shows. The only
thing that varies by activation mode is the thinking level, which picks a
fragment from the connection's level map and merges it over its extra body.

No hardcoded model names and no global fallback: an agent without a usable
connection does not run, and the reason says why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from core.llm.thinking import ThinkingConfigError, effective_extra_body
from core.models import Agent
import db

logger = logging.getLogger(__name__)

# Only the activations the runtime routes (turn_context._determine_mode).
ActivationMode = Literal["social", "work"]


@dataclass(frozen=True)
class ModelRoute:
    """Everything one model call needs from the agent's connection."""

    connection_id: str
    model: str
    api_base: str | None
    api_key: str | None
    extra_body: str | None


class RouteUnavailable(Exception):
    """The agent cannot be routed this turn; ``reason`` names the cause."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def resolve_route(agent: Agent, mode: ActivationMode) -> ModelRoute:
    """Resolve the agent's connection into a route for one activation.

    Args:
        agent: The agent taking the turn.
        mode: The activation, which selects ``thinking_social`` or
            ``thinking_work``.

    Returns:
        The connection's model, endpoint and key, with the extra body for the
        agent's thinking choice in this mode.

    Raises:
        RouteUnavailable: The agent has no connection, the connection no
            longer exists, its model is blank, or the thinking choice cannot
            be applied (unoffered level, extra body not a JSON object).
    """
    if not agent.connection_id:
        raise RouteUnavailable("no AI connection")
    conn = db.get_connection_by_id(agent.connection_id)
    if conn is None:
        raise RouteUnavailable(f"AI connection {agent.connection_id} no longer exists")
    model = (conn.model or "").strip()
    if not model:
        raise RouteUnavailable(f"AI connection '{conn.name}' has no model")
    choice = getattr(agent, f"thinking_{mode}")
    try:
        extra_body = effective_extra_body(conn.extra_body, conn.thinking_levels, choice)
    except ThinkingConfigError as exc:
        raise RouteUnavailable(f"AI connection '{conn.name}', {mode} thinking: {exc}") from exc
    return ModelRoute(
        connection_id=conn.id,
        model=model,
        api_base=conn.api_base_url,
        api_key=conn.api_key,
        extra_body=extra_body,
    )


def agent_model(agent: Agent) -> str | None:
    """Return the model of the agent's linked connection, for name-only callers.

    Returns:
        The connection's model, or None when the agent is unlinked, the
        connection is gone, or its model is blank.
    """
    if not agent.connection_id:
        return None
    conn = db.get_connection_by_id(agent.connection_id)
    if conn is None:
        return None
    return (conn.model or "").strip() or None
