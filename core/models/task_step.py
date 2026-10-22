"""BossMod AI — Task step models: one execution step an agent took on a task."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


# How a finished step ended. ``approval`` and ``consent`` mean it stopped to
# wait on the operator; an open step (still running, or its turn died) has none.
TaskStepOutcome = Literal["ok", "failed", "approval", "consent"]


class TaskStep(BaseModel):
    """One execution step an agent took on a task, as the ``task_steps`` row stores it.

    Written when the step starts; ``outcome``, ``result`` and ``ended_at``
    stay ``None`` until it ends, and for good when its turn died mid-step.
    ``note``, ``command`` and ``result`` are verbatim: the agent's own note,
    the CLI command it ran, and the runtime's one-line result.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    agent_id: str | None = None
    agent_name: str
    action: str
    note: str | None = None
    command: str | None = None
    outcome: TaskStepOutcome | None = None
    result: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
