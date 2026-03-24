"""BossMod AI — Durable work contracts bound to tasks."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.models.work_contract import TaskWorkContract, WorkContract
from db.crud import execute, insert_returning_dict, query_one

_CONTRACT_COLUMNS = "task_id, work_contract, created_at, updated_at"


def get_task_work_contract(task_id: str) -> TaskWorkContract | None:
    """Return the durable work contract record for a task, if any."""
    row = query_one(
        f"SELECT {_CONTRACT_COLUMNS} FROM task_work_contracts WHERE task_id = $1",
        [task_id],
    )
    if row is None:
        return None
    return TaskWorkContract.model_validate(
        {
            **row,
            "work_contract": json.loads(row["work_contract"]),
        }
    )


def set_task_work_contract(task_id: str, work_contract: WorkContract) -> TaskWorkContract:
    """Create or replace the durable work contract for a task."""
    existing = get_task_work_contract(task_id)
    if existing is None:
        row = insert_returning_dict(
            f"""
            INSERT INTO task_work_contracts (task_id, work_contract)
            VALUES ($1, $2)
            RETURNING {_CONTRACT_COLUMNS}
            """,
            [task_id, json.dumps(work_contract.model_dump())],
        )
        return TaskWorkContract.model_validate(
            {
                **row,
                "work_contract": json.loads(row["work_contract"]),
            }
        )

    now = datetime.now(timezone.utc)
    execute(
        """
        UPDATE task_work_contracts
        SET work_contract = $1, updated_at = $2
        WHERE task_id = $3
        """,
        [json.dumps(work_contract.model_dump()), now, task_id],
    )
    refreshed = get_task_work_contract(task_id)
    if refreshed is None:
        raise RuntimeError(f"Failed to reload work contract for task {task_id}")
    return refreshed


def delete_task_work_contract(task_id: str) -> None:
    """Delete a durable work contract bound to a task."""
    execute("DELETE FROM task_work_contracts WHERE task_id = $1", [task_id])
