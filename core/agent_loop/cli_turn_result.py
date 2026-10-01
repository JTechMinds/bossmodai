"""BossMod AI — one BossMod CLI result as a turn result, and its UI side effects.

Both turn types run CLI commands: the decision turn's CLI loop
(decision_turn.py), the execution turn's ``bm_cli`` / ``request_host_access``
actions (actions_cli.py) and either turn's managed writes. They all describe
the same ``BossModCliResult``, so one function maps it (``map_cli_result``)
and the two turn types cannot drift apart. Kept apart from actions_cli.py
(one action handler among many) and turn_helpers.py (repair prompts,
traces, finalize) because this is the contract between the CLI and the
turn, and it is documented here, once.

The side-effect contract on ``BossModCliResult.data`` — what a command may
ask the turn to show the operator live:

- ``origin_chrome``: one operator line the command already persisted, as
  the persist helpers return it (a chat payload with ``agent_id`` or a
  thread payload with ``channel_id``, and its ``content``). The turn
  broadcasts it; the command cannot, because it runs off the event loop.
- ``activity``: ``{"event": str, "detail": str, "extra": dict | None}``,
  one activity the turn broadcasts (for example ``schedule_changed``, so the
  agent's desk repaints). ``extra`` may be left out. It is an announcement
  only: the turn result keeps the step's own outcome in ``event`` /
  ``detail`` (``bm_cli_result`` / ``bm_cli_error``, which liveness reads)
  and carries the declaration apart, as ``cli_activity``.

A malformed declaration is a bug in the command and raises
``CliSideEffectError``; it is never dropped.
"""

from __future__ import annotations

from typing import Any

from core.agent_loop.notifications import broadcast_origin_status_messages
from core.bm_cli.types import BossModCliResult
from core.models import Agent

_ACTIVITY_KEYS = frozenset({"event", "detail", "extra"})


class CliSideEffectError(ValueError):
    """A CLI result declared a side effect in the wrong shape (a bug in its command)."""


def map_cli_result(
    agent: Agent,
    cli_result: BossModCliResult,
    *,
    command: str,
    trigger: dict[str, Any] | None = None,
    surface_gate_block: bool = True,
) -> dict[str, Any]:
    """Convert one BossMod CLI result into the turn-local action result.

    The one mapper for both turn types. It sets, in order:

    - the base fields: ``event`` (``bm_cli_result`` / ``bm_cli_error``),
      ``detail``, ``agent_name``, ``cli_prompt_content``,
      ``cli_image_paths``, ``cli_summary``, ``cli_status_lines``,
      ``cli_extension_id``, ``counts_as_progress``,
      ``suppress_world_broadcast=True`` and ``suppress_activity_broadcast=True``;
    - the declared side effects (see the module docstring):
      ``origin_chrome`` becomes the first ``origin_status_messages`` entry,
      and ``activity`` becomes ``cli_activity`` (``{"event", "detail",
      "extra"}``, ``extra`` ``None`` when left out). Neither touches
      ``event``, ``detail`` or ``suppress_activity_broadcast``;
    - ``audit`` / ``approved_by="system"`` for a System AI auto-approval;
    - a named gate deny's ``Blocked —`` origin line (``surface_cli_gate_block``,
      which persists it and may wake the next owner), unless
      ``surface_gate_block`` is off;
    - the managed / batch writer reports and the writer's detail suffix;
    - approval (``event="cli_approval_required"``, ``detail`` "<agent>
      requests approval: <command>") and host-path consent fields, both with
      ``suppress_activity_broadcast=False``.

    What differs between turn types stays with the caller: the decision turn
    adds ``command`` and ends on consent/approval through
    ``_finalize_origin_chrome_pause``; ``_handle_bm_cli`` adds
    ``cli_channel_id``; ``_handle_request_host_access`` renames an
    already-allowed path's event.

    Args:
        agent: The agent that ran the command.
        cli_result: The command's result.
        command: The command as the agent wrote it, for the approval line.
        trigger: The turn's trigger; a gate deny posts to its origin.
        surface_gate_block: The CLI simulator's opt-out, and only that: a
            simulation must not persist ``Blocked —`` lines or wake owners,
            so it passes ``False``. Turns leave it on.

    Returns:
        The turn-local result dict.

    Raises:
        CliSideEffectError: ``data["origin_chrome"]`` or ``data["activity"]``
            is malformed.
    """
    # Imported here: these reach back into the agent loop at import time.
    from core.agent_loop.blocked_origin import surface_cli_gate_block
    from core.agent_loop.liveness import cli_result_counts_as_progress
    from core.models.host_path_consent import consent_turn_event

    data = cli_result.data or {}
    chrome = _declared_origin_chrome(cli_result.command, data)
    activity = _declared_activity(cli_result.command, data)

    result: dict[str, Any] = {
        "event": "bm_cli_result" if cli_result.ok else "bm_cli_error",
        "detail": cli_result.detail,
        "agent_name": agent.name,
        "cli_prompt_content": cli_result.prompt_content,
        "cli_image_paths": list(cli_result.image_paths),
        "cli_summary": cli_result.summary,
        # Operator one-liners a command asked for (validated where they are posted).
        "cli_status_lines": data.get("status_lines", []),
        # Which extension produced it (stamped by the CLI bridge), for the
        # live-view nudge (see turn_helpers.announce_extension_result).
        "cli_extension_id": data.get("extension_id"),
        "counts_as_progress": cli_result_counts_as_progress(cli_result),
        "suppress_world_broadcast": True,
        "suppress_activity_broadcast": True,
    }
    if chrome is not None:
        result["origin_status_messages"] = [chrome]
    if activity is not None:
        result["cli_activity"] = {
            "event": activity["event"],
            "detail": activity["detail"],
            "extra": activity.get("extra"),
        }
    audit = data.get("audit")
    if isinstance(audit, str) and audit.strip():
        result["audit"] = audit
        result["approved_by"] = "system"
    if surface_gate_block:
        surface_cli_gate_block(result, agent=agent, trigger=trigger, cli_result=cli_result)
    _attach_writer_reports(result, cli_result.detail, data)
    if cli_result.approval_required:
        card = data.get("cli_approval") if isinstance(data.get("cli_approval"), dict) else {}
        result["approval_required"] = True
        result["approval_request_id"] = cli_result.approval_request_id
        result["cli_approval"] = card
        result["event"] = "cli_approval_required"
        result["detail"] = f"{agent.name} requests approval: {command}"
        result["suppress_activity_broadcast"] = False
    if cli_result.consent_required:
        card = data.get("host_path_consent") if isinstance(data.get("host_path_consent"), dict) else {}
        result["consent_required"] = True
        result["consent_request_id"] = cli_result.consent_request_id
        result["consent_reused"] = bool(data.get("consent_reused"))
        result["host_path_consent"] = card
        event, detail = consent_turn_event(agent.name, card)
        result["event"] = event
        result["detail"] = detail
        result["suppress_activity_broadcast"] = False
    return result


