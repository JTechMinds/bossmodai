"""Role contracts v1 — hire fields, assign mismatch, checkable done claims."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from fastapi.testclient import TestClient

import db
from tests._connections import model_connection
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.actions import execute_action, parse_action
from core.agent_loop.activity_runtime import activate_work_activity
from core.agent_loop.deliverables import missing_deliverables
from core.agent_loop.decision_runtime import apply_decision
from core.agent_loop.role_contracts import (
    format_role_contract_block,
    infer_work_kind,
    match_specialty,
    operator_done_claim_guidance,
    prefer_specialty_match,
    specialty_family,
    suggest_finish_line,
)
from core.agent_loop.runtime_core import AUDIENCE_SOFT_JUDGMENT, CHAT_FORMATTING
from core.llm import context_preview
from db.unified_feed import classify_category
from core.bm_cli.virtual_fs import resolve_cli_path
from core.models import AgentCreate, AgentUpdate
from core.models.message import HUMAN_SENDER_ID
from core.models.work_contract import DeliverableSpec, WorkContract
from core.runtime import runtime_services
from core.tasking import create_or_bind_task


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}") if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


def _record_log_tool_evidence(agent_id: str) -> None:
    db.create_bm_cli_event(
        agent_id=agent_id,
        command="cat /me/note.md",
        content_present=False,
        executor="virtual",
        cwd_before="/",
        cwd_after="/",
        policy_tier="read",
        decision="allowed",
        exit_code=0,
        result_kind="read",
        stdout_preview="ok",
        stderr_preview=None,
        changed_paths=None,
        trigger_type="activity_resumed",
    )


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _persist_trigger(**kwargs: Any) -> None:
        db.create_agent_trigger(
            agent_id=kwargs["agent_id"],
            trigger_type=kwargs["trigger_type"],
            source_channel=kwargs["source_channel"],
            payload=kwargs["payload"],
            task_id=kwargs.get("task_id"),
        )

    monkeypatch.setattr(runtime_services, "enqueue_trigger", _persist_trigger)
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _bind_task(agent_id: str, title: str = "Note", description: str | None = None):
    return create_or_bind_task(
        title=title,
        description=description,
        project=None,
        assigned_to=agent_id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=None,
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )


def _write_me_file(storage_key: str, virtual_path: str, content: str) -> str:
    resolved = resolve_cli_path(storage_key, "/me", virtual_path)
    assert resolved.real_path is not None
    resolved.real_path.parent.mkdir(parents=True, exist_ok=True)
    resolved.real_path.write_text(content, encoding="utf-8")
    return resolved.virtual_path


# ---------------------------------------------------------------------------
# Hire / persistence
# ---------------------------------------------------------------------------


def test_create_and_update_agent_api_persists_specialty_and_done_fail_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    created = client.post(
        "/api/agents",
        headers=_headers(),
        json={
            "name": "Cap Writer",
            "connection_id": model_connection("test/mock"),
            "role": "Writer",
            "description": "Writes first drafts and short status notes.",
            "done_fail_bar": "Good: draft path exists. Fail: empty done.",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Cap Writer"
    assert body["role"] == "Writer"
    assert body["description"] == "Writes first drafts and short status notes."
    assert body["done_fail_bar"] == "Good: draft path exists. Fail: empty done."

    listed = client.get("/api/agents", headers=_headers())
    assert listed.status_code == 200
    row = next(item for item in listed.json() if item["id"] == body["id"])
    assert row["role"] == "Writer"
    assert row["description"] == "Writes first drafts and short status notes."
    assert "empty done" in row["done_fail_bar"]

    company = client.get("/api/company/agents", headers=_headers())
    assert company.status_code == 200
    company_row = next(item for item in company.json() if item["id"] == body["id"])
    assert company_row["role"] == "Writer"
    assert company_row["description"] == body["description"]
    assert company_row["done_fail_bar"] == body["done_fail_bar"]

    patched = client.patch(
        f"/api/agents/{body['id']}",
        headers=_headers(),
        json={
            "role": "Auditor",
            "description": "Reviews packages against a checkable claim.",
            "done_fail_bar": "CLEAR only against a checkable claim.",
        },
    )
    assert patched.status_code == 200
    assert patched.json()["role"] == "Auditor"
    assert patched.json()["description"] == "Reviews packages against a checkable claim."
    assert patched.json()["done_fail_bar"] == "CLEAR only against a checkable claim."

    persisted = db.get_agent(body["id"])
    assert persisted is not None
    assert persisted.role == "Auditor"
    assert persisted.description == "Reviews packages against a checkable claim."
    assert persisted.done_fail_bar == "CLEAR only against a checkable claim."


def test_create_agent_api_allows_blank_done_fail_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    created = client.post(
        "/api/agents",
        headers=_headers(),
        json={
            "name": "Cap Blank", "role": "Writer", "description": "Writes notes.",
            "connection_id": model_connection("test/mock"),
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["role"] == "Writer"
    assert body["description"] == "Writes notes."
    assert body["done_fail_bar"] is None
    persisted = db.get_agent(body["id"])
    assert persisted is not None
    assert persisted.done_fail_bar is None


def test_hire_prompt_prose_is_kept_whole_on_create_and_update() -> None:
    """Description and done bar are the agent's prompt: no cap, no truncation."""
    description = "Mission: " + "d" * 4991
    done = "Good: " + "g" * 1994
    assert len(description) == 5000 and len(done) == 2000
    created = AgentCreate(name="Long Prompt", connection_id="c1", description=description, done_fail_bar=done)
    assert created.description == description
    assert created.done_fail_bar == done
    updated = AgentUpdate(description=description, done_fail_bar=done)
    assert updated.description == description
    assert updated.done_fail_bar == done


