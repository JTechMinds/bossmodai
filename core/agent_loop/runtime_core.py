"""Shared runtime core injected on every agent turn.

Hire stays short (Name / Specialty / Description). Role-specific quality
bars live in Description. This block is the shared operational contract:
identity, desk/``/me``, memory, project knowledge and notes, allowed tools,
host-path consent, workspace preference, checkable done, audience
soft-judgment, and chat formatting.
Channel discuss wakes run in rounds. System AI may choose who is woken
before this prompt runs. This prompt is not that route, and a pass does
not require @.
"""

from __future__ import annotations

from core.agent_loop import standing_prefs
from core.boss import boss_label, boss_mention
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
    "The recent thread is already in this prompt. "
    "When nothing changed, prefer an engine pass over a status essay. "
    "Repeating that thread is not new work. "
    "Pass is engine-side: use observe and do not post a chat message. "
    "Do not write that you will stay quiet."
)

# Talk / 1:1 status / channel only — bias, not a hard require. Work stays quiet.
SAY_WITH_ACTIONS = (
    "When acting on an ask from the boss, include a non-empty say with the actions. "
    "Bias only — no essay acks or \"Copy that.\""
)

# The size memory guidance asks for. Prompt copy only, never enforced: the
# agent judges, and the system enforces only the operator's hard limit
# (``standing_prefs.line_max_chars``).
MEMORY_SOFT_TARGET_CHARS = 120


