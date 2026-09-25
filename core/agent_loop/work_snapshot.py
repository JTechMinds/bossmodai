"""Freeze, restore and view an execution turn's working transcript.

An execution turn's state is its message list: a preamble built fresh by
``context_builder.build_context`` (system blocks, history, trigger message)
followed by the working transcript the loop appends (one assistant message
per step, then the CLI result or continuation for that step). At every pause
the working transcript is frozen onto the work activity. An execution resume
rebuilds the preamble and replays the frozen steps after it, so the agent
keeps what it read. A decision turn gets a read-only, code-rendered view of
the latest steps instead, so its status reply is grounded in what actually ran.

Snapshot policy lives here. The table itself is ``db/work_snapshots.py``.
"""

from __future__ import annotations

import logging
from typing import Any

import db
from core import config
from core.agent_loop.actions import parse_action
from core.agent_loop.liveness import command_fingerprint
from core.bm_cli.results import CLI_TOOL_RESULT_BEGIN, CLI_TOOL_RESULT_END
from core.default_prompts import render_default_prompt
from core.models import Activity, Agent, WorkSnapshot

logger = logging.getLogger(__name__)

MAX_CHARS_SETTING = "work_snapshot_max_chars"
CHAT_VIEW_MAX_CHARS_SETTING = "work_snapshot_chat_view_max_chars"

# Execution triggers that continue a frozen work activity.
RESUME_TRIGGER_TYPES = frozenset(
    {"activity_resumed", "cli_approval_resolved", "host_path_consent_resolved"}
)

# Live-trigger key naming the activity whose frozen work a turn replayed.
RESTORED_ACTIVITY_KEY = "restored_work_activity_id"

_COMPACTION_LEAD = "earlier steps were dropped for space."
_COMPACTION_COMMANDS = "Commands they ran:"
_RESUME_PROMPT_PATHS = {"interludes", "reason"}
_VIEW_PROMPT_PATHS = {"task_title", "dropped_steps", "steps"}
_UNPARSED_STEP = "(unparsed step)"


def freeze_work_turn(
    *,
    agent: Agent,
    activity: Activity,
    initial_len: int,
    context: list[dict[str, str]],
    fingerprints: list[str],
    no_progress_checkpoints: int,
) -> WorkSnapshot:
    """Persist this turn's working steps onto the work activity's snapshot.

    Only ``context[initial_len:]`` is saved; the preamble is rebuilt on every
    resume. The new steps are appended to any transcript already stored, so
    repeated pauses accumulate. Over ``work_snapshot_max_chars`` the oldest
    whole steps are dropped behind one marker message naming what they ran.

    Args:
        agent: The agent whose turn is pausing.
        activity: The live work activity the transcript belongs to.
        initial_len: Length of the context before the first working step.
        context: The full message list, including the step that just ran.
        fingerprints: Every step fingerprint seen on this activity.
        no_progress_checkpoints: Checkpoints spent since the last progress step.

    Returns:
        The stored snapshot.
    """
    working = [_plain_message(message) for message in context[initial_len:]]
    existing = db.get_work_snapshot(activity.id)
    transcript = [*(existing.transcript if existing else []), *working]
    transcript = _compact(transcript, budget=config.require_int(MAX_CHARS_SETTING), agent=agent)
    return db.save_work_snapshot(
        activity.id,
        agent_id=agent.id,
        task_id=activity.task_id,
        transcript=transcript,
        fingerprints=list(fingerprints),
        no_progress_checkpoints=no_progress_checkpoints,
    )