def test_over_length_role_is_rejected_not_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Specialty keeps its label cap, and going over it fails loudly (422)."""
    role = "r" * 121
    with pytest.raises(ValidationError, match="role must be 120 characters or fewer"):
        AgentCreate(name="Long Role", connection_id="c1", role=role)
    with pytest.raises(ValidationError, match="role must be 120 characters or fewer"):
        AgentUpdate(role=role)
    assert AgentCreate(name="Edge Role", connection_id="c1", role="r" * 120).role == "r" * 120

    client = _api_client(monkeypatch)
    refused = client.post(
        "/api/agents",
        headers=_headers(),
        json={
            "name": "Long Role", "role": role, "description": "Writes notes.",
            "connection_id": model_connection("test/mock"),
        },
    )
    assert refused.status_code == 422
    assert "role must be 120 characters or fewer" in refused.text


# The agent form is composed from field-group modules (Phase 4 split
# agent-panel.js). Its markup no longer lives in one file, so a source-index
# comparison in any one of them would prove nothing about what the operator
# actually reads. `_form_markup` rebuilds the form in the order buildFormHTML
# composes it — the stronger subject, because it also proves the composition
# order in agent-form.js and not just one file's source order.
_JS = Path("ui/static/js")
_SECTION_OWNERS = {
    "BossModAgentFormFields": "context/agent-form-fields.js",
    "BossModAgentFormAdvanced": "context/agent-form-advanced.js",
    "BossModAgentFormConnections": "context/agent-form-connections.js",
}


def _form_markup() -> str:
    """Every field group's markup, in the order the form renders it."""
    form = (_JS / "context/agent-form.js").read_text(encoding="utf-8")
    template = form.split("container.innerHTML = `", 1)[1].split("\n        `;", 1)[0]
    calls = re.findall(r"\$\{(BossModAgentForm\w+)\.(\w+)\(", template)
    assert calls, "buildFormHTML composes no field groups"
    chunks = []
    for module, fn in calls:
        source = (_JS / _SECTION_OWNERS[module]).read_text(encoding="utf-8")
        assert f"function {fn}(" in source, f"{module}.{fn} is not in {_SECTION_OWNERS[module]}"
        chunks.append(source.split(f"function {fn}(", 1)[1].split("\n    }\n", 1)[0])
    return "\n".join(chunks)


