"""One agent's latest measured instructions: the prompt every turn starts with."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InstructionSize(BaseModel):
    """The size of an agent's instructions, as its latest turn measured them.

    One row per agent (``agent_instruction_sizes``), replaced on every turn by
    ``core.agent_loop.turn_prompt.build_turn_prompt``. The instructions are
    every message ``build_context`` emits before the history block (system
    prompt, role, memory, task and the other turn blocks), except the
    paused-work view; the history and the message being answered are not
    part of them. The largest row of any existing agent is the reserve every
    context budget is a share of what's left after (``core.context_budget``).
    ``agent_name`` is joined from ``agents`` so Settings can say whose
    instructions the reserve is.
    """

    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    agent_name: str
    tokens: int
    measured_at: datetime
