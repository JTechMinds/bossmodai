"""BossMod AI — the per-turn reminder of what an agent left running.

Every turn, decision and execution alike, carries one short system block
listing the agent's live background processes (pipelines it ended with
``&``), so a forgotten dev server is noticed and stopped. Nothing running,
no block: the prompt prefix is then exactly what it was before the agent
ever started one.
"""

from __future__ import annotations

from typing import Any

from core.bm_cli import background_processes
from core.llm.template_engine import render_template
from core.models.agent_process import AgentProcess
from core.prompting.runtime_prompt_registry import resolve_runtime_prompt_text

PROMPT_KEY = "runtime_block_live_processes"
_ALLOWED_PATHS = {"processes"}
_SEPARATOR = " · "
_CHECKIN_NOTE = "operator asked whether to keep it"


def render_live_processes(agent_id: str, template_overrides: dict[str, str] | None) -> str | None:
    """The block for ``agent_id``'s running background processes, or None when none runs.

    One line per process, newest first: ``<id8> · <command> · <cwd> ·
    running for <duration>``, plus `` · operator asked whether to keep it``
    while a check-in waits. Rows whose process has ended are recorded as
    ended first (``background_processes.live_for_agent``).

    Args:
        agent_id: The agent the turn is for.
        template_overrides: Prompt overrides (the settings preview's), or None.

    Returns:
        The rendered block, or None when nothing is running.
    """
    rows = background_processes.live_for_agent(agent_id)
    if not rows:
        return None
    return render_template(
        resolve_runtime_prompt_text(PROMPT_KEY, template_overrides),
        {"processes": "\n".join(_line(row) for row in rows)},
        allowed_paths=_ALLOWED_PATHS,
    )


def live_processes_messages(agent_id: str, template_overrides: dict[str, str] | None) -> list[dict[str, Any]]:
    """The block as the system messages ``build_context`` appends: one, or none.

    ``build_context`` appends these right before the conversation history,
    after every stable block, so the cached prefix is unchanged while
    nothing runs.
    """
    block = render_live_processes(agent_id, template_overrides)
    return [] if block is None else [{"role": "system", "content": block}]


def _line(row: AgentProcess) -> str:
    parts = [row.short_id, row.command, row.cwd, f"running for {background_processes.running_for(row)}"]
    if row.checkin_requested_at is not None:
        parts.append(_CHECKIN_NOTE)
    return _SEPARATOR.join(parts)