def test_hire_form_keeps_casual_fields_and_moves_finish_line_to_advanced() -> None:
    panel = _form_markup()
    specialty_js = Path("ui/static/js/core/specialty.js").read_text(encoding="utf-8")
    assert 'id="role-contract-card"' in panel
    assert 'name="role"' in panel
    assert "Specialty" in panel
    assert 'placeholder="e.g. Writer, Auditor, Engineer"' in panel
    assert 'name="description"' in panel
    assert "What this agent does" in panel
    assert 'name="done_fail_bar"' in panel
    assert "What “done” looks like" in panel
    assert "Optional. We’ll suggest one from the specialty; edit anytime." in panel
    assert "Suggest" in panel
    assert "Done/fail bar" not in panel
    assert "done/fail bar" not in panel.lower()
    assert "KPI" not in panel
    assert "SLA" not in panel
    # What the server is told moved to context/agent-submit.js with the split.
    submit = Path("ui/static/js/context/agent-submit.js").read_text(encoding="utf-8")
    assert "done_fail_bar: formData.get('done_fail_bar')" in submit
    assert "communication: BossModCommunication.resolve({" in submit
    assert "first_unoccupied_chair" not in panel
    assert "No empty desk is free" in panel
    assert "An empty desk is selected when one is free." in panel
    assert "description: formData.get('description')" in submit
    bindings = Path("ui/static/js/context/agent-form-bindings.js").read_text(encoding="utf-8")
    assert "bindFinishLineSuggestion" in bindings
    assert "lastSuggested" in bindings
    assert "applySuggestion({ force: true })" in bindings
    # ...and the form is what actually calls it. A binding nothing invokes is
    # the same as no binding at all.
    assert "bindFinishLineSuggestion" in Path(
        "ui/static/js/context/agent-form.js"
    ).read_text(encoding="utf-8")
    assert 'id="advanced-toggle"' in panel
    assert "Advanced" in panel
    # AI Personalities are retired: the form has no personality control.
    assert 'id="agent-personality-mount"' not in panel
    assert "suggestFinishLine" in specialty_js
    assert "A named draft or document exists. Empty done does not count." in specialty_js
    assert panel.index('name="name"') < panel.index('name="role"')
    assert panel.index('name="role"') < panel.index('name="description"')
    # `<legend>`, not `<label>`: Color is a radio GROUP, and the heading over
    # one is a legend inside its fieldset. It was a <label> pointing at no
    # control at all — the marker moved, the ordering property did not.
    assert panel.index('name="description"') < panel.index(">Color</legend>")
    assert panel.index(">Color</legend>") < panel.index("Advanced")
    assert panel.index("Advanced") < panel.index('name="done_fail_bar"')
    assert panel.index('name="done_fail_bar"') < panel.index("Desk Assignment")
    assert panel.index("Advanced") < panel.index("Desk Assignment")
    assert panel.index('name="description"') < panel.index("Desk Assignment")
    assert "nextUnusedAgentColor" in panel
    assert "runtime core, and desk" in panel
    assert "prompt template" not in panel
    assert "prompt template, color, and desk" not in panel
    assert "Runtime core" in panel
    assert 'id="runtime-core-preview"' in panel
    assert 'name="runtime_core"' not in panel
    assert panel.index('name="done_fail_bar"') < panel.index("${communicationFields()}")
    assert panel.index("${communicationFields()}") < panel.index("Runtime core")
    # `values`, not `agent`: the same fields are shown for an agent being
    # edited and for a snapshot being recreated (spec 2026-09-22 §5.3). The
    # dropdowns take their starting values from the mount, so that is where
    # `values` goes.
    form_js = Path("ui/static/js/context/agent-form.js").read_text(encoding="utf-8")
    assert "BossModAgentFormChoices.mount(form, { roster, values });" in form_js
    advanced = Path("ui/static/js/context/agent-form-advanced.js").read_text(encoding="utf-8")
    assert 'id="agent-communication-${field}-mount"' in advanced
    choices = Path("ui/static/js/context/agent-form-choices.js").read_text(encoding="utf-8")
    assert "name: `communication_${key}`" in choices
    submit_js = Path("ui/static/js/context/agent-submit.js").read_text(encoding="utf-8")
    for key in ("tone", "density", "jargon", "audience"):
        assert f"communication_{key}" in submit_js
    assert panel.index("Runtime core") < panel.index("Desk Assignment")
    agent_status_js = Path("ui/static/js/core/agent-status.js").read_text(encoding="utf-8")
    assert "nextUnusedAgentColor" in agent_status_js
    assert "mergeRosterFromWorld" in agent_status_js
    # Re-pointed in Phase 3A: the assign sheet is places/tasks/assign-form.js and
    # its result panels are places/tasks/assign-outcomes.js. The id literals are
    # h() attributes now rather than markup, so `id="x"` reads `id: 'x'`; the
    # three ids and their source order are unchanged, which is the property —
    # the mismatch warning sits between the assignee and the description.
    assign_js = Path("ui/static/js/places/tasks/assign-form.js").read_text(encoding="utf-8")
    outcomes_js = Path("ui/static/js/places/tasks/assign-outcomes.js").read_text(encoding="utf-8")
    assert "specialty_mismatch" in outcomes_js
    assert "confirm_specialty_mismatch" in assign_js
    assert "ct-assign-mismatch" in assign_js
    assert "specialtyWarningMessage" in assign_js
    assert "(matches)" in assign_js
    assert "(mismatch)" in assign_js
    assert assign_js.index("id: 'ct-assign-agent'") < assign_js.index("id: 'ct-assign-mismatch'")
    assert assign_js.index("id: 'ct-assign-mismatch'") < assign_js.index("id: 'ct-assign-description'")
    # Re-pointed in Phase 3A: the detail panel is places/tasks/task-detail.js.
    # Re-pointed again when the detail was split: task-detail.js composes the
    # modal and task-detail-sections.js builds what is in it, so the copy is
    # asserted over both — it must reach the detail, whichever file holds it.
    detail_js = "\n".join(
        Path(f"ui/static/js/places/tasks/{name}").read_text(encoding="utf-8")
        for name in ("task-detail.js", "task-detail-sections.js")
    )
    assert "doneClaimGuidance" in detail_js
    assert "Blocked — checkable claim missing" in detail_js
    assert "Done claim" in detail_js
    assert "allow/deny proof" in detail_js
    assert "What done looks like:" in detail_js
    assert "Done/fail bar" not in detail_js
    assert "What done looks like for this agent:" in specialty_js
    # Re-pointed in Phase 3B: activity.js and diagnostics.js merged into the
    # Log place. The old assertion was that `world_feedback` appeared in a
    # twenty-five entry icon map, which is what made a role-contract breach
    # visible in the feed. The Log enumerates no event names at all — it reads
    # the category the server already classified — so the property is asserted
    # where it actually lives now: every category classify_category can return
    # has a row type, and an unrecognised one becomes `system` rather than
    # being dropped off the feed.
    log_shape_js = Path("ui/static/js/places/log/log-shape.js").read_text(encoding="utf-8")
    for category in ("agent", "task", "error", "system"):
        assert f"'{category}'" in log_shape_js
    assert "const TYPES = Object.freeze(['agent', 'task', 'error', 'system']);" in log_shape_js
    assert "type: TYPES.indexOf(category) === -1 ? 'system' : category," in log_shape_js
    assert classify_category("activity_log", "world_feedback") == "task"
    # Phase 2B replaced agent-context.js with the context column, and the desk
    # is a modal now. The desk panel carries the agent's own contract copy.
    # The per-task claim copy left the desk's task rows — every open row
    # repeated it — for the task detail's contract section, one click away
    # from a row; that is where doneClaimGuidance takes a task now.
    panel_js = Path("ui/static/js/context/desk-panel.js").read_text(encoding="utf-8")
    assert "No specialty" in panel_js
    assert "who.description" in panel_js
    assert "done_fail_bar" in panel_js
    detail_sections_js = Path(
        "ui/static/js/places/tasks/task-detail-sections.js").read_text(encoding="utf-8")
    assert "doneClaimGuidance" in detail_sections_js
    assert "Blocked — checkable claim missing" in detail_sections_js
    board = Path("core/tasking/board.py").read_text(encoding="utf-8")
    assert "done_claim_guidance" in board
    assert "operator_done_claim_guidance" in board