def format_memory_guidance() -> str:
    """Render where the agent keeps what it is told: memory, project knowledge, notes.

    Memory is the only home shown on every turn, so it is the only one that
    must stay small. The hard limit is read live from Settings, so the number
    the prompt states and the number a save enforces cannot drift.

    Returns:
        The guidance paragraph for the runtime core.

    Raises:
        config.ConfigError: ``standing_prefs_line_max_chars`` is missing,
            not an integer, or below 1.
    """
    hard = standing_prefs.line_max_chars()
    return (
        "Memory: the few things you must never forget. Your memory is shown to you on every "
        "turn, so keep it to small guidance thoughts — important, broadly useful facts about "
        "the boss, the company, clients, systems, and how the boss wants things done. "
        "Not project details.\n"
        f"- Keep each memory to 1–2 short sentences (about {MEMORY_SOFT_TARGET_CHARS} characters; "
        f"at most {hard}).\n"
        "- When someone tells you something like that, save it in this turn: in a reply, put the "
        "sentence in `remember`; while working, `memory add` with the sentence in the body. "
        "The system numbers it.\n"
        "- When it changes or stops being true: `memory replace <n>` with the new sentence in "
        "the body, or `memory remove <n>`. `memory list` shows everything.\n"
        "- Project knowledge (decisions, requirements, contacts, facts about one project) goes "
        "in /projects/<project>/project_knowledge.md. Add to it; don't overwrite unless you "
        "mean to. Your floor reads it. Read it before working on that project. This is not "
        "task work; do it in any turn.\n"
        "- /me/notes is for things you need rarely but long term: a preferred process, a "
        "how-to, scratch work. Not shown automatically; open it when you need it.\n"
        "Save what lasts; skip small talk, one-off instructions for the current task, and "
        "anything already saved. The boss is told automatically when you save; you don't need "
        "to mention it. Saving is never task progress or Done. A correction to how you do "
        "your work, including a recurring job, is lasting, not a one-off."
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
    "Once the boss chooses Branch or workspace-copy, that host folder stays locked "
    "while your copy of it exists. "
    "Stay on the clone. Do not recommend editing the live host tree. "
    "Do not park @Boss to reopen it unless the boss explicitly overrides. "
    "The clone under /me is a real workspace: cd there and validate via cli "
    "(uv run pytest, .venv/bin/pytest, or pytest; local git add/commit). "
    "Do not pip install into the host Python — use uv pip / uv add / .venv/bin/pip "
    "on the clone, or wait for a project-local install card. "
    "Do not invent a desk deny. "
    "Do not invent that the desk cannot shell. "
    "Do not park @Boss as the test runner or git pusher. "
    "If Shell Executor is off, wait for the in-thread Enable/Deny card; "
    "do not park @Boss as the shell enabler. "
    "If nest git needs credentials, wait for Enable host git or Add PAT/SSH "
    "(Settings → Nest git). Always-allow does not skip auth. "
    "Do not invent that browser or desktop GitHub login is the agent's. "
    "Do not park @Boss as the git enabler. "
    "A git push approval card is not a request for the boss to run the command. "
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
    tools = ", ".join(ALLOWED_TOOLS)
    dest_line = _locked_copy_dest_lines(agent, task_id=task_id)
    dest_suffix = f"{dest_line}\n" if dest_line else ""
    return (
        f"{_RUNTIME_CORE_TITLE}\n"
        f"You are {name} ({specialty}).\n"
        f"You work for {boss_label()} — the boss. Tag them as {boss_mention()}.\n"
        f"{desk}\n"
        f"Tools you may use: {tools}.\n"
        "Workspace: /me is your private scratch and /projects is shared "
        "with your floor — prefer them. Other host paths are fine where the "
        "boss names or allows them: use them in cli, and if one is denied "
        "call request_host_access (path + reason). Do not ask the boss for "
        "a verbal yes/no. Do not invent access or claim the file exists.\n"
        "Done: complete only with a checkable claim "
        "(artifact path, tests evidence, or allow/deny proof). "
        "Empty done is rejected. Do not fake done. "
        "A chat assertion is not a claim — the log must show tool evidence. "
        "Thread-origin work: Done must point at a path peers can open "
        "(a /projects path, or the host path the task itself names). "
        "/me is desk-private scratch, not a handoff.\n"
        f"{format_memory_guidance()}\n"
        f"{LOCKED_WORKSPACE_COPY_STEER}\n"
        f"{dest_suffix}"
        f"{AUDIENCE_SOFT_JUDGMENT}\n"
        f"{SAY_WITH_ACTIONS}\n"
        f"{CHAT_FORMATTING}"
    )


def workspace_preference_context(agent: Agent) -> dict[str, str]:
    """Return the agent's own locked Branch/workspace-copy facts for prompt workspace context.

    Args:
        agent: The agent whose turn is being rendered.

    Returns:
        ``preference`` (``cloned``/``branched``) and ``clone_dest`` of the
        newest lock whose copy still exists; both empty when none does.

    Raises:
        ValueError: A locked row's ``clone_dest`` is not a ``/me`` path.
    """
    rows = locked_workspace_copies(agent)
    if not rows:
        return {"preference": "", "clone_dest": ""}
    row = rows[0]
    return {
        "preference": row.status,
        "clone_dest": (row.clone_dest or "").strip(),
    }


def locked_workspace_copies(agent: Agent) -> list[HostPathConsentRequest]:
    """Return the agent's own Branch/workspace-copy locks whose copy still exists.

    A lock binds the agent that owns the copy, in every task, and lasts
    while the copy exists under that agent's ``/me``. Deleting the copy
    releases it. Shell and virtual writes both read locks from here.

    Args:
        agent: The agent whose locks to read.

    Returns:
        The agent's ``cloned``/``branched`` workspace-preference rows, newest
        first, whose copy exists. Empty for the hire preview agent.

    Raises:
        ValueError: A locked row's ``clone_dest`` is not a ``/me`` path.
    """
    # The hire preview agent has no rows, and resolving its /me would create one.
    if agent.id == "preview":
        return []
    import db

    return [
        row
        for row in db.list_locked_workspace_copies(agent_id=agent.id)
        if _copy_exists(agent.storage_key, row)
    ]


def teammate_locked_copies(
    agent: Agent,
    task_id: str | None,
) -> list[tuple[Agent, HostPathConsentRequest]]:
    """Return other agents' locks opened for this task whose copy still exists.

    For prompt lines only: a teammate's lock steers this agent off that host
    folder but never blocks it, since the copy is in the owner's workspace.

    Args:
        agent: The agent whose turn is being rendered.
        task_id: The turn's task; without one there are no teammates.

    Returns:
        ``(owner, row)`` pairs, newest decision first.

    Raises:
        LookupError: A row's owner no longer exists (rows are deleted with
            their agent, so this is a broken invariant).
        ValueError: A locked row's ``clone_dest`` is not a ``/me`` path.
    """
    token = (task_id or "").strip()
    if not token:
        return []
    import db

    found: list[tuple[Agent, HostPathConsentRequest]] = []
    for row in db.list_locked_workspace_copies(task_id=token):
        if row.agent_id == agent.id:
            continue
        owner = db.get_agent(row.agent_id)
        if owner is None:
            raise LookupError(
                f"Locked workspace copy {row.id} belongs to missing agent {row.agent_id}"
            )
        if _copy_exists(owner.storage_key, row):
            found.append((owner, row))
    return found


def _copy_exists(agent_storage_key: str, row: HostPathConsentRequest) -> bool:
    """Return True when a locked row's copy exists in its owner's ``/me``.

    A Clone of one file makes a file copy, so this checks existence, not
    is-dir.

    Raises:
        ValueError: ``clone_dest`` is not a ``/me`` path;
            ``resume_workspace_preference`` always sets ``/me/host-work/<name>``.
    """
    dest = (row.clone_dest or "").strip()
    if not dest.startswith("/me/"):
        raise ValueError(f"Locked workspace copy {row.id} has no /me clone_dest: {dest!r}")
    # core.bm_cli imports this module (via shell_executor_consent) at load.
    from core.bm_cli.virtual_fs import resolve_cli_path

    return resolve_cli_path(agent_storage_key, "/me", dest).exists


def _locked_copy_dest_lines(agent: Agent, *, task_id: str | None) -> str:
    own = [
        f"Locked workspace copy for {row.path}: work at "
        f"{(row.clone_dest or '').strip()}. Host writes stay blocked."
        for row in locked_workspace_copies(agent)
    ]
    teammates = [
        f"{owner.name} is working on a locked copy of {row.path} for this task "
        f"(in their workspace at {(row.clone_dest or '').strip()}). "
        f"Do not edit that host folder; coordinate with {owner.name}."
        for owner, row in teammate_locked_copies(agent, task_id)
    ]
    return "\n".join([*own, *teammates])


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
