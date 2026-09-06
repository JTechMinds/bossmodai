"""Shared runtime core injected on every agent turn.

Hire stays short (Name / Specialty / Description). Role-specific quality
bars live in Description. This block is the shared operational contract:
identity, desk/``/me``, allowed tools, host-path consent, and checkable done.
"""

from __future__ import annotations

from core.bm_cli.host_roots import configured_host_roots
from core.models import Agent

ALLOWED_TOOLS = (
    "cli",
    "request_host_access",
    "work",
    "socialmsg",
    "taskmsg",
    "assign",
    "walk",
    "mtg",
    "idle",
    "wait",
    "done",
    "block",
    "deleg",
    "drop",
)

_RUNTIME_CORE_TITLE = "# Runtime core"


def preview_runtime_core(
    *,
    name: str = "",
    role: str = "",
    desk_x: int | None = None,
    desk_y: int | None = None,
) -> str:
    """Render the shared runtime core for hire Advanced preview."""
    from datetime import datetime, timezone

    agent = Agent(
        id="preview",
        storage_key="preview",
        name=(name or "").strip() or "Unnamed agent",
        role=(role or "").strip() or None,
        desk_x=desk_x,
        desk_y=desk_y,
        created_at=datetime.now(timezone.utc),
    )
    return format_runtime_core_block(agent)


def format_runtime_core_block(agent: Agent) -> str:
    """Render the shared runtime core the model must follow on every turn."""
    name = (agent.name or "").strip() or "Unnamed agent"
    specialty = (agent.role or "").strip() or "unspecified"
    desk = _desk_line(agent)
    host_roots = [str(root) for root in configured_host_roots()]
    host_line = (
        "operator-allowed host roots: " + ", ".join(host_roots)
        if host_roots
        else "no extra host roots until the operator consents on the in-chat card"
    )
    tools = ", ".join(ALLOWED_TOOLS)
    return (
        f"{_RUNTIME_CORE_TITLE}\n"
        f"You are {name} ({specialty}).\n"
        f"{desk}\n"
        f"Tools you may use: {tools}.\n"
        f"Host paths: stay inside /me, /projects, and {host_line}. "
        "If you need a path outside those roots, call request_host_access "
        "(path + reason) or attempt cli on that path — do not ask the operator "
        "for verbal yes/no. Do not invent access or claim the file exists.\n"
        "Done: complete only with a checkable claim "
        "(artifact path, tests evidence, or allow/deny proof). "
        "Empty done is rejected. Do not fake done."
    )


def _desk_line(agent: Agent) -> str:
    """Return the desk / personal-workspace line for one agent."""
    if agent.desk_x is not None and agent.desk_y is not None:
        return (
            f"Desk: assigned at ({agent.desk_x},{agent.desk_y}). "
            "/me is your personal workspace. Shared work uses /projects."
        )
    return (
        "Desk: unassigned. /me is your personal workspace. "
        "Shared work uses /projects."
    )