# ---------------------------------------------------------------------------
# Assign / routing
# ---------------------------------------------------------------------------


def test_create_agent_api_auto_assigns_an_unoccupied_desk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    conn_id = model_connection("test/mock")
    first = client.post("/api/agents", headers=_headers(), json={"name": "Desk One", "connection_id": conn_id})
    second = client.post("/api/agents", headers=_headers(), json={"name": "Desk Two", "connection_id": conn_id})
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["desk_x"] is not None
    assert first.json()["desk_y"] is not None
    first_state = db.get_agent_state(first.json()["id"])
    assert first_state is not None
    assert (first_state.x, first_state.y) == (first.json()["desk_x"], first.json()["desk_y"])
    assert (second.json()["desk_x"], second.json()["desk_y"]) != (
        first.json()["desk_x"],
        first.json()["desk_y"],
    )

    explicit = client.post(
        "/api/agents",
        headers=_headers(),
        json={"name": "Desk Pick", "desk_x": 11, "desk_y": 4, "connection_id": conn_id},
    )
    assert explicit.status_code == 201
    assert explicit.json()["desk_x"] == 11
    assert explicit.json()["desk_y"] == 4

    taken = {(first.json()["desk_x"], first.json()["desk_y"]), (11, 4)}
    assert (second.json()["desk_x"], second.json()["desk_y"]) not in taken


def test_update_agent_api_auto_assigns_desk_when_unassigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    seated = db.create_agent("Seated", desk_x=3, desk_y=4)
    open_seat = client.post(
        "/api/agents", headers=_headers(),
        json={"name": "Needs Desk", "connection_id": model_connection("test/mock")},
    )
    assert open_seat.status_code == 201
    # Recreate an unassigned agent through the DB, then PATCH via API.
    wanderer = db.create_agent("Wanderer")
    assert wanderer.desk_x is None
    patched = client.patch(
        f"/api/agents/{wanderer.id}",
        headers=_headers(),
        json={"name": "Wanderer Seated"},
    )
    assert patched.status_code == 200
    assert patched.json()["desk_x"] is not None
    assert patched.json()["desk_y"] is not None
    seated_state = db.get_agent_state(wanderer.id)
    assert seated_state is not None
    assert (seated_state.x, seated_state.y) == (
        patched.json()["desk_x"],
        patched.json()["desk_y"],
    )
    assert (patched.json()["desk_x"], patched.json()["desk_y"]) != (seated.desk_x, seated.desk_y)
    assert (patched.json()["desk_x"], patched.json()["desk_y"]) != (
        open_seat.json()["desk_x"],
        open_seat.json()["desk_y"],
    )


