"""Shared runtime core injected on every agent turn.

Hire stays short (Name / Specialty / Description). Role-specific quality
bars live in Description. This block is the shared operational contract:
identity, desk/``/me``, allowed tools, host-path consent, workspace
preference, checkable done, audience soft-judgment, and chat formatting.
Fan-out still wakes every member; this is not a router and does not
require @.
"""

from __future__ import annotations

from core.bm_cli.host_roots import configured_host_roots
from core.models import Agent
from core.models.host_path_consent import HostPathConsentRequest

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

AUDIENCE_SOFT_JUDGMENT = (
    "Before you reply, decide if you're the intended audience. "
    "If someone else's specialty clearly fits, stay quiet or post one short pass."
)

CHAT_FORMATTING = (
    "Chat replies: use short paragraphs with real newlines. "
    "Use markdown lists for plans and steps. "
    "Prefer readable formatting over one dense brick. "
    "No hard length limit."
)

LOCKED_WORKSPACE_COPY_STEER = (
    "Once the operator chooses Branch or workspace-copy, that preference stays locked for the task. "
    "Stay on the clone. Do not recommend editing the live host tree. "
    "Do not park @Operator to reopen it unless the operator explicitly overrides. "
    "The clone under /me is a real workspace: cd there and validate via cli "
    "(pytest, local git add/commit). Do not invent a desk deny. "
    "Do not invent that the desk cannot shell. "
    "Do not park @Operator as the test runner or git pusher. "
    "If Shell Executor is off, wait for the in-thread Enable/Deny card; "
    "do not park @Operator as the shell enabler. "
    "A git push approval card is not a request for the operator to run the command."
)


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


def format_runtime_core_block(agent: Agent, *, task_id: str | None = None) -> str:
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
    dest_line = _locked_copy_dest_lines(agent, task_id=task_id)
    dest_suffix = f"{dest_line}\n" if dest_line else ""
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
        "Empty done is rejected. Do not fake done. "
        "A chat assertion is not a claim — the log must show tool evidence. "
        "Thread-origin work: Done must point at a path peers can open "
        "(project/docs/ or a host path under the shared grant). "
        "/me is desk-private scratch, not a handoff.\n"
        f"{LOCKED_WORKSPACE_COPY_STEER}\n"
        f"{dest_suffix}"
        f"{AUDIENCE_SOFT_JUDGMENT}\n"
        f"{CHAT_FORMATTING}"
    )


def workspace_preference_context(
    *,
    agent_id: str,
    task_id: str | None = None,
) -> dict[str, str]:
    """Return locked Branch/workspace-copy facts for prompt workspace context."""
    rows = locked_workspace_copies_for_turn(agent_id, task_id)
    if not rows:
        return {"preference": "", "clone_dest": ""}
    row = rows[0]
    return {
        "preference": row.status,
        "clone_dest": (row.clone_dest or "").strip(),
    }


def locked_workspace_copies_for_turn(
    agent_id: str,
    task_id: str | None = None,
) -> list[HostPathConsentRequest]:
    """Return cloned/branched preferences for this agent and/or current task."""
    token = (agent_id or "").strip()
    if not token or token == "preview":
        return []
    import db

    seen: set[str] = set()
    rows: list[HostPathConsentRequest] = []
    for row in db.list_locked_workspace_copies(agent_id=token, limit=20):
        if row.id not in seen:
            seen.add(row.id)
            rows.append(row)
    task_token = (task_id or "").strip()
    if task_token:
        for row in db.list_locked_workspace_copies(task_id=task_token, limit=20):
            if row.id not in seen:
                seen.add(row.id)
                rows.append(row)
    return rows


def _locked_copy_dest_lines(agent: Agent, *, task_id: str | None) -> str:
    rows = locked_workspace_copies_for_turn(agent.id, task_id)
    if not rows:
        return ""
    return "\n".join(
        (
            f"Locked workspace copy for {row.path}: work at "
            f"{(row.clone_dest or '').strip() or '/me'}. Host writes stay blocked."
        )
        for row in rows
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
