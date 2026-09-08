"""Hire UI poke: casual color, create dismiss, live roster upsert."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop import activity_runtime
from core.runtime import runtime_services
from core.world.seating import heal_desk_seats
from core.world.simulation import simulation

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_hire_roster_harness.cjs"


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


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


# The agent form is composed from field-group modules (Phase 4 split
# agent-panel.js). Its markup no longer lives in one file, so a source-index
# comparison in any one of them would prove nothing about what the operator
# actually reads. `_form_markup` rebuilds the form in the order
# buildFormHTML composes it, which is strictly the stronger subject: it now
# also proves the composition order in agent-form.js, not just the source
# order of one file.
SECTION_OWNERS = {
    "BossModAgentFormFields": "context/agent-form-fields.js",
    "BossModAgentFormAdvanced": "context/agent-form-advanced.js",
    "BossModAgentFormConnections": "context/agent-form-connections.js",
}


def _form_markup() -> str:
    """Every field group's markup, in the order the form renders it."""
    form = _read("context/agent-form.js")
    template = form.split("container.innerHTML = `", 1)[1].split("\n        `;", 1)[0]
    calls = re.findall(r"\$\{(BossModAgentForm\w+)\.(\w+)\(", template)
    assert calls, "buildFormHTML composes no field groups"
    chunks = []
    for module, fn in calls:
        source = _read(SECTION_OWNERS[module])
        assert f"function {fn}(" in source, f"{module}.{fn} is not in {SECTION_OWNERS[module]}"
        chunks.append(source.split(f"function {fn}(", 1)[1].split("\n    }\n", 1)[0])
    return "\n".join(chunks)


def _headers() -> dict[str, str]:
    return {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _persist_trigger(**kwargs):
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


def test_casual_hire_shows_color_under_description() -> None:
    panel = _form_markup()
    assert 'name="description"' in panel
    assert ">Color</label>" in panel
    assert panel.index('name="description"') < panel.index(">Color</label>")
    assert panel.index(">Color</label>") < panel.index('id="advanced-toggle"')
    assert panel.index(">Color</label>") < panel.index('name="done_fail_bar"')
    assert panel.index('id="advanced-toggle"') < panel.index("Desk Assignment")
    assert "nextUnusedAgentColor" in panel
    assert "runtime core, prompt template, and desk" in panel
    assert "prompt template, color, and desk" not in panel


def test_create_agent_submit_is_gated_and_warns_on_duplicate_name() -> None:
    # Re-pointed by Phase 4's split: the gate and the submit ordering are
    # context/agent-edit.js's, the two markup ids and the disabled styling are
    # the field groups', and the duplicate-name binding is agent-form.js's.
    # Every assertion below is the one that was here before.
    panel = _read("context/agent-edit.js")
    markup = _form_markup()
    gates = _read("core/gates.js")
    assert "createInFlightGate()" in gates
    assert "const hireSubmit = BossModGates.createInFlightGate()" in panel
    assert "if (hireSubmit.busy()) return;" in panel
    # Round four pinned the primary in the DIALOG's action row, outside the
    # form it submits, because the operator could not find it at the bottom of
    # a scrolling form. The id this pins and the disabled styling moved with
    # it — the id onto the dialog's action descriptor, the styling onto
    # `.modal-action:disabled`, which is what the Tailwind pair carried.
    assert "id: 'agent-form-submit'" in panel
    assert "form: 'agent-form'" in panel
    overlays_css = (ROOT / "ui" / "static" / "css" / "overlays.css").read_text(encoding="utf-8")
    assert "pointer-events: none" in overlays_css
    assert "Creating…" in panel
    assert "id=\"agent-name-duplicate-warn\"" in markup
    assert "bindDuplicateNameWarning" in _read("context/agent-form.js")
    assert "function bindDuplicateNameWarning(" in _read("context/agent-form-bindings.js")
    submit = panel.split("form.addEventListener('submit', async (e) => {", 1)[1].split(
        "if (deleteBtn)", 1
    )[0]
    assert "hireSubmit.run(" in submit
    assert submit.index("if (hireSubmit.busy()) return;") < submit.index("hireSubmit.run(")
    assert submit.index("submitBtn.disabled = true") < submit.index("apiCreateAgent")


def test_successful_create_dismisses_hire_form() -> None:
    """Re-pointed to context/agent-edit.js, which hosts the form in Phase 2B.

    Same property, same order: whether this was a CREATE is captured before the
    save, and only a create closes the form and selects the new agent. Editing
    must leave the operator where they were.
    """
    source = _read("context/agent-edit.js")
    # Phase 4 split agent-panel.js away; renderInline is this module's own now.
    assert "void renderInline(formEl, agent || null, onSave, onDelete)" in source
    # Captured at construction, before any save can land.
    assert "const wasCreating = !agent;" in source
    on_save = source.split("function onSave(savedAgent) {", 1)[1].split(
        "function onDelete()", 1
    )[0]
    assert "savedAgent && wasCreating" in on_save
    assert "conversationId: savedAgent.id" in on_save
    assert "conversationKind: 'agent'" in on_save
    # The form closes either way; only a create moves the conversation. Round
    # three made the form a dialog, so "the form closes" is the dialog closing
    # rather than the host being told to put its own view back.
    assert "modal.close();" in on_save
    assert on_save.index("savedAgent && wasCreating") < on_save.index("modal.close();")
    assert source.index("const wasCreating = !agent;") < source.index(
        "function onSave(savedAgent) {"
    )


def test_directory_and_org_upsert_world_roster() -> None:
    # Re-pointed in Phase 4: company-view.js hosted the directory and was
    # deleted with the rest of the dock era. The rail is the directory now, and
    # it keeps the same two properties — the snapshot is MERGED rather than
    # replacing what is on screen, and an empty incoming roster is still
    # applied so a removed agent actually disappears.
    directory = _read("shell/roster.js")
    handler = directory.split("bus.subscribe('world_update', (world) => {", 1)[1].split(
        "bus.subscribe('resync'", 1
    )[0]
    assert "BossModAgentStatus.mergeRosterFromWorld" in handler
    assert "!roster.length" not in handler
    assert "roster.map(item =>" not in handler

    # Re-pointed to places/office/org-view.js, which hosts the chart in
    # Phase 3A. Same assertions: the world snapshot is merged rather than
    # replaced, a membership change rebuilds the grid, and an empty incoming
    # roster is still applied — bailing on it would freeze the last agent on
    # screen after they were removed.
    org = _read("places/office/org-view.js")
    org_handler = org.split("function handleWorldUpdate(incomingAgents) {", 1)[1].split(
        "function updateCardStatus(agent) {", 1
    )[0]
    assert "BossModAgentStatus.mergeRosterFromWorld" in org_handler
    assert "membershipChanged" in org_handler
    assert "renderGrid()" in org_handler
    assert "incomingAgents.length === 0" not in org_handler


def test_hire_roster_harness_color_and_upsert() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "agent-status.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "firstUnused": True,
        "nextUnusedSkipsTaken": True,
        "rotatesWhenFull": True,
        "emptyRosterAcceptsCreate": True,
        "upsertsMembership": True,
        "dropsMissing": True,
        "preservesExtras": True,
    }