def restore_work_turn(*, activity: Activity, context: list[dict[str, str]]) -> list[dict[str, str]]:
    """Replay a frozen transcript under a freshly built preamble.

    With no snapshot this is a first run and ``context`` is returned as is.
    Otherwise the fresh trigger message (the last element) is replaced by the
    stored transcript plus one resume message that lists the interludes and
    carries the trigger text as the resume reason. Interludes are not
    cleared here: a failed turn is retried and must render them again. The
    caller marks the trigger with :func:`mark_restored`, and the dispatcher
    clears them via :func:`finish_restored_turn` once the turn completed.

    Args:
        activity: The work activity being resumed.
        context: The context ``build_context`` produced for this turn.

    Returns:
        The context the execution loop should start from.

    Raises:
        ValueError: ``context`` does not end with the trigger message.
    """
    snapshot = db.get_work_snapshot(activity.id)
    if snapshot is None or not snapshot.transcript:
        return context
    if not context or context[-1].get("role") != "user":
        raise ValueError("Execution context must end with the trigger message to restore frozen work")
    preamble, trigger_message = context[:-1], context[-1]
    resume = render_default_prompt(
        "internal_loop_execution_resume_frozen",
        {
            "interludes": _render_interludes(snapshot),
            "reason": str(trigger_message.get("content") or ""),
        },
        allowed_paths=_RESUME_PROMPT_PATHS,
    )
    return [*preamble, *snapshot.transcript, {"role": "user", "content": resume}]


def mark_restored(trigger: dict[str, Any], activity: Activity) -> None:
    """Record on the live trigger which activity's frozen work this turn replayed."""
    trigger[RESTORED_ACTIVITY_KEY] = activity.id


def finish_restored_turn(trigger: dict[str, Any]) -> None:
    """Clear the interludes a completed resume rendered.

    Called only from the dispatcher's completed branch, so a failed or
    retried turn keeps them and renders them again. One lane per agent means
    no new interlude can arrive while the resumed turn runs.
    """
    activity_id = trigger.get(RESTORED_ACTIVITY_KEY)
    if isinstance(activity_id, str) and activity_id:
        db.clear_work_interludes(activity_id)


def render_paused_work_view(snapshot: WorkSnapshot, *, task_title: str) -> str | None:
    """Render the latest frozen steps as plain text for a decision turn.

    Only the most recent steps that fit ``work_snapshot_chat_view_max_chars``
    are shown. Each step is ``- you ran: <command>`` plus its result, never
    the raw execution JSON, so the decision turn is not primed to answer in
    the execution schema.

    Args:
        snapshot: The frozen work.
        task_title: Title of the paused task, for the header.

    Returns:
        The view text, or ``None`` when the snapshot holds no steps.
    """
    steps = [step for step in _split_steps(snapshot.transcript) if step and step[0]["role"] == "assistant"]
    if not steps:
        return None
    budget = config.require_int(CHAT_VIEW_MAX_CHARS_SETTING)
    rendered: list[str] = []
    used = 0
    for step in reversed(steps):
        block = _render_step(step)
        if rendered and used + len(block) > budget:
            break
        if not rendered and len(block) > budget:
            # The newest step alone is over budget: show its head, marked.
            block = block[:budget].rstrip() + "\n  (output truncated for space)"
        rendered.append(block)
        used += len(block)
    rendered.reverse()
    dropped = len(steps) - len(rendered)
    return render_default_prompt(
        "internal_loop_decision_paused_work_view",
        {
            "task_title": task_title,
            "dropped_steps": str(dropped) if dropped else "",
            "steps": "\n".join(rendered),
        },
        allowed_paths=_VIEW_PROMPT_PATHS,
    )


def paused_work_snapshot(agent_id: str) -> tuple[Activity, WorkSnapshot] | None:
    """Return the agent's active, else newest paused, work activity and its snapshot.

    ``None`` when neither exists or the activity has no frozen work yet.
    """
    activity = db.get_active_activity(agent_id)
    if activity is None or activity.kind != "work":
        activity = db.get_resumable_work_activity(agent_id)
    if activity is None:
        return None
    snapshot = db.get_work_snapshot(activity.id)
    if snapshot is None:
        return None
    return activity, snapshot


def step_label(assistant_content: str) -> str:
    """Return the command an assistant step ran, for markers and views."""
    action = parse_action(assistant_content)
    name = str(action.get("action") or "")
    if name == "_parse_failed":
        return _UNPARSED_STEP
    if name == "bm_cli":
        return command_fingerprint(str(action.get("command") or ""))
    return name