def test_create_agent_api_leaves_desk_unassigned_when_all_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.world.tilemap import DEFAULT_DESKS

    client = _api_client(monkeypatch)
    conn_id = model_connection("test/mock")
    for index, desk in enumerate(DEFAULT_DESKS):
        chair = desk["chair_xy"]
        created = client.post(
            "/api/agents",
            headers=_headers(),
            json={"name": f"Seated {index}", "desk_x": chair[0], "desk_y": chair[1], "connection_id": conn_id},
        )
        assert created.status_code == 201
    extra = client.post("/api/agents", headers=_headers(), json={"name": "Standing", "connection_id": conn_id})
    assert extra.status_code == 201
    assert extra.json()["desk_x"] is None
    assert extra.json()["desk_y"] is None


def test_suggest_finish_line_uses_specialty_then_description() -> None:
    assert suggest_finish_line("Writer") == (
        "A named draft or document exists. Empty done does not count."
    )
    assert suggest_finish_line("Auditor") == (
        "A checkable allow/deny (or tests/artifact) exists. Empty done does not count."
    )
    assert suggest_finish_line("Engineer") == (
        "Tests evidence or a named artifact exists. Empty done does not count."
    )
    assert suggest_finish_line("Lead") == (
        "A named plan or status note exists. Empty done does not count."
    )
    assert suggest_finish_line(None, "Draft a short status note") == (
        "A named draft or document exists. Empty done does not count."
    )
    assert suggest_finish_line("Custom role", None) == (
        "A checkable claim exists (tests, artifact, or allow/deny). Empty done does not count."
    )
    # Specialty wins when it maps; description does not silently replace it.
    assert suggest_finish_line("Writer", "Review the audit package") == (
        "A named draft or document exists. Empty done does not count."
    )


def test_specialty_inference_is_conservative() -> None:
    assert specialty_family("Writer") == "write"
    assert specialty_family("Lead") == "coordinate"
    assert infer_work_kind("Write the status note", None) == "write"
    assert infer_work_kind("Review the audit package", None) == "review"
    assert infer_work_kind("Coordinate the rollout", None) is None
    assert match_specialty(assignee_role="Writer", work_kind="write") == "match"
    assert match_specialty(assignee_role="Writer", work_kind="review") == "mismatch"
    assert match_specialty(assignee_role="Lead", work_kind="write") == "unknown"
    assert match_specialty(assignee_role="Eng", work_kind="write") == "unknown"


def test_assign_api_soft_denies_specialty_mismatch_until_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    auditor = db.create_agent("Cap Auditor", role="Auditor", desk_x=2, desk_y=1)

    denied = client.post(
        "/api/tasks",
        headers=_headers(),
        json={
            "title": "Review the security audit",
            "description": "Audit the package and report findings.",
            "assigned_to": writer.id,
        },
    )
    assert denied.status_code == 409
    body = denied.json()
    assert body["outcome"] == "specialty_mismatch"
    assert body["task"] is None
    assert "Writer" in (body["reason"] or "")
    assert any(item["id"] == auditor.id for item in body["suggested_assignees"])
    assert db.list_tasks() == []

    confirmed = client.post(
        "/api/tasks",
        headers=_headers(),
        json={
            "title": "Review the security audit",
            "description": "Audit the package and report findings.",
            "assigned_to": writer.id,
            "confirm_specialty_mismatch": True,
        },
    )
    assert confirmed.status_code == 201
    assert confirmed.json()["outcome"] == "create_new_task"
    assert confirmed.json()["specialty_warning"]
    assert db.list_tasks(assigned_to=writer.id)


def test_assign_api_matching_specialty_creates_without_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)

    created = client.post(
        "/api/tasks",
        headers=_headers(),
        json={
            "title": "Write the status note",
            "description": "Draft a short status note.",
            "assigned_to": writer.id,
        },
    )
    assert created.status_code == 201
    assert created.json()["outcome"] == "create_new_task"
    assert created.json()["specialty_warning"] is None
    assert created.json()["task"]["assigned_to"] == writer.id


def test_unassigned_create_prefers_matching_specialty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    auditor = db.create_agent("Cap Auditor", role="Auditor", desk_x=2, desk_y=1)

    created = client.post(
        "/api/tasks",
        headers=_headers(),
        json={"title": "Review the security audit"},
    )
    assert created.status_code == 201
    suggested = created.json()["suggested_assignees"]
    assert suggested
    assert suggested[0]["id"] == auditor.id
    assert suggested[0]["match"] == "match"


