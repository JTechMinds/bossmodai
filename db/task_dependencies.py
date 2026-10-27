"""BossMod AI — Which task waits on which (the ``task_dependencies`` edge table).

One row per (task, prerequisite) edge: the task does not start until the
prerequisite is ``complete``. Edges are written only when a task is created
(``db/tasks.py`` ``create_task``), only to tasks that already exist, and are
never edited, so the graph cannot hold a cycle. The one removal is
``delete_unfinished_prerequisites``, the operator's "start it anyway".

Edges are read through the task join in ``db/tasks.py`` (``Task.prerequisites``)
and through the functions here. What a finished prerequisite does to the
tasks waiting on it lives in ``core/tasking/dependencies.py``; this layer
stores and reads edges.

``complete`` is the one status that meets a prerequisite
(``core.tasking.dependencies.MET_PREREQUISITE_STATUSES``) and ``held`` the
status of a task still waiting; both are spelled in the queries below.
"""

from __future__ import annotations

from collections.abc import Sequence

from db.crud import execute, query, query_one


class PrerequisiteClosed(Exception):
    """A prerequisite closed without completing, so nothing can wait on it.

    Raised by ``create_task`` after the savepoint rolled back: no task row
    and no edge were written.

    Attributes:
        prerequisite_id: The prerequisite task.
        status: The closed status it has (``abandoned``, ``declined`` or ``cancelled``).
    """

    def __init__(self, prerequisite_id: str, status: str) -> None:
        self.prerequisite_id = prerequisite_id
        self.status = status
        super().__init__(f"Prerequisite {prerequisite_id} is {status}")


def add_task_dependencies(task_id: str, prerequisite_ids: Sequence[str]) -> None:
    """Record that ``task_id`` waits on each of ``prerequisite_ids``, in order.

    Insertion order is the order ``Task.prerequisites`` lists them in.
    Duplicates collapse to the first.

    Args:
        task_id: The waiting task.
        prerequisite_ids: The tasks it waits on. Empty writes nothing.

    Raises:
        sqlite3.IntegrityError: An id names no task, or a task names itself.
    """
    for prerequisite_id in dict.fromkeys(prerequisite_ids):
        execute(
            "INSERT INTO task_dependencies (task_id, prerequisite_id) VALUES ($1, $2)",
            [task_id, prerequisite_id],
        )


def list_held_dependents(prerequisite_id: str) -> list[str]:
    """Return the ids of the ``held`` tasks with an edge to ``prerequisite_id``.

    Args:
        prerequisite_id: The task that may have just finished.

    Returns:
        Task ids, oldest task first (insertion order breaks ties).
    """
    rows = query(
        """
        SELECT t.id
        FROM task_dependencies td
        JOIN tasks t ON t.id = td.task_id
        WHERE td.prerequisite_id = $1 AND t.status = 'held'
        ORDER BY t.created_at, t.rowid
        """,
        [prerequisite_id],
    )
    return [str(row["id"]) for row in rows]


def count_unfinished_prerequisites(task_id: str) -> int:
    """Return how many of ``task_id``'s prerequisites are not ``complete``.

    Args:
        task_id: The waiting task.

    Returns:
        The number of edges whose prerequisite is in any other status, open
        or closed without completing. 0 for a task with no edges.
    """
    row = query_one(
        """
        SELECT COUNT(*) AS cnt
        FROM task_dependencies td
        JOIN tasks p ON p.id = td.prerequisite_id
        WHERE td.task_id = $1 AND p.status <> 'complete'
        """,
        [task_id],
    )
    return int(row["cnt"]) if row is not None else 0


def delete_unfinished_prerequisites(task_id: str) -> list[str]:
    """Remove ``task_id``'s edges to prerequisites that are not ``complete``.

    The operator's override (release, resume, edit): the task stops waiting
    on them. Edges to completed prerequisites stay, so the task can still be
    told what came before it.

    Args:
        task_id: The waiting task.

    Returns:
        The removed prerequisite ids, in edge order, for the audit event.
    """
    rows = query(
        """
        DELETE FROM task_dependencies
        WHERE task_id = $1
          AND prerequisite_id IN (SELECT id FROM tasks WHERE status <> 'complete')
        RETURNING prerequisite_id, rowid AS edge_order
        """,
        [task_id],
    )
    rows.sort(key=lambda row: row["edge_order"])
    return [str(row["prerequisite_id"]) for row in rows]
