"""Task list, create/bind, board, and events."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Response

from api.websocket import manager
from core.agent_loop.activity_scheduler import assignment_wake_trigger
from core.agent_loop.deliverables import build_work_contract
from core.agent_loop.task_roles import default_task_owner_id
from core.models import Task, TaskCandidateSummary, TaskCreate, TaskCreateResponse
from core.models.message import HUMAN_SENDER_ID
from core.runtime import runtime_services
from core.tasking import build_task_board, create_or_bind_task, serialize_task_board
import db

router = APIRouter()


# ─── Tasks CRUD ───

@router.get("/tasks")
async def list_tasks(
    assigned_to: str | None = None,
    owner_id: str | None = None,
    requester_id: str | None = None,
    parent_task_id: str | None = None,
    status: str | None = None,
):
    tasks = db.list_tasks(
        assigned_to=assigned_to,
        owner_id=owner_id,
        requester_id=requester_id,
        parent_task_id=parent_task_id,
        status=status,
    )
    # Resolve agent UUIDs to human-readable names
    agent_ids = {t.assigned_to for t in tasks if t.assigned_to}
    agent_ids |= {t.requester_id for t in tasks if t.requester_id}
    agent_ids |= {t.owner_id for t in tasks if t.owner_id}
    agent_names: dict[str, str] = {}
    agent_storage_keys: dict[str, str] = {}
    for aid in agent_ids:
        agent = db.get_agent(aid)
        if agent:
            agent_names[aid] = agent.name
            agent_storage_keys[aid] = agent.storage_key
    recent_events = db.list_recent_task_events([task.id for task in tasks], limit_per_task=1)
    return [
        {
            **t.model_dump(mode="json"),
            "assigned_to_name": agent_names.get(t.assigned_to) if t.assigned_to else None,
            "assigned_to_storage_key": agent_storage_keys.get(t.assigned_to) if t.assigned_to else None,
            "requester_name": agent_names.get(t.requester_id) if t.requester_id else None,
            "owner_name": agent_names.get(t.owner_id) if t.owner_id else None,
            "latest_event": (
                recent_events[t.id][-1].model_dump(mode="json")
                if recent_events.get(t.id)
                else None
            ),
        }
        for t in tasks
    ]


def _task_candidate_summaries(tasks: tuple | list) -> list[TaskCandidateSummary]:
    """Serialize ambiguous-match candidates for the Assign Task UI."""
    agent_ids = {task.assigned_to for task in tasks if getattr(task, "assigned_to", None)}
    names: dict[str, str] = {}
    for agent_id in agent_ids:
        agent = db.get_agent(agent_id)
        if agent:
            names[agent_id] = agent.name
    return [
        TaskCandidateSummary(
            id=task.id,
            title=task.title,
            status=task.status,
            assigned_to=task.assigned_to,
            assigned_to_name=names.get(task.assigned_to) if task.assigned_to else None,
            last_activity=task.last_activity,
        )
        for task in tasks
    ]


@router.post("/tasks", status_code=201)
async def create_task(body: TaskCreate, response: Response) -> TaskCreateResponse:
    work_contract = body.work_contract
    source_channel = body.source_channel or "api"
    notification_policy = body.notification_policy or "completion_blocked"
    requester_id = body.requester_id or HUMAN_SENDER_ID
    parent_task = None
    if requester_id != HUMAN_SENDER_ID and not db.get_agent(requester_id):
        raise HTTPException(404, "Requester agent not found")
    if body.owner_id == HUMAN_SENDER_ID:
        raise HTTPException(400, "Task owner must be an agent, not the human operator")
    if body.owner_id and not db.get_agent(body.owner_id):
        raise HTTPException(404, "Owner agent not found")
    if body.parent_task_id:
        parent_task = db.get_task(body.parent_task_id)
        if not parent_task:
            raise HTTPException(404, "Parent task not found")
    if work_contract is not None:
        if body.assigned_to:
            agent = db.get_agent(body.assigned_to)
            if not agent:
                raise HTTPException(404, "Assigned agent not found")
            cli_state = db.ensure_agent_cli_state(agent.id)
            work_contract = build_work_contract(
                work_contract.deliverables,
                agent_storage_key=agent.storage_key,
                cwd=cli_state.cwd,
            )
        elif any(item.type == "file" and not item.path.startswith("/") for item in work_contract.deliverables):
            raise HTTPException(
                400,
                "Task work_contract file deliverables must use absolute BossMod CLI paths when assigned_to is omitted.",
            )
    if body.bind_task_id and not db.get_task(body.bind_task_id):
        raise HTTPException(404, "Task not found")
    owner_id = body.owner_id or default_task_owner_id(
        assignee_id=body.assigned_to,
        requester_id=requester_id,
        created_by=HUMAN_SENDER_ID,
        parent_task=parent_task,
    )

    try:
        creation = create_or_bind_task(
            title=body.title,
            description=body.description,
            project=body.project,
            assigned_to=body.assigned_to,
            requester_id=requester_id,
            owner_id=owner_id,
            created_by=HUMAN_SENDER_ID,
            parent_task_id=body.parent_task_id,
            work_contract=work_contract,
            source_channel=source_channel,
            notification_policy=notification_policy,
            notification_channel_id=body.notification_channel_id,
            audit_author_name="Human Operator",
            audit_author_type="human",
            audit_author_agent_id=None,
            bind_task_id=body.bind_task_id,
        )
    except ValueError as exc:
        if "bind_task_id not found" in str(exc):
            raise HTTPException(404, "Task not found") from exc
        raise
    if creation.outcome == "clarify_ambiguous_match":
        candidates = _task_candidate_summaries(creation.resolution.candidates)
        await manager.broadcast_activity(
            event="task_clarify",
            detail=f'Multiple open tasks match "{body.title}"',
        )
        response.status_code = 409
        return TaskCreateResponse(
            task=None,
            outcome="clarify_ambiguous_match",
            candidates=candidates,
            reason=creation.resolution.reason,
        )
    task = creation.task
    if task is None:
        raise HTTPException(500, "Task create/bind returned no task")
    wake = assignment_wake_trigger(task)
    if wake is not None:
        await runtime_services.enqueue_trigger(**wake)
    await manager.broadcast_activity(
        event="task_created" if creation.outcome == "create_new_task" else "task_reused",
        detail=(
            f"Task \"{task.title}\" created" + (f" → {body.assigned_to}" if body.assigned_to else "")
            if creation.outcome == "create_new_task"
            else f'Existing task "{task.title}" reused for the same workstream'
        ),
    )
    return TaskCreateResponse(task=task, outcome=creation.outcome)


@router.get("/tasks/board")
async def get_task_board(agent_id: str, scope: Literal["self", "owned", "delegated"] = "self"):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return serialize_task_board(build_task_board(agent.id, scope=scope))


@router.get("/tasks/{task_id}/events")
async def get_task_events(task_id: str, limit: int = 100):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    events = db.list_task_events(task_id, limit=limit)
    return [event.model_dump(mode="json") for event in events]


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> Task:
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task