@pytest.mark.asyncio
async def test_delegate_task_mismatch_is_world_feedback() -> None:
    assigner = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=2, desk_y=1)
    auditor = db.create_agent("Cap Auditor", role="Auditor", desk_x=3, desk_y=1)
    state = db.get_agent_state(assigner.id)
    assert state is not None

    denied = await execute_action(
        {
            "action": "delegateTask",
            "agentId": writer.id,
            "taskTitle": "Review the security audit",
            "taskDescription": "Audit the package.",
        },
        assigner,
        state,
    )
    assert denied["event"] == "world_feedback"
    assert "Writer" in denied["detail"]
    assert any(item["id"] == auditor.id for item in denied["suggested_assignees"])
    assert db.list_tasks() == []

    confirmed = await execute_action(
        {
            "action": "delegateTask",
            "agentId": writer.id,
            "taskTitle": "Review the security audit",
            "taskDescription": "Audit the package.",
            "confirmSpecialtyMismatch": True,
        },
        assigner,
        state,
    )
    assert confirmed["event"] == "status_changed"
    assert confirmed.get("specialty_warning")
    assert db.list_tasks(assigned_to=writer.id)


def test_work_plan_prefers_matching_specialty_on_duplicate_names() -> None:
    lead = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    writer = db.create_agent("Cap Worker", role="Writer", desk_x=2, desk_y=1)
    auditor = db.create_agent("Cap Worker", role="Auditor", desk_x=3, desk_y=1)
    preferred = prefer_specialty_match(
        [writer, auditor],
        title="Write the status note",
        description="Draft the note.",
    )
    assert preferred is not None
    assert preferred.id == writer.id

    state = db.get_agent_state(lead.id)
    assert state is not None
    parent = db.create_task(title="Coordinate the note", assigned_to=lead.id, created_by=lead.id)
    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": parent.title,
            "reply": "Cap Worker can take the note.",
            "executionPlan": {
                "mode": "delegate",
                "delegations": [{"agentName": "Cap Worker", "taskTitle": "Write the status note"}],
            },
        },
        lead,
        state,
        {
            "type": "task_assigned",
            "task_id": parent.id,
            "content": "Coordinate the note",
            "from_name": "Operator",
        },
    )
    assert result["event"] == "decision_applied"
    children = db.list_tasks(parent_task_id=parent.id)
    assert len(children) == 1
    assert children[0].assigned_to == writer.id


def test_work_plan_mismatch_does_not_create_child() -> None:
    lead = db.create_agent("Cap Assigner", role="Lead", desk_x=1, desk_y=1)
    writer = db.create_agent("Cap Writer", role="Writer", desk_x=2, desk_y=1)
    state = db.get_agent_state(lead.id)
    assert state is not None
    parent = db.create_task(title="Coordinate the audit", assigned_to=lead.id, created_by=lead.id)

    result = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": parent.title,
            "reply": "Cap Writer can take the audit.",
            "executionPlan": {
                "mode": "delegate",
                "delegations": [
                    {
                        "agentId": writer.id,
                        "taskTitle": "Review the security audit",
                    }
                ],
            },
        },
        lead,
        state,
        {
            "type": "task_assigned",
            "task_id": parent.id,
            "content": "Coordinate the audit",
            "from_name": "Operator",
        },
    )
    assert result["event"] == "world_feedback"
    assert "Writer" in result["detail"]
    refreshed = db.get_task(parent.id)
    assert refreshed is not None
    assert refreshed.status != "accepted"
    assert db.list_tasks(parent_task_id=parent.id) == []


# ---------------------------------------------------------------------------
# Checkable done claims
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_without_checkable_claim_is_rejected() -> None:
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Write a note")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {"action": "complete", "summary": "Finished."},
        agent,
        state,
    )
    assert result["event"] == "world_feedback"
    assert "checkable claim" in result["detail"].lower()
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


@pytest.mark.asyncio
async def test_auditor_clear_without_claim_is_rejected() -> None:
    agent = db.create_agent("Cap Auditor", role="Auditor", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Review the package")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {"action": "complete", "summary": "Looks good."},
        agent,
        state,
    )
    assert result["event"] == "world_feedback"
    assert "clear" in result["detail"].lower()
    assert "checkable claim" in result["detail"].lower()
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


@pytest.mark.asyncio
async def test_complete_with_tests_claim_succeeds() -> None:
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Write a note")
    activate_work_activity(agent.id, creation.task)
    _record_log_tool_evidence(agent.id)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Tests passed.",
            "doneClaim": {"type": "tests", "evidence": "pytest tests/test_role_contracts.py: 12 passed"},
        },
        agent,
        state,
    )
    assert result["event"] == "status_changed"
    assert result["done_claim"]["type"] == "tests"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"


