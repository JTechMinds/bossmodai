#!/usr/bin/env python3
"""Reproducible capability-pass item (3) scenario: host-path peer assign → deliver.

A true dual-LLM GUI loop is not started. This script hits the same HTTP
routes and the same apply_decision / execute_action / persist_result_triggers
path the live loop uses. Fixture names stay impersonal. Host-roots jail
stays fail-closed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Isolated DB must be set before importing db / app modules.
if not os.environ.get("BOSSMOD_DB_PATH"):
    root = Path(tempfile.mkdtemp(prefix="bossmod-cap-peer-"))
    os.environ["BOSSMOD_DB_PATH"] = str(root / "bossmod.sqlite3")

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.agent_loop.actions import execute_action
from core.agent_loop.activity_scheduler import persist_result_triggers
from core.agent_loop.decision_runtime import apply_decision
from core.runtime import runtime_services


def _persist_trigger(**kwargs: Any) -> None:
    db.create_agent_trigger(
        agent_id=kwargs["agent_id"],
        trigger_type=kwargs["trigger_type"],
        source_channel=kwargs["source_channel"],
        payload=kwargs["payload"],
        task_id=kwargs.get("task_id"),
    )


async def _persist_trigger_async(**kwargs: Any) -> None:
    _persist_trigger(**kwargs)


def _client() -> TestClient:
    runtime_services.enqueue_trigger = _persist_trigger_async  # type: ignore[method-assign]
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app)


def _print_step(step: str, call: str, result: str) -> None:
    print(f"| {step} | `{call}` | {result} |")


def _host_root() -> Path:
    raw = os.environ.get("BOSSMOD_CAP_HOST_ROOT")
    if raw:
        path = Path(raw)
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()
    path = Path(tempfile.mkdtemp(prefix="bossmod-cap-host-"))
    return path.resolve()


async def _run() -> int:
    db.close_connection()
    db.init_db()
    config.reload()
    client = _client()
    token = db.ensure_local_api_token()
    headers = {LOCAL_API_TOKEN_HEADER: token}

    host = _host_root()
    fixture = host / "review.py"
    fixture.write_text('print("before-review")\n', encoding="utf-8")
    roots = client.put(
        "/api/settings/workspace_host_roots",
        params={"value": str(host), "category": "cli_policy"},
        headers=headers,
    )
    if roots.status_code != 200:
        print("host roots failed", roots.status_code, roots.text, file=sys.stderr)
        return 1

    print("DB:", os.environ["BOSSMOD_DB_PATH"])
    print("HOST:", host)
    print()
    print("| Step | Call | Result |")
    print("| --- | --- | --- |")
    _print_step(
        "0 roots",
        "PUT /api/settings/workspace_host_roots",
        f"**{roots.status_code}**; allowlist `{host}` (not a full host mount)",
    )

    assigner_resp = client.post(
        "/api/agents",
        headers=headers,
        json={"name": "Cap Assigner", "role": "Lead", "desk_x": 1, "desk_y": 1},
    )
    worker_resp = client.post(
        "/api/agents",
        headers=headers,
        json={"name": "Cap Worker", "role": "Writer", "desk_x": 2, "desk_y": 1},
    )
    if assigner_resp.status_code != 201 or worker_resp.status_code != 201:
        print("agent create failed", assigner_resp.status_code, worker_resp.status_code, file=sys.stderr)
        return 1
    assigner = assigner_resp.json()
    worker = worker_resp.json()
    _print_step(
        "1 create",
        "POST /api/agents Cap Assigner + Cap Worker",
        f"**{assigner_resp.status_code}** / **{worker_resp.status_code}**; ids `{assigner['id']}` / `{worker['id']}`",
    )

    owned = client.post(
        "/api/tasks",
        headers=headers,
        json={
            "title": "Review host fixture",
            "description": "Review the allowlisted host file, then hand it off.",
            "assigned_to": assigner["id"],
            "work_contract": {
                "deliverables": [
                    {"type": "file", "path": str(fixture), "description": "Host review file"},
                ]
            },
        },
    )
    if owned.status_code != 201:
        print("owner assign failed", owned.status_code, owned.text, file=sys.stderr)
        return 1
    parent = owned.json()["task"]
    parent_path = parent["work_contract"]["deliverables"][0]["path"]
    _print_step(
        "2 own",
        "POST /api/tasks assigned_to=Cap Assigner host deliverable",
        f"**{owned.status_code}** id=`{parent['id']}` status=`{parent['status']}` path=`{parent_path}`",
    )

    assigner_model = db.get_agent(assigner["id"])
    assigner_state = db.get_agent_state(assigner["id"])
    assert assigner_model is not None and assigner_state is not None
    parent_task = db.get_task(parent["id"])
    assert parent_task is not None
    accepted_parent = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": parent_task.title,
            "reply": "I will hand this host-path review to Cap Worker.",
        },
        assigner_model,
        assigner_state,
        {
            "type": "task_assigned",
            "task_id": parent_task.id,
            "content": parent_task.description,
            "from_name": "Operator",
        },
    )
    persist_result_triggers(accepted_parent)

    assigned = await execute_action(
        {
            "action": "delegateTask",
            "agentId": worker["id"],
            "taskTitle": "Edit host review file",
            "taskDescription": "Read and edit the allowlisted host fixture.",
            "deliverables": [
                {"type": "file", "path": str(fixture), "description": "Host review file"},
            ],
        },
        assigner_model,
        assigner_state,
    )
    persist_result_triggers(assigned)
    children = db.list_tasks(parent_task_id=parent_task.id, assigned_to=worker["id"])
    if not children:
        print("delegate failed", assigned, file=sys.stderr)
        return 1
    child = children[0]
    deliverable = child.work_contract.deliverables[0].path if child.work_contract else ""
    _print_step(
        "3 assign",
        "execute_action delegateTask Cap Assigner → Cap Worker (same as live loop)",
        f"event=`{assigned['event']}` child=`{child.id}` status=`{child.status}` path=`{deliverable}`",
    )

    wakes = client.get(f"/api/agents/{worker['id']}/triggers?status=queued", headers=headers)
    wake_rows = [row for row in wakes.json() if row["task_id"] == child.id and row["trigger_type"] == "task_assigned"]
    _print_step(
        "4 wake",
        "GET /api/agents/{worker}/triggers",
        f"**{wakes.status_code}**; queued `task_assigned` count={len(wake_rows)} from=`{wake_rows[0]['payload'].get('from_name') if wake_rows else None}`",
    )

    worker_model = db.get_agent(worker["id"])
    worker_state = db.get_agent_state(worker["id"])
    assert worker_model is not None and worker_state is not None
    accepted = apply_decision(
        {
            "decision": "accept",
            "intentKind": "work_request",
            "commitmentKind": "work",
            "taskTitle": child.title,
            "reply": "I will edit the host fixture.",
        },
        worker_model,
        worker_state,
        {
            "type": "task_assigned",
            "task_id": child.id,
            "content": child.description,
            "from_name": assigner["name"],
            "from_agent": assigner["id"],
        },
    )
    persist_result_triggers(accepted)
    after_accept = client.get(f"/api/tasks/{child.id}", headers=headers)
    _print_step(
        "5 accept",
        "apply_decision accept on task_assigned (same as live loop)",
        f"event=`{accepted['event']}` GET task **{after_accept.status_code}** status=`{after_accept.json()['status']}`",
    )

    read = await execute_action(
        {"action": "bm_cli", "command": f"cat {deliverable}"},
        worker_model,
        worker_state,
    )
    written = await execute_action(
        {
            "action": "bm_cli",
            "command": f"write {deliverable}",
            "content": 'print("after-review")\n',
        },
        worker_model,
        worker_state,
    )
    denied = await execute_action(
        {"action": "bm_cli", "command": "cat /etc/passwd"},
        worker_model,
        worker_state,
    )
    http_deny = client.get("/api/company/files", params={"path": "/etc/passwd"}, headers=headers)
    completed = await execute_action(
        {
            "action": "complete",
            "summary": "Updated the host review file.",
            "followUpMessage": "Host fixture is edited under the allowlisted root.",
        },
        worker_model,
        worker_state,
    )
    persist_result_triggers(completed)
    after_done = client.get(f"/api/tasks/{child.id}", headers=headers)
    events = client.get(f"/api/tasks/{child.id}/events", headers=headers)
    artifact = client.get("/api/company/files", params={"path": str(fixture)}, headers=headers)
    notes = client.get(f"/api/agents/{assigner['id']}/notifications", headers=headers)
    observer = client.get(f"/api/agents/{assigner['id']}/triggers?status=queued", headers=headers)
    update_rows = [
        row
        for row in observer.json()
        if row["task_id"] in {child.id, parent_task.id}
        and row["trigger_type"] in {"task_update", "task_follow_up"}
    ]
    deny_text = (denied.get("detail") or "") + (denied.get("cli_prompt_content") or "")
    _print_step(
        "6 edit",
        "execute_action bm_cli cat/write host path + deny /etc/passwd",
        (
            f"cat=`{read['event']}` write=`{written['event']}` "
            f"cli deny=`{denied['event']}` company deny **{http_deny.status_code}**"
        ),
    )
    _print_step(
        "7 deliver",
        "execute_action complete + GET task/events/file/triggers",
        (
            f"complete=`{completed['event']}` GET task **{after_done.status_code}** "
            f"status=`{after_done.json()['status']}`; file **{artifact.status_code}**; "
            f"queued observer triggers={len(update_rows)}"
        ),
    )

    transcript = {
        "db_path": os.environ["BOSSMOD_DB_PATH"],
        "host_root": str(host),
        "fixture_path": str(fixture),
        "assigner_id": assigner["id"],
        "worker_id": worker["id"],
        "parent_task_id": parent["id"],
        "task_id": child.id,
        "deliverable_path": deliverable,
        "task_status": after_done.json()["status"],
        "host_file_after": fixture.read_text(encoding="utf-8"),
        "wake_triggers": wake_rows,
        "observer_triggers": update_rows,
        "events": events.json(),
        "file": artifact.json() if artifact.status_code == 200 else {"status_code": artifact.status_code, "body": artifact.text},
        "deny": {
            "cli_event": denied.get("event"),
            "cli_detail": deny_text,
            "company_status": http_deny.status_code,
            "company_detail": http_deny.json().get("detail") if http_deny.status_code != 200 else None,
        },
        "notifications": notes.json(),
    }
    out = Path(os.environ.get("BOSSMOD_CAP_PEER_TRANSCRIPT", "/tmp/bossmod-cap-peer-transcript.json"))
    out.write_text(json.dumps(transcript, default=str, indent=2), encoding="utf-8")
    print()
    print("transcript:", out)
    ok = (
        after_done.json()["status"] == "complete"
        and wake_rows
        and update_rows
        and artifact.status_code == 200
        and 'print("after-review")' in str(artifact.json().get("content", ""))
        and fixture.read_text(encoding="utf-8") == 'print("after-review")\n'
        and denied.get("event") == "bm_cli_error"
        and "outside the allowed workspace roots" in deny_text
        and http_deny.status_code == 400
    )
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
