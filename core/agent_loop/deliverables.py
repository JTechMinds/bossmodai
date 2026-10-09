"""BossMod AI — Structured deliverable helpers for work activities.

A file deliverable is satisfied when the file exists and was modified at or
after the task was created. Who wrote it, and with which tool (a CLI write, a
shell command, a teammate's write), does not matter: the invariant is that the
file exists and was produced for this task. Editing the contract never
invalidates work already done for the task.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.bm_cli.virtual_fs import resolve_cli_path
from core.models.task import Task
from core.models.work_contract import DeliverableSpec, WorkContract
from pydantic import ValidationError

# The wire shape of one ``data.task.outs`` item, quoted in every outs error so
# the model is told the right shape instead of one wrong key at a time.
OUTS_ITEM_SHAPE = '{"type":"file","path":"<path>","desc":"<optional>"}'
_OUTS_SHAPE_HINT = f"; each outs item is {OUTS_ITEM_SHAPE}, files only, omit outs otherwise"
# Keeps a model-invented key from pushing the shape hint past the repair
# prompt's 180-character snippet cut.
_MAX_FIELD_CHARS = 20


def parse_wire_outs(value: Any) -> list[dict[str, Any]] | None:
    """Validate model-facing ``outs`` into canonical deliverable dicts.

    The wire item is ``{"type":"file","path":...,"desc":...}`` (``desc``
    optional). Each item is validated by :class:`DeliverableSpec`, the single
    source of truth for the deliverable shape: ``type`` must be ``"file"``,
    ``path`` must be a non-empty string (stripped here), and any other key is
    rejected. Used by both execution actions and decision contracts.

    Args:
        value: The raw ``outs`` value from the model's JSON.

    Returns:
        ``None`` when ``value`` is ``None`` or ``""``; otherwise one
        ``{"type", "path", "description"}`` dict per item, in order.

    Raises:
        ValueError: ``value`` is not a list, an item is not an object, an
            item carries the model field name ``description`` instead of
            ``desc``, or an item fails ``DeliverableSpec`` validation. The
            message names the first failing field and ends with the correct
            item shape; it is at most 180 characters.
    """
    if value in (None, ""):
        return None
    if not isinstance(value, list):
        raise ValueError(f'"data.task.outs" must be an array{_OUTS_SHAPE_HINT}')

    specs: list[DeliverableSpec] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f'each "data.task.outs" item must be an object{_OUTS_SHAPE_HINT}')
        # ``description`` is the model's field, not a wire key; mapping ``desc``
        # onto it would otherwise let the unmapped name through ``extra="forbid"``.
        if "description" in item:
            raise ValueError(f'invalid outs item ("description": use "desc"){_OUTS_SHAPE_HINT}')
        fields = {("description" if key == "desc" else key): val for key, val in item.items()}
        try:
            specs.append(DeliverableSpec.model_validate(fields))
        except ValidationError as exc:
            raise ValueError(_describe_outs_error(exc)) from exc
    return [spec.model_dump() for spec in specs]


def _describe_outs_error(exc: ValidationError) -> str:
    """Name the first failing field of one outs item, in wire terms, plus the shape hint."""
    first = exc.errors()[0]
    loc = first.get("loc") or ()
    field = str(loc[0]) if loc else ""
    if field == "description":
        field = "desc"
    error = first.get("ctx", {}).get("error") if first.get("type") == "value_error" else None
    reason = str(error) if error is not None else str(first["msg"])
    if field:
        return f'invalid outs item ("{field[:_MAX_FIELD_CHARS]}": {reason}){_OUTS_SHAPE_HINT}'
    return f"invalid outs item ({reason}){_OUTS_SHAPE_HINT}"


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
    agent_storage_key: str,
    task: Task | None,
) -> list[DeliverableSpec]:
    """Return the structured deliverables that are still unsatisfied.

    Args:
        agent_storage_key: Storage key of the agent whose ``/me`` resolves
            relative deliverable paths.
        task: The durable task carrying the work contract, or ``None``.

    Returns:
        The deliverables that do not yet exist as a file modified at or after
        ``task.created_at``. Empty when the task has no deliverables.
    """
    contract = get_work_contract(task)
    if not contract.deliverables or task is None:
        return []
    threshold = _normalize_threshold(task.created_at)
    return [
        item
        for item in contract.deliverables
        if not _deliverable_is_satisfied(
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
    agent_storage_key: str,
    deliverable: DeliverableSpec,
    not_before: datetime,
) -> bool:
    """Return whether one deliverable exists as a file modified at or after ``not_before``."""
    if deliverable.type != "file":
        return False
    resolved = resolve_cli_path(agent_storage_key, "/", deliverable.path)
    if not _path_is_file(resolved.real_path, resolved.exists):
        return False
    modified_at = datetime.fromtimestamp(resolved.real_path.stat().st_mtime, tz=timezone.utc)
    return modified_at >= not_before


def _path_is_file(path: object, exists: bool) -> bool:
    """Return whether a resolved filesystem path currently exists as a file."""
    return bool(path and exists and getattr(path, "is_file", lambda: False)())


def _normalize_threshold(value: datetime) -> datetime:
    """Normalize a threshold datetime to timezone-aware UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