def test_late_desk_assign_seats_agent_at_chair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    spawn_x = config.get_int("default_spawn_x")
    spawn_y = config.get_int("default_spawn_y")
    wanderer = db.create_agent("Hall Wanderer")
    state = db.get_agent_state(wanderer.id)
    assert wanderer.desk_x is None
    assert state is not None
    assert (state.x, state.y) == (spawn_x, spawn_y)

    patched = client.patch(
        f"/api/agents/{wanderer.id}",
        headers=_headers(),
        json={"desk_x": 11, "desk_y": 4},
    )
    assert patched.status_code == 200
    assert patched.json()["desk_x"] == 11
    assert patched.json()["desk_y"] == 4
    seated = db.get_agent_state(wanderer.id)
    assert seated is not None
    assert (seated.x, seated.y) == (11, 4)
    world = db.get_world_state()
    row = next(item for item in world if item["id"] == wanderer.id)
    assert (row["x"], row["y"]) == (11, 4)
    assert row["location"] == "Main Workspace"


def test_world_state_includes_created_agent_and_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _api_client(monkeypatch)
    created = client.post(
        "/api/agents",
        headers=_headers(),
        json={"name": "Poke Nova", "role": "Writer", "color": "#f59e0b"},
    )
    assert created.status_code == 201
    agent_id = created.json()["id"]

    world = db.get_world_state()
    row = next(item for item in world if item["id"] == agent_id)
    assert row["name"] == "Poke Nova"
    assert row["color"] == "#f59e0b"
    assert row["location"]

    company = client.get("/api/company/agents", headers=_headers())
    assert company.status_code == 200
    company_row = next(item for item in company.json() if item["id"] == agent_id)
    assert company_row["name"] == "Poke Nova"
    assert company_row["location"] == row["location"]


def test_seat_heal_moves_hallway_desk_agent_to_chair() -> None:
    spawn_x = config.get_int("default_spawn_x")
    spawn_y = config.get_int("default_spawn_y")
    drifted = db.create_agent("Hall Drift")
    seated = db.update_agent(drifted.id, desk_x=11, desk_y=4)
    assert seated is not None
    state = db.get_agent_state(drifted.id)
    assert state is not None
    assert (state.x, state.y) == (spawn_x, spawn_y)
    assert db.get_world_state()  # world build heals desk≠hallway body
    healed = db.get_agent_state(drifted.id)
    assert healed is not None
    assert (healed.x, healed.y) == (11, 4)
    row = next(item for item in db.get_world_state() if item["id"] == drifted.id)
    assert (row["x"], row["y"]) == (11, 4)
    assert row["location"] == "Main Workspace"


@pytest.mark.asyncio
async def test_simulation_start_heals_hallway_desk_agent_to_chair() -> None:
    spawn_x = config.get_int("default_spawn_x")
    spawn_y = config.get_int("default_spawn_y")
    drifted = db.create_agent("Boot Drift")
    db.update_agent(drifted.id, desk_x=3, desk_y=4)
    state = db.get_agent_state(drifted.id)
    assert state is not None
    assert (state.x, state.y) == (spawn_x, spawn_y)

    simulation.start()
    try:
        seated = db.get_agent_state(drifted.id)
        assert seated is not None
        assert (seated.x, seated.y) == (3, 4)
    finally:
        await simulation.stop()


def test_seat_heal_skips_in_transit_hallway_and_other_rooms() -> None:
    walker = db.create_agent("Walker", desk_x=3, desk_y=4)
    db.update_agent_state(walker.id, x=14, y=9, status="in_transit")
    activity_runtime.start_movement_activity(
        walker.id,
        destination="meetingRoom",
        metadata={"destination_x": 18, "destination_y": 4},
    )
    meeting = db.create_agent("In Meeting", desk_x=7, desk_y=4)
    db.update_agent_state(meeting.id, x=18, y=4)

    assert heal_desk_seats() == 0
    walker_state = db.get_agent_state(walker.id)
    meeting_state = db.get_agent_state(meeting.id)
    assert walker_state is not None
    assert meeting_state is not None
    assert (walker_state.x, walker_state.y) == (14, 9)
    assert (meeting_state.x, meeting_state.y) == (18, 4)
