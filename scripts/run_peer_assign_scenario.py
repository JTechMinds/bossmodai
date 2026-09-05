#!/usr/bin/env python3
"""Reproducible capability-pass item (3) scenario: peer assign → deliver.

A true dual-LLM GUI loop is not started. This script hits the same HTTP
routes and the same apply_decision / execute_action / persist_result_triggers
path the live loop uses. Fixture names stay impersonal.
"""

from __future__ import annotations

import argparse
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


async def _run() -> int:
    db.close_connection()
    db.init_db()
    config.reload()
    client = _client()
    token = db.ensure_local_api_token()
    headers = {LOCAL_API_TOKEN_HEADER: token}

    print("DB:", os.environ["BOSSMOD_DB_PATH"])
    print()
    print("| Step | Call | Result |")
    print("| --- | --- | --- |")

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

    created = client.post(
        "/api/tasks",
        headers=headers,
        json={
            "title": "Write cap status note",
            "description": "Write a short status note for the capability pass.",
            "project": "cap-peer",
            "assigned_to": worker["id"],
            "requester_id": assigner["id"],
            "source_channel": "peer",
            "work_contract": {
                "deliverables": [
                    {"type": "file", "path": "/me/status-note.md", "description": "Status note"},
                ]
            },
        },
    )
    if created.status_code != 201:
        print("assign failed", created.status_code, created.text, file=sys.stderr)
        return 1
    body = created.json()
    task = body["task"]
    task_id = task["id"]
    deliverable = task["work_contract"]["deliverables"][0]["path"]
    _print_step(
        "2 assign",
        "POST /api/tasks requester=Cap Assigner assigned_to=Cap Worker",
        f"**{created.status_code}** outcome=`{body['outcome']}` status=`{task['status']}` id=`{task_id}` path=`{deliverable}`",
    )

    wakes = client.get(f"/api/agents/{worker['id']}/triggers?status=queued", headers=headers)
    wake_rows = [row for row in wakes.json() if row["task_id"] == task_id and row["trigger_type"] == "task_assigned"]
    _print_step(
        "3 wake",
        f"GET /api/agents/{{worker}}/triggers",
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
            "taskTitle": task["title"],
            "reply": "I will write the status note.",
        },
        worker_model,
        worker_state,
        {
            "type": "task_assigned",
            "task_id": task_id,
            "content": task["description"],
            "from_name": assigner["name"],
            "from_agent": assigner["id"],
        },
    )
    persist_result_triggers(accepted)
    after_accept = client.get(f"/api/tasks/{task_id}", headers=headers)
    _print_step(
        "4 accept",
        "apply_decision accept on task_assigned (same as live loop)",
        f"event=`{accepted['event']}` GET task **{after_accept.status_code}** status=`{after_accept.json()['status']}`",
    )

    written = await execute_action(
        {
            "action": "bm_cli",
            "command": f"write {deliverable}",
            "content": "# Cap status note\nPeer assign/deliver loop completed.\n",
        },
        worker_model,
        worker_state,
    )
    completed = await execute_action(
        {
            "action": "complete",
            "summary": "Wrote the status note.",
            "followUpMessage": "Status note is in the shared project folder.",
        },
        worker_model,
        worker_state,
    )
    persist_result_triggers(completed)
    after_done = client.get(f"/api/tasks/{task_id}", headers=headers)
    events = client.get(f"/api/tasks/{task_id}/events", headers=headers)
    artifact = client.get("/api/company/files", params={"path": deliverable}, headers=headers)
    notes = client.get(f"/api/agents/{assigner['id']}/notifications", headers=headers)
    observer = client.get(f"/api/agents/{assigner['id']}/triggers?status=queued", headers=headers)
    update_rows = [
        row
        for row in observer.json()
        if row["task_id"] == task_id and row["trigger_type"] in {"task_update", "task_follow_up"}
    ]
    _print_step(
        "5 deliver",
        "execute_action bm_cli write + complete (same as live loop)",
        (
            f"cli=`{written['event']}` complete=`{completed['event']}` "
            f"GET task **{after_done.status_code}** status=`{after_done.json()['status']}`"
        ),
    )
    _print_step(
        "6 observe",
        "GET task events + company file + assigner triggers/notifications",
        (
            f"events **{events.status_code}** ({len(events.json())} rows); "
            f"file **{artifact.status_code}**; "
            f"queued observer triggers={len(update_rows)}; "
            f"notifications={len(notes.json())}"
        ),
    )

    transcript = {
        "db_path": os.environ["BOSSMOD_DB_PATH"],
        "assigner_id": assigner["id"],
        "worker_id": worker["id"],
        "task_id": task_id,
        "deliverable_path": deliverable,
        "task_status": after_done.json()["status"],
        "wake_triggers": wake_rows,
        "observer_triggers": update_rows,
        "events": events.json(),
        "file": artifact.json() if artifact.status_code == 200 else {"status_code": artifact.status_code, "body": artifact.text},
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
        and "Peer assign/deliver loop completed." in str(artifact.json().get("content", ""))
    )
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    import asyncio

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