async def broadcast_cli_side_effects(sink: Any, result: dict[str, Any], *, agent: Agent) -> None:
    """Broadcast what one mapped CLI result asks the operator to see live.

    In order: the step's outcome activity, unless
    ``suppress_activity_broadcast`` (an approval or a consent), then the
    declared ``cli_activity`` (``broadcast_cli_activity``), then every
    ``origin_status_messages`` line (the declared ``origin_chrome`` and any
    gate deny's ``Blocked —`` line). Every line is sent, including one that
    is also the result's ``chat_message`` / ``channel_message``: nothing
    else broadcasts a CLI step's result where this is used.

    The decision turn calls it after every CLI step (``bm_cli`` and
    ``request_host_access``), before it may pause on consent/approval, and
    the CLI simulator after a real run. The execution turn does not: its
    per-action broadcast already sends a result's outcome activity and its
    primary and extra lines once each, and calls ``broadcast_cli_activity``
    for the declaration.

    Args:
        sink: Anything with ``broadcast_activity``, ``broadcast_chat_message``
            and ``broadcast_channel_message``: the worker's ``runtime_events``
            or the app's websocket ``manager``.
        result: A result from ``map_cli_result`` (as returned, or with the
            caller's additions).
        agent: The agent that ran the command; the lines' default author.

    Raises:
        KeyError: ``result`` lacks a field ``map_cli_result`` always sets, or
            a line lacks its ``content`` (a bug, not a missing effect).
    """
    if not result["suppress_activity_broadcast"]:
        await sink.broadcast_activity(
            event=result["event"],
            detail=result["detail"],
            agent_name=result["agent_name"],
        )
    await broadcast_cli_activity(sink, result)
    lines = result.get("origin_status_messages")
    if lines:
        # Only the lines: broadcast_origin_status_messages skips a result's
        # primary chat/channel message, which no one else sends here.
        await broadcast_origin_status_messages({"origin_status_messages": lines}, agent=agent, sink=sink)


