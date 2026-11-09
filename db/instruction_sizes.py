"""BossMod AI — Measured agent instruction sizes (``agent_instruction_sizes``).

One row per agent: the tokens of the instructions its latest turn sent (every
prompt message before the history, except the paused-work view), written by
``core/agent_loop/turn_prompt.py`` on every turn. The largest row of an
existing agent is the reserve ``core/context_budget.py`` subtracts from the
context window before it shares out the rest, and Settings names that agent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.models.instruction_size import InstructionSize
from db.crud import execute, fetch_one


def record_instruction_size(agent_id: str, tokens: int) -> None:
    """Store an agent's latest instruction measurement, replacing its last one.

    ``measured_at`` is set to now, so Settings can show how old the
    measurement behind the reserve is.

    Args:
        agent_id: The agent whose turn measured its instructions.
        tokens: The measured instructions, in tokens.

    Raises:
        ValueError: ``agent_id`` is empty or ``tokens`` is negative.
    """
    if not str(agent_id or "").strip():
        raise ValueError("An instruction size needs the agent it was measured for.")
    if tokens < 0:
        raise ValueError(f"An instruction size cannot be negative ({tokens}).")
    execute(
        """
        INSERT INTO agent_instruction_sizes (agent_id, tokens, measured_at)
        VALUES ($1, $2, $3)
        ON CONFLICT(agent_id) DO UPDATE SET
            tokens = excluded.tokens,
            measured_at = excluded.measured_at
        """,
        [agent_id, int(tokens), datetime.now(timezone.utc)],
    )


def largest_instruction_size() -> InstructionSize | None:
    """Return the largest stored instruction measurement of an existing agent.

    Joined with ``agents`` for the name Settings shows, so a row left by an
    agent that no longer exists never counts. A tie goes to the newest
    measurement.

    Returns:
        The largest measurement, or ``None`` when no existing agent has one
        yet (the reserve is then 0: "not measured yet").
    """
    return fetch_one(
        """
        SELECT sizes.agent_id, agents.name AS agent_name, sizes.tokens, sizes.measured_at
        FROM agent_instruction_sizes AS sizes
        JOIN agents ON agents.id = sizes.agent_id
        ORDER BY sizes.tokens DESC, sizes.measured_at DESC
        LIMIT 1
        """,
        [],
        InstructionSize,
    )
