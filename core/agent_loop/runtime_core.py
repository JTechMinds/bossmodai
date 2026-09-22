"""Shared runtime core injected on every agent turn.

Hire stays short (Name / Specialty / Description). Role-specific quality
bars live in Description. This block is the shared operational contract:
identity, desk/``/me``, cold notes, standing prefs, allowed tools, host-path
consent, workspace preference, checkable done, audience soft-judgment,
and chat formatting.
Channel discuss wakes run in rounds. System AI may choose who is woken
before this prompt runs. This prompt is not that route, and a pass does
not require @.
"""

from __future__ import annotations

from core.agent_loop.standing_prefs import STANDING_PREFS_PATH
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
    "Before you reply, choose speak or pass. "
    "Speak only when this wake is for you; one line is enough, not an essay. "
    "Pass is engine-side: use observe and do not post a chat message. "
    "Do not write that you will stay quiet."
)

# Talk / 1:1 status / channel only — bias, not a hard require. Work stays quiet.
SAY_WITH_ACTIONS = (
    "When acting on a human ask, include a non-empty say with the actions. "
    "Bias only — no essay acks or \"Copy that.\""
)

# Cold notes stay on demand. Standing prefs are a separate warm store.
NOTES_STORE_RETRIEVE = (
    "Notes (cold): Personal: /me/notes. "
    "Project: /projects/<project>/. "
    "Open on demand for how-to. Pointers-first. Never dump. "
    "Never invent Board/Done from note text.\n"
    "Standing prefs (warm): "
    f"Agent prefs store {STANDING_PREFS_PATH} (not notes). "
    "Kinds: preference / constraint / style / tool_bias. "
    "On a clear operator statement, write a short sticky + sources there. "
    "Engine injects those pointers every work turn — "
    "the agent does not re-open prefs for inject. "
    "Supersede only when the operator replaces. "
    "Optional note pointer for prose — warm inject does not scrape notes. "
    "Invent-key / Board fakes still fail-closed."
)

CHAT_FORMATTING = (
    "Chat replies must emit this shape.\n"
    "\n"
    "Write short paragraphs. Separate them with real newline characters.\n"
    "\n"
    "- Use markdown lists for plans, steps, and findings.\n"
    "- Do not emit one dense brick or wall of text.\n"
    "- Do not flatten a reply onto a single line.\n"
    "\n"
    "No hard length limit."
)

LOCKED_WORKSPACE_COPY_STEER = (
    "Once the operator chooses Branch or workspace-copy, that preference stays locked for the task. "
    "Stay on the clone. Do not recommend editing the live host tree. "
    "Do not park @Operator to reopen it unless the operator explicitly overrides. "
    "The clone under /me is a real workspace: cd there and validate via cli "
    "(uv run pytest, .venv/bin/pytest, or pytest; local git add/commit). "
    "Do not pip install into the host Python — use uv pip / uv add / .venv/bin/pip "
    "on the clone, or wait for a project-local install card. "
    "Do not invent a desk deny. "
    "Do not invent that the desk cannot shell. "
    "Do not park @Operator as the test runner or git pusher. "
    "If Shell Executor is off, wait for the in-thread Enable/Deny card; "
    "do not park @Operator as the shell enabler. "
    "If nest git needs credentials, wait for Enable host git or Add PAT/SSH "
    "(Settings → Nest git). Always-allow does not skip auth. "
    "Do not invent that browser or desktop GitHub login is the agent's. "
    "Do not park @Operator as the git enabler. "
    "A git push approval card is not a request for the operator to run the command. "
    "Do not claim an Approve or consent card is live unless the cli result shows "
    "a request id."
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
        f"{NOTES_STORE_RETRIEVE}\n"
        f"{LOCKED_WORKSPACE_COPY_STEER}\n"
        f"{dest_suffix}"
        f"{AUDIENCE_SOFT_JUDGMENT}\n"
        f"{SAY_WITH_ACTIONS}\n"
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