async def broadcast_cli_activity(sink: Any, result: dict[str, Any]) -> None:
    """Broadcast a mapped CLI result's declared ``cli_activity``, if it has one.

    Args:
        sink: Anything with ``broadcast_activity``.
        result: A turn result; one without ``cli_activity`` (any non-CLI
            action, or a command that declared none) broadcasts nothing.
    """
    activity = result.get("cli_activity")
    if activity is None:
        return
    await sink.broadcast_activity(
        event=activity["event"],
        detail=activity["detail"],
        agent_name=result["agent_name"],
        extra=activity["extra"],
    )


def _declared_origin_chrome(command: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """The validated ``origin_chrome`` line, or ``None`` when none was declared."""
    if "origin_chrome" not in data:
        return None
    chrome = data["origin_chrome"]
    if not isinstance(chrome, dict):
        raise CliSideEffectError(f"CLI command {command!r} declared origin_chrome that is not an object: {chrome!r}")
    content = chrome.get("content")
    if not isinstance(content, str) or not content.strip():
        raise CliSideEffectError(f"CLI command {command!r} declared origin_chrome without content: {chrome!r}")
    if not any(isinstance(chrome.get(key), str) and chrome[key].strip() for key in ("channel_id", "agent_id")):
        raise CliSideEffectError(
            f"CLI command {command!r} declared origin_chrome with neither channel_id nor agent_id: {chrome!r}"
        )
    return chrome


def _declared_activity(command: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """The validated ``activity`` declaration, or ``None`` when none was declared."""
    if "activity" not in data:
        return None
    activity = data["activity"]
    if not isinstance(activity, dict):
        raise CliSideEffectError(f"CLI command {command!r} declared an activity that is not an object: {activity!r}")
    unknown = set(activity) - _ACTIVITY_KEYS
    if unknown:
        raise CliSideEffectError(
            f"CLI command {command!r} declared an activity with unknown keys {sorted(unknown)}: {activity!r}"
        )
    for key in ("event", "detail"):
        value = activity.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CliSideEffectError(f"CLI command {command!r} declared an activity without a {key}: {activity!r}")
    extra = activity.get("extra")
    if extra is not None and not isinstance(extra, dict):
        raise CliSideEffectError(f"CLI command {command!r} declared an activity whose extra is not an object: {activity!r}")
    return activity


def _attach_writer_reports(result: dict[str, Any], cli_detail: str, data: dict[str, Any]) -> None:
    """Add the managed / batch writer reports a managed write's data carries."""
    if data.get("managed_writer_attempted") or data.get("managed_writer_used"):
        call_count = int(data.get("managed_calls") or data.get("managed_chunks") or 0)
        completed = bool(data.get("managed_writer_completed") or data.get("managed_writer_used"))
        strategy = str(data.get("managed_strategy") or "managed")
        strategy_label = strategy.replace("_", "-")
        section_count = int(data.get("managed_sections") or 0)
        batch_file_count = int(data.get("batch_file_count") or 0)
        section_suffix = (
            f", {section_count} section{'s' if section_count != 1 else ''}"
            if section_count > 0 and strategy == "sectioned"
            else ""
        )
        if batch_file_count > 0:
            suffix = (
                f"via batch writer ({batch_file_count} file{'s' if batch_file_count != 1 else ''}, "
                f"{call_count} call{'s' if call_count != 1 else ''}, {strategy_label}{section_suffix})"
                if completed
                else (
                    f"after batch writer attempt ({batch_file_count} file{'s' if batch_file_count != 1 else ''}, "
                    f"{call_count} call{'s' if call_count != 1 else ''}, {strategy_label}{section_suffix})"
                )
            )
        else:
            suffix = (
                f"via managed writer ({strategy_label}, {call_count} call{'s' if call_count != 1 else ''}{section_suffix})"
                if completed
                else f"after managed writer attempt ({strategy_label}, {call_count} call{'s' if call_count != 1 else ''}{section_suffix})"
            )
        result["detail"] = f"{cli_detail} {suffix}"
        result["managed_writer"] = {
            "attempted": True,
            "used": bool(data.get("managed_writer_used")),
            "completed": completed,
            "strategy": strategy,
            "calls": call_count,
            "chunks": call_count,
            "sections": section_count,
            "bytes": int(data.get("managed_bytes") or 0),
            "prompt_tokens": int(data.get("managed_prompt_tokens") or 0),
            "completion_tokens": int(data.get("managed_completion_tokens") or 0),
            "total_tokens": int(data.get("managed_total_tokens") or 0),
        }
    if data.get("batch_writer_attempted") or data.get("batch_writer_used"):
        result["batch_writer"] = {
            "attempted": True,
            "used": bool(data.get("batch_writer_used")),
            "completed": bool(data.get("batch_writer_completed") or data.get("batch_writer_used")),
            "file_count": int(data.get("batch_file_count") or 0),
            "files": data.get("batch_files") or [],
        }