@pytest.mark.asyncio
async def test_complete_reviewed_without_log_tool_evidence_is_rejected() -> None:
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Review the package")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Reviewed the codebase.",
            "doneClaim": {"type": "proof", "ev": "reviewed the codebase"},
        },
        agent,
        state,
    )
    assert result["event"] == "world_feedback"
    assert "tool evidence" in result["detail"].lower()
    assert "chat assertion" in result["detail"].lower()
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


@pytest.mark.asyncio
async def test_auditor_clear_reviewed_without_log_tool_evidence_is_rejected() -> None:
    agent = db.create_agent("Hugh", role="Auditor", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Review the package")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Reviewed the codebase.",
            "doneClaim": {"type": "proof", "ev": "reviewed the codebase"},
        },
        agent,
        state,
    )
    assert result["event"] == "world_feedback"
    assert "clear" in result["detail"].lower()
    assert "tool evidence" in result["detail"].lower()
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


@pytest.mark.asyncio
async def test_complete_with_missing_artifact_claim_is_rejected() -> None:
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    creation = _bind_task(agent.id, title="Write a note")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Wrote the note.",
            "doneClaim": {"type": "artifact", "path": "/me/missing-note.md"},
        },
        agent,
        state,
    )
    assert result["event"] == "world_feedback"
    assert "does not exist" in result["detail"].lower()
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status != "complete"


