"""BossMod AI — a background process an agent started with ``&``.

One row per launch (``agent_processes``). The row is the process's identity
for every later question: the run tag finds every process it started
(``core.bm_cli.process_containment.reap_run``), and the root pid plus its
creation time says whether it is still alive without holding a handle. The
lifecycle lives in ``core.bm_cli.background_processes``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

AgentProcessStatus = Literal["running", "exited", "stopped"]
ProcessStopper = Literal["agent", "operator", "system"]


class AgentProcess(BaseModel):
    """One stored background process.

    Attributes:
        id: The row id; its first 8 characters are what agents type.
        agent_id: The agent that started it.
        command: The pipeline as the agent wrote it (virtual paths).
        cwd: The virtual directory it started in.
        run_tag: The ``BOSSMOD_SHELL_RUN`` value every process it started carries.
        pid: The root process: the pipeline's last command.
        pid_create_time: ``psutil`` creation time of ``pid``; tells a reused pid apart.
        log_path: The real path of the file its stdout and stderr go to.
        status: ``running`` until it exits on its own (``exited``) or is
            stopped (``stopped``).
        stopped_by: Who stopped it; None unless ``stopped``.
        stop_reason: Why it ended, in plain words; None while running.
        started_at: When the launch began (the run's start).
        ended_at: When it was found ended or was stopped; None while running.
        checkin_due_at: When the operator is next asked whether to keep it.
        checkin_requested_at: When that question was asked; None while not asked.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    command: str
    cwd: str
    run_tag: str
    pid: int
    pid_create_time: float
    log_path: str
    status: AgentProcessStatus
    stopped_by: ProcessStopper | None = None
    stop_reason: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    checkin_due_at: datetime
    checkin_requested_at: datetime | None = None

    @property
    def short_id(self) -> str:
        """The 8-character prefix agents and the operator see."""
        return self.id[:8]

    @property
    def started_at_epoch(self) -> float:
        """``started_at`` as wall-clock seconds, comparable with ``psutil`` creation times."""
        return self.started_at.timestamp()
