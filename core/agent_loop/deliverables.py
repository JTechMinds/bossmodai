"""BossMod AI — Structured deliverable helpers for work activities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models.task import Task
from core.models.work_contract import DeliverableSpec, WorkContract


def build_work_contract(
    deliverables: list[DeliverableSpec] | None,
    *,
    agent_storage_key: str,
    cwd: str,
) -> WorkContract | None:
    """Normalize deliverables to absolute virtual paths for durable storage."""
    if not deliverables:
        return None
    normalized_items: list[DeliverableSpec] = []
    for item in WorkContract(deliverables=deliverables).deliverables:
        if item.type == "file":
            resolved = resolve_cli_path(agent_storage_key, cwd, item.path)
            normalized_items.append(
                DeliverableSpec(
                    type=item.type,
                    path=resolved.virtual_path,
                    description=item.description,
                )
            )
            continue
        normalized_items.append(item)
    return WorkContract(deliverables=normalized_items)


def get_work_contract(task: Task | dict[str, Any] | None) -> WorkContract:
    """Return the normalized work contract from a durable task."""
    if not task:
        return WorkContract()
    raw_contract = task.work_contract if isinstance(task, Task) else task.get("work_contract")
    if not raw_contract:
        return WorkContract()
    if isinstance(raw_contract, WorkContract):
        return raw_contract
    return WorkContract.model_validate(raw_contract)


def missing_deliverables(
    *,
    agent_id: str,
    agent_storage_key: str,
    task: Task | None,
) -> list[DeliverableSpec]:
    """Return the structured deliverables that are still unsatisfied."""
    contract = get_work_contract(task)
    if not contract.deliverables:
        return []
    threshold = _contract_threshold(task)
    return [
        item
        for item in contract.deliverables
        if not _deliverable_is_satisfied(
            agent_id=agent_id,
            agent_storage_key=agent_storage_key,
            deliverable=item,
            not_before=threshold,
        )
    ]


def summarize_deliverable(deliverable: DeliverableSpec) -> str:
    """Render a short operator/model-facing description of a deliverable."""
    return deliverable.path


def format_deliverables_for_context(task: Task | dict[str, Any] | None) -> list[str]:
    """Render current durable work-contract deliverables for prompt context."""
    contract = get_work_contract(task)
    if not contract.deliverables:
        return []
    return [f"- {item.type}: {summarize_deliverable(item)}" for item in contract.deliverables]


def _deliverable_is_satisfied(
    *,
    agent_id: str,
    agent_storage_key: str,
    deliverable: DeliverableSpec,
    not_before: datetime | None,
) -> bool:
    """Return whether one deliverable has been satisfied through allowed tools."""
    if deliverable.type != "file":
        return False
    resolved = resolve_cli_path(agent_storage_key, "/", deliverable.path)
    if not _path_is_file(resolved.real_path, resolved.exists):
        return False
    if not_before is None:
        return True
    return db.has_bm_cli_write_for_path(agent_id, resolved.virtual_path, since=not_before)


def _path_is_file(path: object, exists: bool) -> bool:
    """Return whether a resolved filesystem path currently exists as a file."""
    return bool(path and exists and getattr(path, "is_file", lambda: False)())


def _normalize_threshold(value: datetime) -> datetime:
    """Normalize a threshold datetime to timezone-aware UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _contract_threshold(task: Task | None) -> datetime | None:
    """Return the durable threshold for contract-bound deliverable validation."""
    if task is None:
        return None
    if task.work_contract_updated_at is not None:
        return _normalize_threshold(task.work_contract_updated_at)
    return _normalize_threshold(task.created_at)