@pytest.mark.asyncio
async def test_complete_with_existing_artifact_claim_succeeds() -> None:
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    path = _write_me_file(agent.storage_key, "/me/note.md", "draft")
    creation = _bind_task(agent.id, title="Write a note")
    activate_work_activity(agent.id, creation.task)

    result = await execute_action(
        {
            "action": "complete",
            "summary": "Wrote the note.",
            "doneClaim": {"type": "artifact", "path": path},
        },
        agent,
        state,
    )
    assert result["event"] == "status_changed"
    assert result["done_claim"]["type"] == "artifact"
    refreshed = db.get_task(creation.task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"


# ---------------------------------------------------------------------------
# File deliverables: the file exists and was modified since the task began
# ---------------------------------------------------------------------------


def _bind_contract_task(agent, path: str):
    """An operator task for ``agent`` that must produce ``path``."""
    creation = create_or_bind_task(
        title="Write the report",
        description=None,
        project=None,
        assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID,
        owner_id=None,
        created_by=HUMAN_SENDER_ID,
        parent_task_id=None,
        work_contract=WorkContract(deliverables=[DeliverableSpec(type="file", path=path)]),
        source_channel=None,
        notification_policy=None,
        notification_channel_id=None,
        audit_author_name="Human Operator",
        audit_author_type="human",
    )
    assert creation.task is not None
    return creation.task


def test_a_shell_produced_file_satisfies_its_deliverable() -> None:
    """No BossMod CLI write event is needed: a file a shell command made counts."""
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _bind_contract_task(agent, "/me/out/report.md")
    assert [item.path for item in missing_deliverables(agent_storage_key=agent.storage_key, task=task)] == [
        "/me/out/report.md"
    ]
    _write_me_file(agent.storage_key, "/me/out/report.md", "written by cp")
    assert db.list_bm_cli_events(agent.id) == []
    assert missing_deliverables(agent_storage_key=agent.storage_key, task=task) == []


def test_a_teammate_written_file_satisfies_the_assignees_deliverable() -> None:
    """Who wrote the shared file does not matter, only that it exists for this task."""
    owner = db.create_agent("Cap Owner", role="Writer", desk_x=1, desk_y=1)
    teammate = db.create_agent("Cap Mate", role="Writer", desk_x=2, desk_y=1)
    task = _bind_contract_task(owner, "/projects/shared/brief.md")
    resolved = resolve_cli_path(teammate.storage_key, "/", "/projects/shared/brief.md")
    assert resolved.real_path is not None
    resolved.real_path.parent.mkdir(parents=True, exist_ok=True)
    resolved.real_path.write_text("teammate draft", encoding="utf-8")
    assert missing_deliverables(agent_storage_key=owner.storage_key, task=task) == []


def test_a_file_older_than_the_task_does_not_satisfy() -> None:
    """A stale file left from earlier work is not this task's output."""
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _bind_contract_task(agent, "/me/out/report.md")
    _write_me_file(agent.storage_key, "/me/out/report.md", "last month's report")
    real = resolve_cli_path(agent.storage_key, "/", "/me/out/report.md").real_path
    assert real is not None
    stale = task.created_at.timestamp() - 3600
    os.utime(real, (stale, stale))
    assert [item.path for item in missing_deliverables(agent_storage_key=agent.storage_key, task=task)] == [
        "/me/out/report.md"
    ]


def test_a_contract_edit_after_the_write_still_satisfies() -> None:
    """Editing the requirements never invalidates work already done for the task."""
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    task = _bind_contract_task(agent, "/me/out/report.md")
    _write_me_file(agent.storage_key, "/me/out/report.md", "done")
    edited = db.update_task(
        task.id,
        work_contract=WorkContract(
            deliverables=[DeliverableSpec(type="file", path="/me/out/report.md", description="The final report")]
        ),
    )
    assert edited is not None
    assert missing_deliverables(agent_storage_key=agent.storage_key, task=edited) == []


@pytest.mark.asyncio
async def test_done_with_a_shell_produced_deliverable_completes() -> None:
    """The agent that was stuck behind the CLI-write rule can now close its task."""
    agent = db.create_agent("Cap Writer", role="Writer", desk_x=1, desk_y=1)
    state = db.get_agent_state(agent.id)
    assert state is not None
    task = _bind_contract_task(agent, "/me/out/report.md")
    activate_work_activity(agent.id, task)
    _write_me_file(agent.storage_key, "/me/out/report.md", "written by a script")

    result = await execute_action({"action": "complete", "summary": "Report written."}, agent, state)

    assert result["event"] == "status_changed"
    assert result["done_claim"] == {"type": "artifact", "path": "/me/out/report.md"}
    refreshed = db.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "complete"


def test_parse_done_action_keeps_claim() -> None:
    parsed = parse_action(
        '{"act":"done","data":{"sum":"Draft saved.","msg":"Finished.","claim":{"type":"tests","ev":"12 passed"}},"th":"complete"}'
    )
    assert parsed["action"] == "complete"
    assert parsed["doneClaim"]["type"] == "tests"
    assert parsed["doneClaim"]["ev"] == "12 passed"


def test_role_contract_block_and_done_claim_guidance_are_operator_actionable() -> None:
    agent = db.create_agent(
        "Cap Writer",
        role="Writer",
        description="Writes first drafts and short status notes.",
        done_fail_bar="Good: draft path exists. Fail: empty done.",
        desk_x=1,
        desk_y=1,
    )
    block = format_role_contract_block(agent)
    assert "# Role contract" in block
    assert "Specialty: Writer" in block
    assert "Description: Writes first drafts and short status notes." in block
    assert "Communication:" in block
    assert "- tone: product-clear" in block
    assert "- density: scannable" in block
    assert "Good: draft path exists" in block
    assert "data.claim" in block
    assert "Empty done is rejected" in block
    guidance = operator_done_claim_guidance(
        auditor=False,
        done_fail_bar=agent.done_fail_bar,
        has_file_deliverables=False,
    )
    assert "tests evidence" in guidance
    assert "artifact path" in guidance
    assert "allow/deny proof" in guidance
    assert "Good: draft path exists" in guidance


def test_task_list_includes_assignee_contract_and_done_claim_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    writer = db.create_agent(
        "Cap Writer",
        role="Writer",
        done_fail_bar="Good: draft path exists. Fail: empty done.",
        desk_x=1,
        desk_y=1,
    )
    _bind_task(writer.id, title="Write a note")
    listed = client.get("/api/tasks", headers=_headers())
    assert listed.status_code == 200
    row = next(item for item in listed.json() if item["assigned_to"] == writer.id)
    assert row["assigned_to_role"] == "Writer"
    assert row["assigned_to_done_fail_bar"] == "Good: draft path exists. Fail: empty done."
    assert "checkable claim" in row["done_claim_guidance"]
    assert "Good: draft path exists" in row["done_claim_guidance"]
    assert row["done_claim"] is None


def test_preview_bundle_injects_role_contract() -> None:
    preview = context_preview.preview_prompt_bundle("execution", "activity_resumed")
    contents = "\n".join(str(message.get("content") or "") for message in preview["messages"])
    assert "# Role contract" in contents
    assert "# Runtime core" in contents
    assert "Specialty:" in contents
    assert "data.claim" in contents
    assert "Empty done" in contents
    core_msgs = [
        message.get("content") or ""
        for message in preview["messages"]
        if str(message.get("content") or "").startswith("# Runtime core")
    ]
    assert core_msgs
    assert "Description:" not in core_msgs[0]
    assert "request_host_access" in core_msgs[0]
    assert (
        "Workspace: /me is your private scratch and /projects is shared "
        "with your floor — prefer them."
    ) in core_msgs[0]
    assert "Do not ask the operator for a verbal yes/no." in core_msgs[0]
    assert "stop and ask in chat" not in core_msgs[0]
    assert AUDIENCE_SOFT_JUDGMENT in core_msgs[0]
    assert AUDIENCE_SOFT_JUDGMENT in contents
    assert CHAT_FORMATTING in core_msgs[0]
    assert CHAT_FORMATTING in contents


def test_world_feedback_is_a_task_feed_event() -> None:
    assert classify_category("activity_log", "world_feedback") == "task"
