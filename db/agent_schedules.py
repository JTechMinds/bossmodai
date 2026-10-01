"""BossMod AI — per-agent recurring schedules (``agent_schedules``).

The app writes the rule (operator create, edit, delete); the runtime worker
reads the enabled rules and writes each occurrence's outcome. When a
schedule runs next is never stored: the worker derives it from the rule and
the clock (core/scheduling/timetable.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.models.schedule import AgentSchedule, RecurrenceRule, ScheduleOutcome
from db.connection import transaction
from db.crud import build_update, execute, insert_returning_dict, query, query_one

_SCHEDULE_COLUMNS = (
    "id, agent_id, title, instructions, recurrence, notification_policy, enabled, "
    "last_occurrence_at, last_outcome, last_outcome_detail, last_task_id, created_by, agent_can_change, "
    "created_at, updated_at"
)

# What an edit may change. The outcome columns are record_outcome's; who may
# change agent_can_change is the service's rule (core/scheduling/service.py).
_EDITABLE_COLUMNS = {"title", "instructions", "recurrence", "notification_policy", "enabled", "agent_can_change"}


def _schedule_from_row(row: dict[str, Any]) -> AgentSchedule:
    """Hydrate one row, parsing ``recurrence`` through ``RecurrenceRule``.

    Raises:
        pydantic.ValidationError: The stored rule is not valid JSON or not a
            valid rule. Only this module writes it, so that is corruption,
            and it is never replaced by a default.
    """
    data = dict(row)
    data["recurrence"] = RecurrenceRule.model_validate_json(data["recurrence"])
    return AgentSchedule.model_validate(data)


def create_schedule(
    *,
    agent_id: str,
    title: str,
    instructions: str,
    recurrence: RecurrenceRule,
    notification_policy: str,
    enabled: bool,
    created_by: str,
    agent_can_change: bool,
) -> AgentSchedule:
    """Insert one schedule for ``agent_id``.

    Args:
        agent_id: An existing agent's id (FK).
        title: Each run's task title (already validated).
        instructions: Each run's task description (already validated).
        recurrence: The validated rule; stored as JSON.
        notification_policy: ``none``, ``completion_blocked`` or ``all``.
        enabled: Whether the worker runs it.
        created_by: ``HUMAN_SENDER_ID`` (the operator) or the creating agent's id.
        agent_can_change: Whether the agent may change it.

    Returns:
        The stored schedule.

    Raises:
        sqlite3.IntegrityError: ``agent_id`` names no agent, or the policy
            is not one of the three.
    """
    row = insert_returning_dict(
        """
        INSERT INTO agent_schedules (
            agent_id, title, instructions, recurrence, notification_policy, enabled, created_by, agent_can_change
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
        """,
        [
            agent_id, title, instructions, recurrence.model_dump_json(), notification_policy, enabled,
            created_by, agent_can_change,
        ],
    )
    # Re-read rather than hydrate the RETURNING row: SQLite reports no
    # declared column types there, so its timestamps would come back as text.
    schedule = get_schedule(row["id"])
    if schedule is None:
        raise RuntimeError(f"Failed to reload created schedule {row['id']}")
    return schedule


def get_schedule(schedule_id: str) -> AgentSchedule | None:
    """Return one schedule, or ``None`` when there is no such row.

    Raises:
        pydantic.ValidationError: The stored rule is corrupt.
    """
    row = query_one(f"SELECT {_SCHEDULE_COLUMNS} FROM agent_schedules WHERE id = $1", [schedule_id])
    return _schedule_from_row(row) if row is not None else None


def list_schedules_for_agent(agent_id: str) -> list[AgentSchedule]:
    """Return one agent's schedules, oldest first (enabled or not).

    Raises:
        pydantic.ValidationError: A stored rule is corrupt.
    """
    rows = query(
        f"SELECT {_SCHEDULE_COLUMNS} FROM agent_schedules WHERE agent_id = $1 ORDER BY created_at, id",
        [agent_id],
    )
    return [_schedule_from_row(row) for row in rows]


def find_schedules_by_prefix(agent_id: str, prefix: str) -> list[AgentSchedule]:
    """Return ``agent_id``'s schedules whose id starts with ``prefix``, oldest first.

    For the agent's ``schedules`` command, which shows short ids. Compared
    with ``substr`` rather than ``LIKE``, so ``_`` and ``%`` in a prefix are
    not wildcards.

    Raises:
        ValueError: ``prefix`` is empty.
        pydantic.ValidationError: A stored rule is corrupt.
    """
    if not prefix:
        raise ValueError("find_schedules_by_prefix needs a non-empty prefix")
    rows = query(
        f"""
        SELECT {_SCHEDULE_COLUMNS} FROM agent_schedules
        WHERE agent_id = $1 AND substr(id, 1, length($2)) = $2
        ORDER BY created_at, id
        """,
        [agent_id, prefix],
    )
    return [_schedule_from_row(row) for row in rows]


def list_enabled_schedules() -> list[AgentSchedule]:
    """Return every enabled schedule, oldest first: the worker's timetable load.

    Raises:
        pydantic.ValidationError: A stored rule is corrupt.
    """
    rows = query(
        f"SELECT {_SCHEDULE_COLUMNS} FROM agent_schedules WHERE enabled = 1 ORDER BY created_at, id",
    )
    return [_schedule_from_row(row) for row in rows]


def update_schedule(schedule_id: str, **fields: Any) -> AgentSchedule | None:
    """Apply an operator edit and stamp ``updated_at``.

    Args:
        schedule_id: The schedule to change.
        **fields: Any of ``title``, ``instructions``, ``recurrence`` (a
            ``RecurrenceRule``), ``notification_policy``, ``enabled`` and
            ``agent_can_change``.

    Returns:
        The stored schedule, or ``None`` when there is no such row.

    Raises:
        ValueError: No field, or a field outside the editable set (the
            outcome columns belong to ``record_outcome``).
        sqlite3.IntegrityError: The policy is not one of the three.
    """
    if not fields:
        raise ValueError("update_schedule needs at least one field")
    unknown = sorted(set(fields) - _EDITABLE_COLUMNS)
    if unknown:
        raise ValueError(f"update_schedule cannot change {', '.join(unknown)}")
    values = dict(fields)
    if "recurrence" in values:
        rule = values["recurrence"]
        if not isinstance(rule, RecurrenceRule):
            raise ValueError("update_schedule takes recurrence as a RecurrenceRule")
        values["recurrence"] = rule.model_dump_json()
    values["updated_at"] = datetime.now(timezone.utc)
    build_update("agent_schedules", "id", schedule_id, values, _EDITABLE_COLUMNS | {"updated_at"})
    return get_schedule(schedule_id)


def record_outcome(
    schedule_id: str,
    *,
    occurrence: datetime,
    outcome: ScheduleOutcome,
    detail: str | None,
    task_id: str | None,
) -> None:
    """Record what one occurrence did. Written only by the runtime worker's runner.

    ``last_task_id`` moves only on ``fired``: a skipped, missed or failed
    run leaves the last real run's task in place, so an open one keeps
    skipping the runs that follow it.

    Args:
        schedule_id: The schedule that came due.
        occurrence: The instant it was due (not when it was handled).
        outcome: What happened.
        detail: The error sentence for ``failed``; optional otherwise.
        task_id: The created task for ``fired``; ``None`` otherwise.

    Raises:
        ValueError: ``fired`` without ``task_id``, ``task_id`` on any other
            outcome, or ``failed`` without ``detail``.
        LookupError: No such schedule (deleted since it was read).
    """
    if outcome == "fired" and not task_id:
        raise ValueError("a fired occurrence needs the task it created")
    if outcome != "fired" and task_id is not None:
        raise ValueError(f"a {outcome} occurrence created no task")
    if outcome == "failed" and not detail:
        raise ValueError("a failed occurrence needs its error detail")
    rows = query(
        """
        UPDATE agent_schedules
        SET last_occurrence_at = $2,
            last_outcome = $3,
            last_outcome_detail = $4,
            last_task_id = COALESCE($5, last_task_id)
        WHERE id = $1
        RETURNING id
        """,
        [schedule_id, occurrence, outcome, detail, task_id],
    )
    if not rows:
        raise LookupError(f"schedule {schedule_id} no longer exists")


def delete_schedule(schedule_id: str) -> bool:
    """Delete one schedule; its past runs stay on the board, detached.

    In one transaction: every task it created has ``schedule_id`` set to
    NULL, then the row goes, so no task points at a missing schedule.

    Returns:
        False when there was no such schedule.
    """
    with transaction():
        execute("UPDATE tasks SET schedule_id = NULL WHERE schedule_id = $1", [schedule_id])
        deleted = query("DELETE FROM agent_schedules WHERE id = $1 RETURNING id", [schedule_id])
    return bool(deleted)
