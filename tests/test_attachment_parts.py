"""BossMod AI — attachments become model content parts at the completion seam."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

import db
from core import config
from core.llm.attachment_parts import (
    ATTACHMENT_IDS_KEY,
    AttachmentUnavailableError,
    expand_attachment_messages,
)
from db import attachments as db_att
from db.model_capabilities import set_supports_images

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000"
    "00907753de0000000c4944415408d7c000000003000185d29b"
    "290000000049454e44ae426082"
)


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


def _stored(tmp_path: Path, name: str, body: bytes, mime: str, tier: str) -> str:
    path = tmp_path / f"u_{name}"
    path.write_bytes(body)
    return db_att.create_attachment(
        "m1", name, len(body), mime, str(path), tier,
        context_type="direct", context_id="agent-1",
    ).id


def _trigger(*ids: str) -> list[dict]:
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "look at this", ATTACHMENT_IDS_KEY: list(ids)},
    ]


def test_vision_model_gets_an_image_url_part(tmp_path):
    aid = _stored(tmp_path, "pic.png", _PNG, "image/png", "image")
    set_supports_images("vision-model", True)

    out = expand_attachment_messages(_trigger(aid), model="vision-model")

    parts = out[1]["content"]
    assert parts[0] == {"type": "text", "text": "look at this"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(_PNG).decode()


@pytest.mark.parametrize(("name", "mime"), [
    ("logo.svg", "image/svg+xml"),
    ("scan.tiff", "image/tiff"),
    ("old.bmp", "image/bmp"),
])
def test_unsupported_image_formats_are_referenced_even_for_vision_models(tmp_path, name, mime):
    aid = _stored(tmp_path, name, b"not-a-png", mime, "image")
    set_supports_images("vision-model", True)

    part = expand_attachment_messages(_trigger(aid), model="vision-model")[1]["content"][1]

    assert part["type"] == "text"
    assert "its format is not one models accept as an image" in part["text"]
    assert f"/projects/.attachments/direct/agent-1/u_{name}" in part["text"]


def test_unflagged_model_gets_an_explicit_notice(tmp_path, caplog):
    aid = _stored(tmp_path, "pic.png", _PNG, "image/png", "image")
    set_supports_images("text-model", False)

    with caplog.at_level("WARNING"):
        out = expand_attachment_messages(_trigger(aid), model="text-model")
    # A model with no row at all is text-only too.
    unknown = expand_attachment_messages(_trigger(aid), model="never-flagged")

    for result in (out, unknown):
        notice = result[1]["content"][1]
        assert notice["type"] == "text"
        assert notice["text"] == (
            "[Image pic.png attached: your model cannot view images. "
            "It is saved at /projects/.attachments/direct/agent-1/u_pic.png.]"
        )
    assert "not marked image-capable" in caplog.text


def test_short_text_is_inlined_and_long_text_is_referenced(tmp_path):
    db.set_setting("bossmod.attach.inline_text_max_chars", "10", "advanced")
    config.reload()
    short = _stored(tmp_path, "short.txt", b"0123456789", "text/plain", "text")
    long = _stored(tmp_path, "long.txt", b"0123456789X", "text/plain", "text")

    parts = expand_attachment_messages(_trigger(short, long), model="m")[1]["content"]

    assert parts[1] == {"type": "text", "text": "Attached file short.txt:\n```\n0123456789\n```"}
    assert parts[2]["type"] == "text"
    assert "longer than the 10-character inline limit" in parts[2]["text"]
    assert "/projects/.attachments/direct/agent-1/u_long.txt" in parts[2]["text"]


def test_documents_are_referenced_not_inlined(tmp_path):
    aid = _stored(tmp_path, "spec.pdf", b"%PDF-1.4", "application/pdf", "document")
    part = expand_attachment_messages(_trigger(aid), model="m")[1]["content"][1]
    assert part["type"] == "text"
    assert part["text"].startswith("[File spec.pdf attached (application/pdf, 8 bytes): it is not inlined.")


def test_private_key_is_stripped_from_every_message_and_input_is_untouched(tmp_path):
    aid = _stored(tmp_path, "a.txt", b"hi", "text/plain", "text")
    messages = _trigger(aid) + [{"role": "user", "content": "later", ATTACHMENT_IDS_KEY: []}]

    out = expand_attachment_messages(messages, model="m")

    assert all(ATTACHMENT_IDS_KEY not in message for message in out)
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[2] == {"role": "user", "content": "later"}
    assert messages[1][ATTACHMENT_IDS_KEY] == [aid]
    assert messages[1]["content"] == "look at this"


def test_missing_file_raises(tmp_path):
    aid = _stored(tmp_path, "gone.txt", b"hi", "text/plain", "text")
    (tmp_path / "u_gone.txt").unlink()
    with pytest.raises(AttachmentUnavailableError):
        expand_attachment_messages(_trigger(aid), model="m")


def test_missing_row_raises():
    with pytest.raises(AttachmentUnavailableError):
        expand_attachment_messages(_trigger("no-such-id"), model="m")


# ─── context_builder: the trigger names its files, history shows a manifest ───


def test_build_context_marks_the_trigger_and_manifests_history(tmp_path):
    from core.llm import context_builder
    from core.models.message import HUMAN_SENDER_ID

    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    old_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "earlier", message_type="human")
    old_path = tmp_path / "u_old.png"
    old_path.write_bytes(_PNG)
    old = db_att.create_attachment(
        old_msg.id, "old.png", len(_PNG), "image/png", str(old_path), "image",
        context_type="direct", context_id=agent.id,
    )
    new_path = tmp_path / "u_new.txt"
    new_path.write_bytes(b"hello")
    new = db_att.create_attachment(
        "trigger-msg", "new.txt", 5, "text/plain", str(new_path), "text",
        context_type="direct", context_id=agent.id,
    )
    plain_msg = db.create_message(HUMAN_SENDER_ID, agent.id, "no files", message_type="human")

    turn = context_builder.TurnContext(
        agent=agent,
        state=db.get_agent_state(agent.id),
        trigger={
            "type": "human_chat", "content": "see attached", "from_name": "Human Operator",
            "source_message_id": "trigger-msg", "attachment_ids": [new.id],
        },
        conversation_history=[
            {"id": old_msg.id, "from_agent": HUMAN_SENDER_ID, "from_name": "Human Operator", "content": "earlier"},
            {"id": plain_msg.id, "from_agent": HUMAN_SENDER_ID, "from_name": "Human Operator", "content": "no files"},
        ],
        prompt_notifications=[],
        reference_materials=[],
        contract_kind="decision",
    )
    messages = context_builder.build_context(turn)

    trigger = messages[-1]
    assert trigger[ATTACHMENT_IDS_KEY] == [new.id]
    assert trigger["content"].endswith(
        f"Attachments: new.txt (text, 5 B) at /projects/.attachments/direct/{agent.id}/u_new.txt"
    )
    history = [m for m in messages if m["role"] == "user" and m is not trigger]
    earlier = next(m for m in history if "earlier" in m["content"])
    assert ATTACHMENT_IDS_KEY not in earlier
    assert earlier["content"].endswith(
        f"Attachments: old.png (image, {len(_PNG)} B) at /projects/.attachments/direct/{agent.id}/u_old.png"
    )
    plain = next(m for m in history if "no files" in m["content"])
    assert "Attachments:" not in plain["content"]
    assert all(ATTACHMENT_IDS_KEY not in m for m in messages[:-1])
    assert old.id not in str(messages)


# ─── client.completion: expansion keyed by the RAW model name ───


async def test_completion_expands_with_the_raw_model_before_the_provider_prefix(tmp_path, monkeypatch):
    from core.llm.client import completion

    aid = _stored(tmp_path, "pic.png", _PNG, "image/png", "image")
    # Flagged under the raw name; the call goes out as ``openai/llama3``.
    set_supports_images("llama3", True)
    seen: dict = {}

    async def _fake_acompletion(**kwargs):
        seen.update(kwargs)
        return {
            "choices": [{"message": {"content": "a red pixel"}}],
            "model": kwargs["model"],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("core.llm.client.litellm.acompletion", _fake_acompletion)
    messages = _trigger(aid)
    result = await completion(
        model="llama3", messages=messages,
        api_base="http://127.0.0.1:9/v1", extra_body='{"stream": false}',
    )

    assert result.content == "a red pixel"
    assert seen["model"] == "openai/llama3"
    sent = seen["messages"]
    assert all(ATTACHMENT_IDS_KEY not in m for m in sent)
    assert sent[1]["content"][1]["type"] == "image_url"
    assert messages[1][ATTACHMENT_IDS_KEY] == [aid]


# ─── dispatcher: a missing attachment is not retried ───


async def test_a_missing_attachment_fails_the_turn_without_retry(monkeypatch):
    import json as _json

    from core.agent_loop.dispatcher import TurnDispatcher

    agent = db.create_agent("Ada", role="Eng", desk_x=1, desk_y=1)
    row = db.create_agent_trigger(
        agent_id=agent.id, trigger_type="human_chat", source_channel="chat",
        payload={"content": "see file", "attachment_ids": ["gone"]},
    )
    claimed = db.claim_trigger(row.id)
    trigger = {
        **_json.loads(claimed.payload),
        "type": claimed.trigger_type,
        "trigger_id": claimed.id,
        "source_channel": claimed.source_channel,
        "claim_generation": claimed.claim_generation,
    }

    async def _boom(*_args, **_kwargs):
        raise AttachmentUnavailableError("Attachment gone no longer exists")

    recorded: list[str] = []
    supervised: list[bool] = []
    real_record = TurnDispatcher._record_dispatcher_exception

    async def _record(self, *, agent, trigger, exc):
        recorded.append(str(exc))
        await real_record(self, agent=agent, trigger=trigger, exc=exc)

    async def _supervise(self, *, agent, trigger, failure_detail, retryable):
        supervised.append(retryable)

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _boom)
    monkeypatch.setattr(TurnDispatcher, "_record_dispatcher_exception", _record)
    monkeypatch.setattr(TurnDispatcher, "_supervise_failed_turn", _supervise)

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), trigger)

    assert recorded == ["Attachment gone no longer exists"]
    assert supervised == [False]
