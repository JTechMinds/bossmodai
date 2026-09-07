"""BossMod AI — Tasking services and board utilities."""

from core.tasking.board import build_project_summary, build_task_board, serialize_task_board
from core.tasking.service import (
    TaskCreateOrBindResult,
    append_task_event,
    cancel_task_as_operator,
    cancel_tasks_as_operator,
    create_or_bind_subtask,
    create_or_bind_task,
    list_open_child_tasks,
    list_open_origin_tasks_for_channel,
)
from core.tasking.transitions import (
    ALLOWED_TASK_TRANSITIONS,
    TERMINAL_TASK_STATUSES,
    IllegalTaskTransition,
    assert_valid_task_transition,
    is_allowed_task_transition,
    is_terminal_task_status,
    transition_task,
)

__all__ = [
    "ALLOWED_TASK_TRANSITIONS",
    "TERMINAL_TASK_STATUSES",
    "IllegalTaskTransition",
    "TaskCreateOrBindResult",
    "assert_valid_task_transition",
    "build_project_summary",
    "append_task_event",
    "build_task_board",
    "cancel_task_as_operator",
    "cancel_tasks_as_operator",
    "create_or_bind_subtask",
    "create_or_bind_task",
    "is_allowed_task_transition",
    "is_terminal_task_status",
    "list_open_child_tasks",
    "list_open_origin_tasks_for_channel",
    "serialize_task_board",
    "transition_task",
]
