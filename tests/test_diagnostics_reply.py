"""Diagnostic summaries carry the model ``msg`` so Log can preview it."""

from __future__ import annotations

import json
import os
from pathlib import Path

import db
from core import config


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


def test_extract_reply_prefers_wire_msg() -> None:
    raw = json.dumps({"act": "reply", "intent": "info", "msg": "Hello\nthere"})
    parsed = {"decision": "answer", "reply": "Hello\nthere"}
    assert db.extract_reply(raw, parsed) == "Hello\nthere"
    assert db.extract_reply({"data": {"msg": "peer line"}}) == "peer line"
    assert db.extract_reply({"content": "execution note"}) == "execution note"
    assert db.extract_reply("not json", None, "") == ""


def test_diagnostic_summary_exposes_reply_without_blobs() -> None:
    created = db.create_diagnostic(
        agent_id="a1",
        agent_name="Ada",
        trigger_type="human_chat",
        trigger_data=json.dumps({"content": "the trigger"}),
        status="success",
        mode="decision",
        raw_response=json.dumps({"act": "reply", "msg": "Visible transcript\nline two"}),
        action_name="answer(none)",
        parsed_action=json.dumps({"decision": "answer", "reply": "Visible transcript\nline two"}),
        result=json.dumps({"event": "decision_applied", "detail": "Ada answered the request"}),
    )
    assert created["reply"] == "Visible transcript\nline two"
    assert "parsed_action" not in created
    assert "raw_response" not in created

    listed = db.get_diagnostics(agent_id="a1")
    assert len(listed) == 1
    assert listed[0]["reply"] == "Visible transcript\nline two"
    assert "parsed_action" not in listed[0]
    assert "raw_response" not in listed[0]

    detail = db.get_diagnostic(created["id"])
    assert detail is not None
    assert detail["reply"] == "Visible transcript\nline two"
    assert json.loads(detail["raw_response"])["msg"] == "Visible transcript\nline two"
    assert json.loads(detail["parsed_action"])["reply"] == "Visible transcript\nline two"
