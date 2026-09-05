"""BossMod AI — Tasking services and board utilities."""

from core.tasking.board import build_project_summary, build_task_board, serialize_task_board
from core.tasking.service import (
    TaskCreateOrBindResult,
    append_task_event,
    create_or_bind_subtask,
    create_or_bind_task,
    list_open_child_tasks,
)
from core.tasking.transitions import (
    ALLOWED_TASK_TRANSITIONS,
    IllegalTaskTransition,
    assert_valid_task_transition,
    is_allowed_task_transition,
    transition_task,
)

__all__ = [
    "ALLOWED_TASK_TRANSITIONS",
    "IllegalTaskTransition",
    "TaskCreateOrBindResult",
    "assert_valid_task_transition",
    "build_project_summary",
    "append_task_event",
    "build_task_board",
    "create_or_bind_subtask",
    "create_or_bind_task",
    "is_allowed_task_transition",
    "list_open_child_tasks",
    "serialize_task_board",
    "transition_task",
]