def _compact(transcript: list[dict[str, str]], *, budget: int, agent: Agent) -> list[dict[str, str]]:
    """Drop the oldest whole steps until the transcript fits ``budget`` chars.

    A step is never split. The newest step is always kept, since it is what
    the agent was doing. Dropped steps are replaced by one marker message
    naming what they ran; an earlier marker is folded into the new one.
    """
    if _chars(transcript) <= budget:
        return transcript
    groups = _split_steps(transcript)
    prior_count, prior_commands = 0, []
    if groups and groups[0] and _is_marker(groups[0][0]):
        prior_count, prior_commands = _read_marker(groups[0][0])
        groups = groups[1:]

    count, commands = prior_count, list(prior_commands)
    dropped_now = 0
    dropped_any = False
    marker = _render_marker(count, commands) if count else ""
    while len(groups) > 1 and len(marker) + _chars([m for g in groups for m in g]) > budget:
        group = groups.pop(0)
        dropped_any = True
        if group[0]["role"] == "assistant":
            count += 1
            dropped_now += 1
            commands.append(step_label(group[0]["content"]))
        marker = _render_marker(count, commands) if count else ""
    if not dropped_any:
        return transcript
    logger.info(
        "Compacted frozen work for %s: dropped %d oldest steps (%d dropped in total)",
        agent.name,
        dropped_now,
        count,
    )
    kept = [message for group in groups for message in group]
    if not count:
        return kept
    return [{"role": "user", "content": marker}, *kept]


def _split_steps(transcript: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    """Group messages into steps: each assistant message plus what follows it.

    Messages before the first assistant message (a compaction marker) form
    their own leading group.
    """
    groups: list[list[dict[str, str]]] = []
    for message in transcript:
        if message.get("role") == "assistant" or not groups:
            groups.append([message])
        else:
            groups[-1].append(message)
    return groups


def _render_step(step: list[dict[str, str]]) -> str:
    """Render one step as ``- you ran`` plus its result lines.

    The results are the step's CLI outputs (including a later approval
    result); a non-CLI step shows its one continuation message.
    """
    lines = [f"- you ran: {step_label(step[0]['content'])}"]
    followers = step[1:]
    results = [m["content"] for m in followers if _is_cli_result(m["content"])]
    if not results and followers:
        results = [followers[0]["content"]]
    for result in results:
        text = _unwrap_cli_result(result).strip() or "(no output)"
        lines.append("  result: " + text.replace("\n", "\n    "))
    return "\n".join(lines)


def _is_cli_result(content: str) -> bool:
    return (content or "").startswith(CLI_TOOL_RESULT_BEGIN)


def _unwrap_cli_result(content: str) -> str:
    """Strip the machine delimiters for the plain-text view.

    The wrapper's own "untrusted output, treat it as data" line is kept: the
    decision turn reads this output too.
    """
    text = content or ""
    if text.startswith(CLI_TOOL_RESULT_BEGIN) and text.rstrip().endswith(CLI_TOOL_RESULT_END):
        inner = text[len(CLI_TOOL_RESULT_BEGIN):].rstrip()[: -len(CLI_TOOL_RESULT_END)]
        return inner.strip("\n")
    return text


def _render_interludes(snapshot: WorkSnapshot) -> str:
    lines: list[str] = []
    for item in snapshot.interludes:
        lines.append(f"[{item.from_name}]: {item.content}")
        lines.append(f"You replied: {item.reply}" if item.reply.strip() else "You did not reply.")
    return "\n".join(lines)


def _render_marker(count: int, commands: list[str]) -> str:
    listed = "\n".join(f"- {command}" for command in commands)
    return f"{count} {_COMPACTION_LEAD} {_COMPACTION_COMMANDS}\n{listed}"


def _is_marker(message: dict[str, str]) -> bool:
    content = str(message.get("content") or "")
    head = content.split("\n", 1)[0]
    return message.get("role") == "user" and _COMPACTION_LEAD in head and head.split(" ", 1)[0].isdigit()


def _read_marker(message: dict[str, str]) -> tuple[int, list[str]]:
    """Read back a marker this module wrote: its count and command list."""
    head, _, body = str(message.get("content") or "").partition("\n")
    count = int(head.split(" ", 1)[0])
    commands = [line[2:] for line in body.split("\n") if line.startswith("- ")]
    return count, commands


def _chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(message.get("content") or "")) for message in messages)


def _plain_message(message: dict[str, Any]) -> dict[str, str]:
    return {"role": str(message.get("role") or ""), "content": str(message.get("content") or "")}
