"""GET /api/office/chatter: one floor's agent-to-agent messages, paged."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.floor_moves import apply_move, plan_move
from core.models.message import HUMAN_SENDER_ID
from db.crud import execute
from db.floors import LOBBY_ID, create_floor


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


def _client() -> tuple[TestClient, dict[str, str]]:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app), {LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}


def _agent(name: str, x: int, *, floor_id: str | None = None):
    return db.create_agent(name, role="Eng", desk_x=x, desk_y=1, floor_id=floor_id)


def _peer(sender, recipient, content: str, *, floor_id: str, at: str | None = None):
    message = db.create_message(
        from_agent=sender.id, to_agent=recipient.id, content=content,
        message_type="social", floor_id=floor_id,
    )
    if at is not None:
        execute("UPDATE messages SET created_at = $1 WHERE id = $2", [at, message.id])
    return message


def _set_page_size(value: str) -> None:
    db.set_setting("office_chatter_page_size", value, "simulation")
    config.reload()


def _get(client: TestClient, headers: dict[str, str], **params: str):
    return client.get("/api/office/chatter", params=params, headers=headers)


def test_only_this_floors_agent_to_agent_rows_are_listed_newest_first() -> None:
    finance = create_floor("Finance")
    ada, bob = _agent("Ada", 1), _agent("Bob", 2)
    cy, di = _agent("Cy", 3, floor_id=finance.id), _agent("Di", 4, floor_id=finance.id)
    first = _peer(ada, bob, "first", floor_id=LOBBY_ID, at="2026-10-01 09:00:00")
    second = _peer(bob, ada, "second", floor_id=LOBBY_ID, at="2026-10-01 10:00:00")
    _peer(cy, di, "finance talk", floor_id=finance.id, at="2026-10-01 11:00:00")
    # Human DMs carry no floor; a stray stamped one must still be excluded.
    db.create_message(from_agent=HUMAN_SENDER_ID, to_agent=ada.id, content="boss", floor_id=LOBBY_ID)
    db.create_message(from_agent=ada.id, to_agent=HUMAN_SENDER_ID, content="to boss", floor_id=LOBBY_ID)
    db.create_message(from_agent=ada.id, to_agent=None, content="work output")

    client, headers = _client()
    response = _get(client, headers, floor_id=LOBBY_ID)
    assert response.status_code == 200
    body = response.json()
    assert [row["message_id"] for row in body["messages"]] == [second.id, first.id]
    assert body["has_more"] is False
    row = body["messages"][0]
    assert row == {
        "message_id": second.id,
        "from_agent_id": bob.id,
        "to_agent_id": ada.id,
        "content": "second",
        "message_type": "social",
        "created_at": row["created_at"],
        "floor_id": LOBBY_ID,
    }
    assert row["created_at"].startswith("2026-10-01T10:00:00")

    finance_rows = _get(client, headers, floor_id=finance.id).json()["messages"]
    assert [item["content"] for item in finance_rows] == ["finance talk"]


def test_page_size_comes_from_the_setting_and_the_cursor_survives_a_tie() -> None:
    ada, bob = _agent("Ada", 1), _agent("Bob", 2)
    # Three rows in the same second: only the id orders them.
    tied = sorted(
        (_peer(ada, bob, f"tie {index}", floor_id=LOBBY_ID, at="2026-10-02 09:00:00") for index in range(3)),
        key=lambda message: message.id,
        reverse=True,
    )
    oldest = _peer(bob, ada, "oldest", floor_id=LOBBY_ID, at="2026-10-02 08:00:00")
    _set_page_size("2")

    client, headers = _client()
    page1 = _get(client, headers, floor_id=LOBBY_ID).json()
    assert [row["message_id"] for row in page1["messages"]] == [tied[0].id, tied[1].id]
    assert page1["has_more"] is True

    page2 = _get(client, headers, floor_id=LOBBY_ID, before=tied[1].id).json()
    assert [row["message_id"] for row in page2["messages"]] == [tied[2].id, oldest.id]
    assert page2["has_more"] is False

    page3 = _get(client, headers, floor_id=LOBBY_ID, before=oldest.id).json()
    assert page3 == {"messages": [], "has_more": False}


def test_an_unknown_floor_is_404() -> None:
    client, headers = _client()
    response = _get(client, headers, floor_id="nowhere")
    assert response.status_code == 404
    assert response.json()["detail"] == "Floor not found"


def test_a_bad_foreign_or_human_cursor_is_400() -> None:
    finance = create_floor("Finance")
    ada, bob = _agent("Ada", 1), _agent("Bob", 2)
    cy, di = _agent("Cy", 3, floor_id=finance.id), _agent("Di", 4, floor_id=finance.id)
    _peer(ada, bob, "lobby", floor_id=LOBBY_ID)
    foreign = _peer(cy, di, "finance", floor_id=finance.id)
    human = db.create_message(from_agent=HUMAN_SENDER_ID, to_agent=ada.id, content="boss")
    stamped_human = db.create_message(
        from_agent=ada.id, to_agent=HUMAN_SENDER_ID, content="reply", floor_id=LOBBY_ID,
    )

    client, headers = _client()
    for cursor in ("missing-id", foreign.id, human.id, stamped_human.id):
        response = _get(client, headers, floor_id=LOBBY_ID, before=cursor)
        assert response.status_code == 400, cursor
        assert response.json()["detail"] == "Unknown cursor"


def test_an_invalid_page_size_setting_is_a_500_not_a_clamp(caplog) -> None:
    ada, bob = _agent("Ada", 1), _agent("Bob", 2)
    _peer(ada, bob, "hello", floor_id=LOBBY_ID)
    client, headers = _client()
    for bad in ("0", "-3", "many"):
        _set_page_size(bad)
        caplog.clear()
        response = _get(client, headers, floor_id=LOBBY_ID)
        assert response.status_code == 500, bad
        detail = response.json()["detail"]
        assert "Office Chatter Page Size" in detail
        assert "office_chatter_page_size" not in detail
        assert any(
            record.levelname == "ERROR" and "office_chatter_page_size" in record.getMessage()
            for record in caplog.records
        ), bad


class _Services:
    """The floor move cancels each mover's live turn; nothing is live here."""

    async def reset_agent_runtime(self, agent_id: str) -> None:
        return None

    async def enqueue_trigger(self, **_kwargs: object) -> None:
        raise AssertionError("a floor move must not wake anyone")


async def test_a_conversation_stays_on_its_floor_after_an_agent_moves() -> None:
    finance = create_floor("Finance")
    ada, bob = _agent("Ada", 1), _agent("Bob", 2)
    message = _peer(ada, bob, "said in the lobby", floor_id=LOBBY_ID)

    plan = plan_move(finance.id, agent_ids=[ada.id], channel_ids=[])
    await apply_move(
        finance.id, agent_ids=[ada.id], channel_ids=[], fingerprint=plan.fingerprint, services=_Services(),
    )
    assert db.get_agent(ada.id).floor_id == finance.id

    client, headers = _client()
    lobby = _get(client, headers, floor_id=LOBBY_ID).json()["messages"]
    assert [row["message_id"] for row in lobby] == [message.id]
    assert _get(client, headers, floor_id=finance.id).json()["messages"] == []
