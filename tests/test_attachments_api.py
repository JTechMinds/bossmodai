"""BossMod AI — API integration tests for attachment endpoints.

Uploads are scoped to a real conversation (an agent DM or a thread), stored
under that conversation's floor, and linked only on send.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.floor_roots import floor_root
from core.floors import send_home
from db import attachments as db_att


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()


def teardown_function() -> None:
    db.close_connection()


class _Services:
    """Records wakes instead of starting the runtime."""

    def __init__(self) -> None:
        self.triggers: list[dict[str, Any]] = []

    async def enqueue_trigger(self, **kwargs: Any) -> None:
        self.triggers.append(kwargs)


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr("api.routes.agents.runtime_services", _Services())
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def _png_bytes() -> bytes:
    # 1x1 red PNG
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000"
        "00907753de0000000c4944415408d7c000000003000185d29b"
        "290000000049454e44ae426082"
    )


def _agent(name: str = "Ada", x: int = 1):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1)


def _upload(client: TestClient, ctx: dict[str, str], name: str, body: bytes, mime: str = "text/plain"):
    return client.post(
        "/api/attachments/upload",
        files={"file": (name, body, mime)},
        data={"message_context": json.dumps(ctx), "original_name": name},
    )


def test_upload_png_stores_under_the_agents_floor(client):
    ada = _agent()
    r = _upload(client, {"type": "direct", "id": ada.id}, "pic.png", _png_bytes(), "image/png")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["preview_tier"] == "image"
    assert body["file_name"] == "pic.png"
    assert "storage_path" not in body
    row = db_att.get_attachment_by_id(body["id"])
    expected_dir = floor_root(ada.floor_id) / ".attachments" / "direct" / ada.id
    assert Path(row.storage_path).parent == expected_dir
    assert Path(row.storage_path).read_bytes() == _png_bytes()
    assert (row.context_type, row.context_id, row.message_id) == ("direct", ada.id, "pending")


def test_upload_to_a_thread_stores_under_the_threads_floor(client):
    ada = _agent()
    channel = db.create_channel(name="Room", member_agent_ids=[ada.id])
    r = _upload(client, {"type": "thread", "id": channel.id}, "notes.txt", b"hi")
    assert r.status_code == 201, r.text
    row = db_att.get_attachment_by_id(r.json()["id"])
    assert Path(row.storage_path).parent == floor_root(channel.floor_id) / ".attachments" / "thread" / channel.id


def test_upload_exe_rejected(client):
    ada = _agent()
    r = _upload(client, {"type": "direct", "id": ada.id}, "bad.exe", b"MZ", "application/octet-stream")
    assert r.status_code == 415
    assert r.json()["detail"]["code"] == "BLOCKLISTED"


def test_upload_over_the_cap_is_413(client):
    ada = _agent()
    db.set_setting("bossmod.attach.max_size_mb", "1", "advanced")
    config.reload()
    big = b"\x00" * (1024 * 1024 + 1)
    r = _upload(client, {"type": "direct", "id": ada.id}, "big.bin", big, "application/octet-stream")
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "SIZE_EXCEEDED"
    assert not (floor_root(ada.floor_id) / ".attachments" / "direct" / ada.id).exists()


@pytest.mark.parametrize("ctx", [
    {"type": "bogus", "id": "x"},
    {"type": "unscoped", "id": ""},
    {"type": "channel", "id": "p/c"},
    {"type": "direct", "id": ""},
])
def test_upload_bad_context_is_400(client, ctx):
    r = _upload(client, ctx, "x.txt", b"hi")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_CONTEXT"


@pytest.mark.parametrize("kind", ["direct", "thread"])
def test_upload_with_a_traversal_id_is_404_and_writes_nothing(client, kind):
    ada = _agent()
    r = _upload(client, {"type": kind, "id": "../../escape"}, "x.txt", b"hi")
    assert r.status_code == 404
    assert not (floor_root(ada.floor_id).parent / "escape").exists()
    assert db.query("SELECT id FROM attachments") == []


def test_upload_for_a_vacationing_agent_is_409(client):
    ada = _agent()
    send_home(ada.id)
    r = _upload(client, {"type": "direct", "id": ada.id}, "x.txt", b"hi")
    assert r.status_code == 409


def test_download_returns_file_with_nosniff(client):
    ada = _agent()
    aid = _upload(client, {"type": "direct", "id": ada.id}, "d.txt", b"hello world").json()["id"]
    dl = client.get(f"/api/attachments/{aid}")
    assert dl.status_code == 200
    assert dl.content == b"hello world"
    assert dl.headers["x-content-type-options"] == "nosniff"


def test_download_missing_404(client):
    r = client.get(f"/api/attachments/{uuid.uuid4()}")
    assert r.status_code == 404


def test_preview_non_image_204(client):
    ada = _agent()
    aid = _upload(client, {"type": "direct", "id": ada.id}, "n.txt", b"x").json()["id"]
    assert client.get(f"/api/attachments/{aid}/preview").status_code == 204


def test_preview_image_200_with_nosniff(client):
    ada = _agent()
    aid = _upload(client, {"type": "direct", "id": ada.id}, "p.png", _png_bytes(), "image/png").json()["id"]
    r = client.get(f"/api/attachments/{aid}/preview")
    assert r.status_code == 200
    assert r.content == _png_bytes()
    assert r.headers["x-content-type-options"] == "nosniff"


def test_limits_returns_the_settings(client):
    db.set_setting("bossmod.attach.max_per_message", "3", "advanced")
    config.reload()
    r = client.get("/api/attachments/limits")
    assert r.status_code == 200
    assert r.json() == {"max_size_mb": 10, "max_per_message": 3}


def test_delete_pending_removes_row_and_file(client):
    ada = _agent()
    aid = _upload(client, {"type": "direct", "id": ada.id}, "d.txt", b"bye").json()["id"]
    path = Path(db_att.get_attachment_by_id(aid).storage_path)
    r = client.delete(f"/api/attachments/{aid}")
    assert r.status_code == 204
    assert db_att.get_attachment_by_id(aid) is None
    assert not path.exists()


def test_delete_linked_is_409_and_unknown_is_404(client):
    ada = _agent()
    aid = _upload(client, {"type": "direct", "id": ada.id}, "d.txt", b"keep").json()["id"]
    assert client.post(
        f"/api/agents/{ada.id}/activate", json={"content": "see file", "attachment_ids": [aid]},
    ).status_code == 200
    r = client.delete(f"/api/attachments/{aid}")
    assert r.status_code == 409
    assert Path(db_att.get_attachment_by_id(aid).storage_path).exists()
    assert client.delete(f"/api/attachments/{uuid.uuid4()}").status_code == 404


def test_activate_with_an_unknown_attachment_is_422(client):
    ada = _agent()
    r = client.post(
        f"/api/agents/{ada.id}/activate", json={"content": "hi", "attachment_ids": ["nope"]},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["missing_ids"] == ["nope"]
    assert db.get_human_chat_thread(ada.id) == []


def test_thread_post_with_only_attachments_links_them(client):
    ada = _agent()
    channel = db.create_channel(name="Room", member_agent_ids=[ada.id])
    aid = _upload(client, {"type": "thread", "id": channel.id}, "a.txt", b"x").json()["id"]
    r = client.post(f"/api/channels/{channel.id}/messages", json={"content": "", "attachment_ids": [aid]})
    assert r.status_code == 200, r.text
    message = r.json()["message"]
    assert [a["id"] for a in message["attachments"]] == [aid]
    assert db_att.get_attachment_by_id(aid).message_id == message["id"]


def test_thread_post_with_neither_text_nor_attachments_is_400(client):
    ada = _agent()
    channel = db.create_channel(name="Room", member_agent_ids=[ada.id])
    r = client.post(f"/api/channels/{channel.id}/messages", json={"content": "  "})
    assert r.status_code == 400


def test_thread_post_with_another_threads_upload_is_422(client):
    ada = _agent()
    room = db.create_channel(name="Room", member_agent_ids=[ada.id])
    other = db.create_channel(name="Other", member_agent_ids=[ada.id])
    aid = _upload(client, {"type": "thread", "id": other.id}, "a.txt", b"x").json()["id"]
    r = client.post(f"/api/channels/{room.id}/messages", json={"content": "hi", "attachment_ids": [aid]})
    assert r.status_code == 422
    assert r.json()["detail"]["missing_ids"] == [aid]


def test_history_returns_attachments_for_dm_and_thread(client):
    ada = _agent()
    dm_aid = _upload(client, {"type": "direct", "id": ada.id}, "dm.txt", b"x").json()["id"]
    client.post(f"/api/agents/{ada.id}/activate", json={"content": "dm", "attachment_ids": [dm_aid]})
    client.post(f"/api/agents/{ada.id}/activate", json={"content": "plain"})

    rows = client.get(f"/api/agents/{ada.id}/messages").json()
    by_content = {row["content"]: row for row in rows}
    assert [a["id"] for a in by_content["dm"]["attachments"]] == [dm_aid]
    assert by_content["dm"]["attachments"][0]["file_name"] == "dm.txt"
    assert "storage_path" not in by_content["dm"]["attachments"][0]
    assert by_content["plain"]["attachments"] == []

    channel = db.create_channel(name="Room", member_agent_ids=[ada.id])
    th_aid = _upload(client, {"type": "thread", "id": channel.id}, "th.txt", b"y").json()["id"]
    client.post(f"/api/channels/{channel.id}/messages", json={"content": "thread", "attachment_ids": [th_aid]})
    messages = client.get(f"/api/channels/{channel.id}").json()["messages"]
    posted = next(m for m in messages if m["content"] == "thread")
    assert [a["id"] for a in posted["attachments"]] == [th_aid]
