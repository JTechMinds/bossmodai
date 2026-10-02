"""BossMod AI — attachments become model content parts at the completion seam."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

import db
from tests._connections import model_connection
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
            "[Image pic.png attached, but your model cannot view images. "
            "Don't try to open it with the CLI; tell the operator you can't see it. "
            "It is saved at /projects/.attachments/direct/agent-1/u_pic.png "
            "if you need to move or reference the file.]"
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

    from core.agent_loop.dispatcher import NOT_RETRIED_ATTACHMENT_REASON, TurnDispatcher

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
    supervised: list[str | None] = []
    real_record = TurnDispatcher._record_dispatcher_exception

    async def _record(self, *, agent, trigger, exc):
        recorded.append(str(exc))
        await real_record(self, agent=agent, trigger=trigger, exc=exc)

    async def _supervise(self, *, agent, trigger, failure_detail, not_retried_reason):
        supervised.append(not_retried_reason)

    monkeypatch.setattr("core.agent_loop.dispatcher.run_turn", _boom)
    monkeypatch.setattr(TurnDispatcher, "_record_dispatcher_exception", _record)
    monkeypatch.setattr(TurnDispatcher, "_supervise_failed_turn", _supervise)

    await TurnDispatcher()._run_trigger(agent, db.get_agent_state(agent.id), trigger)

    assert recorded == ["Attachment gone no longer exists"]
    assert supervised == [NOT_RETRIED_ATTACHMENT_REASON]


# ─── attachment_route_line: what the thread router is told ───


def test_attachment_route_line_names_files_and_tiers_without_paths(tmp_path):
    from core.llm.attachment_parts import attachment_route_line

    shot = _stored(tmp_path, "shot.png", _PNG, "image/png", "image")
    spec = _stored(tmp_path, "spec.pdf", b"%PDF", "application/pdf", "document")

    line = attachment_route_line([shot, spec])

    assert line == "Attachments: shot.png (image), spec.pdf (document)"
    assert "/projects" not in line


def test_attachment_route_line_unknown_id_raises():
    from core.llm.attachment_parts import attachment_route_line

    with pytest.raises(AttachmentUnavailableError):
        attachment_route_line(["no-such-id"])


# ─── CLI screenshots (Browser Vision): only the newest is sent as an image ───


def _shot(tmp_path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(_PNG)
    return str(path)


def _cli_result(text: str, *paths: str) -> dict:
    from core.bm_cli.results import wrap_cli_tool_message

    return wrap_cli_tool_message(text, image_paths=tuple(paths))


def test_screenshot_png_is_a_model_image_type():
    from core.attachments import MODEL_IMAGE_MIME_TYPES
    from core.llm.attachment_parts import SCREENSHOT_MIME_TYPE

    assert SCREENSHOT_MIME_TYPE in MODEL_IMAGE_MIME_TYPES


def test_only_the_last_screenshot_carrier_is_expanded(tmp_path):
    from core.llm.attachment_parts import SCREENSHOT_PATHS_KEY, SCREENSHOT_SUPERSEDED_TEXT

    set_supports_images("vision-model", True)
    old, new = _shot(tmp_path, "old.png"), _shot(tmp_path, "new.png")
    messages = [
        {"role": "system", "content": "sys"},
        _cli_result("first view", old),
        {"role": "assistant", "content": "click"},
        _cli_result("second view", new),
        {"role": "user", "content": "continue"},
    ]
    assert messages[1][SCREENSHOT_PATHS_KEY] == [old]

    out = expand_attachment_messages(messages, model="vision-model")

    earlier, newest = out[1]["content"], out[3]["content"]
    assert earlier[0]["type"] == "text" and "first view" in earlier[0]["text"]
    assert earlier[1:] == [{"type": "text", "text": SCREENSHOT_SUPERSEDED_TEXT}]
    assert "second view" in newest[0]["text"]
    assert newest[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + base64.b64encode(_PNG).decode()},
    }
    assert sum(part["type"] == "image_url" for m in out if isinstance(m["content"], list) for part in m["content"]) == 1
    assert out[4] == {"role": "user", "content": "continue"}


def test_screenshot_key_is_never_sent_and_input_is_untouched(tmp_path):
    from core.llm.attachment_parts import SCREENSHOT_PATHS_KEY

    set_supports_images("vision-model", True)
    path = _shot(tmp_path, "s.png")
    messages = [_cli_result("view", path), _cli_result("again", path)]

    out = expand_attachment_messages(messages, model="vision-model")

    assert all(SCREENSHOT_PATHS_KEY not in message for message in out)
    assert messages[0][SCREENSHOT_PATHS_KEY] == [path]
    assert isinstance(messages[0]["content"], str)


def test_non_vision_model_gets_the_cannot_view_notice_for_a_screenshot(tmp_path, caplog):
    set_supports_images("text-model", False)
    path = _shot(tmp_path, "s.png")

    with caplog.at_level("WARNING"):
        out = expand_attachment_messages([_cli_result("view", path)], model="text-model")

    notice = out[0]["content"][1]
    assert notice["type"] == "text"
    assert "your model cannot view images" in notice["text"]
    assert "not marked image-capable" in caplog.text


def test_a_missing_newest_screenshot_gets_the_unavailable_notice_and_a_warning(tmp_path, caplog):
    set_supports_images("vision-model", True)
    path = _shot(tmp_path, "gone.png")
    Path(path).unlink()

    with caplog.at_level("WARNING"):
        out = expand_attachment_messages([_cli_result("view", path)], model="vision-model")

    assert out[0]["content"][1] == {
        "type": "text",
        "text": (
            "[screenshot unavailable: the browser session that took it has ended (the app restarted "
            'or the browser was closed); run "bv status" to see whether a page is open, '
            'and "bv open <url>" to start again]'
        ),
    }
    assert "is missing" in caplog.text


def test_a_frozen_transcript_keeps_the_screenshot_paths(tmp_path):
    from core.agent_loop import activity_runtime
    from core.agent_loop.work_snapshot import freeze_work_turn, restore_work_turn
    from core.llm.attachment_parts import SCREENSHOT_PATHS_KEY
    from core.models.message import HUMAN_SENDER_ID
    from core.tasking import create_or_bind_task

    agent = db.create_agent("Iris", role="Researcher", connection_id=model_connection("vision-model"))
    task = create_or_bind_task(
        title="Browse", description="Look at a page.", project=None, assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID, owner_id=None, created_by=HUMAN_SENDER_ID, parent_task_id=None,
        work_contract=None, source_channel=None, notification_policy=None, notification_channel_id=None,
        audit_author_name="Human Operator", audit_author_type="human",
    ).task
    activity = activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    path = _shot(tmp_path, "s.png")
    steps = [{"role": "assistant", "content": '{"act":"cli","data":{"cmd":"bv view"}}'}, _cli_result("view", path)]

    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0,
    )
    assert snapshot.transcript[1][SCREENSHOT_PATHS_KEY] == [path]
    stored = db.get_work_snapshot(activity.id)
    assert stored.transcript[1][SCREENSHOT_PATHS_KEY] == [path]
    restored = restore_work_turn(activity=activity, context=[{"role": "system", "content": "sys"}, {"role": "user", "content": "go on"}])
    assert restored[2][SCREENSHOT_PATHS_KEY] == [path]


# ─── superseded screenshot results collapse to their one-line summary (R22) ───


def _bv_result(text: str, path: str, summary: str) -> dict:
    from core.bm_cli.results import wrap_cli_tool_message

    return wrap_cli_tool_message(text, image_paths=(path,), summary=summary)


def test_only_the_newest_browser_result_keeps_its_full_text(tmp_path):
    from core.bm_cli.results import CLI_TOOL_RESULT_BEGIN, CLI_TOOL_RESULT_END, wrap_cli_text
    from core.llm.attachment_parts import SCREENSHOT_PATHS_KEY, SCREENSHOT_SUPERSEDED_TEXT, SUMMARY_KEY

    set_supports_images("vision-model", True)
    legend = '[@1] button "Sign in"\n[@2] textbox "Email" (empty)'
    summaries = [
        "bv open example.com → example.com",
        'bv click @1 → example.com/login; clicked button "Sign in"',
        "bv type @2 → example.com/login",
    ]
    messages = [{"role": "system", "content": "sys"}]
    for step, summary in enumerate(summaries):
        messages.append({"role": "assistant", "content": f"step {step}"})
        messages.append(_bv_result(f"url: page {step}\n{legend}", _shot(tmp_path, f"{step}.png"), summary))
    assert messages[2][SUMMARY_KEY] == summaries[0]

    out = expand_attachment_messages(messages, model="vision-model")

    first, second, newest = out[2]["content"], out[4]["content"], out[6]["content"]
    for parts, summary in ((first, summaries[0]), (second, summaries[1])):
        assert parts[0] == {"type": "text", "text": wrap_cli_text(summary)}
        assert parts[0]["text"].startswith(CLI_TOOL_RESULT_BEGIN)
        assert parts[0]["text"].endswith(CLI_TOOL_RESULT_END)
        assert "[@1]" not in parts[0]["text"] and "url: page" not in parts[0]["text"]
        assert parts[1:] == [{"type": "text", "text": SCREENSHOT_SUPERSEDED_TEXT}]
    assert "url: page 2" in newest[0]["text"] and legend in newest[0]["text"]
    assert newest[1]["type"] == "image_url"
    assert all(SUMMARY_KEY not in m and SCREENSHOT_PATHS_KEY not in m for m in out)
    # The input is untouched.
    assert messages[2][SUMMARY_KEY] == summaries[0] and isinstance(messages[2]["content"], str)


def test_a_summary_is_kept_only_beside_screenshots():
    from core.bm_cli.results import cli_continuation_messages, wrap_cli_tool_message
    from core.llm.attachment_parts import SUMMARY_KEY

    assert SUMMARY_KEY not in wrap_cli_tool_message("plain output", summary="one line")
    carried = cli_continuation_messages(
        assistant_content="a", cli_prompt_content="full", followup_content="go on",
        image_paths=("/x.png",), summary="bv view → example.com",
    )[1]
    assert carried[SUMMARY_KEY] == "bv view → example.com"


def test_a_frozen_transcript_keeps_the_summary(tmp_path):
    from core.agent_loop import activity_runtime
    from core.agent_loop.work_snapshot import freeze_work_turn, restore_work_turn
    from core.llm.attachment_parts import SCREENSHOT_SUPERSEDED_TEXT, SUMMARY_KEY
    from core.models.message import HUMAN_SENDER_ID
    from core.tasking import create_or_bind_task

    set_supports_images("vision-model", True)
    agent = db.create_agent("Iris", role="Researcher", connection_id=model_connection("vision-model"))
    task = create_or_bind_task(
        title="Browse", description="Look at a page.", project=None, assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID, owner_id=None, created_by=HUMAN_SENDER_ID, parent_task_id=None,
        work_contract=None, source_channel=None, notification_policy=None, notification_channel_id=None,
        audit_author_name="Human Operator", audit_author_type="human",
    ).task
    activity = activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    steps = [
        {"role": "assistant", "content": '{"act":"cli","data":{"cmd":"bv open example.com"}}'},
        _bv_result("full first result", _shot(tmp_path, "a.png"), "bv open example.com → example.com"),
        {"role": "assistant", "content": '{"act":"cli","data":{"cmd":"bv view"}}'},
        _bv_result("full second result", _shot(tmp_path, "b.png"), "bv view → example.com"),
    ]

    snapshot = freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0,
    )
    assert snapshot.transcript[1][SUMMARY_KEY] == "bv open example.com → example.com"
    assert db.get_work_snapshot(activity.id).transcript[3][SUMMARY_KEY] == "bv view → example.com"
    restored = restore_work_turn(activity=activity, context=[{"role": "system", "content": "sys"}, {"role": "user", "content": "go on"}])
    out = expand_attachment_messages(restored, model="vision-model")
    assert "bv open example.com → example.com" in out[2]["content"][0]["text"]
    assert "full first result" not in out[2]["content"][0]["text"]
    assert out[2]["content"][1]["text"] == SCREENSHOT_SUPERSEDED_TEXT
    assert "full second result" in out[4]["content"][0]["text"]


def test_a_resumed_transcript_whose_session_ended_gets_the_session_ended_notice(tmp_path):
    """R28: the restart deleted the session's screenshots, so the resumed turn is told, not shown the old page."""
    from core.agent_loop import activity_runtime
    from core.agent_loop.work_snapshot import freeze_work_turn, restore_work_turn
    from core.llm.attachment_parts import SCREENSHOT_SESSION_ENDED_TEXT, SCREENSHOT_SUPERSEDED_TEXT
    from core.models.message import HUMAN_SENDER_ID
    from core.tasking import create_or_bind_task

    set_supports_images("vision-model", True)
    agent = db.create_agent("Iris", role="Researcher", connection_id=model_connection("vision-model"))
    task = create_or_bind_task(
        title="Browse", description="Look at a page.", project=None, assigned_to=agent.id,
        requester_id=HUMAN_SENDER_ID, owner_id=None, created_by=HUMAN_SENDER_ID, parent_task_id=None,
        work_contract=None, source_channel=None, notification_policy=None, notification_channel_id=None,
        audit_author_name="Human Operator", audit_author_type="human",
    ).task
    activity = activity_runtime.activate_work_activity(agent.id, task, task_status="active")
    older, newest = _shot(tmp_path, "a.png"), _shot(tmp_path, "b.png")
    steps = [
        {"role": "assistant", "content": '{"act":"cli","data":{"cmd":"bv open example.com"}}'},
        _bv_result("full first result", older, "bv open example.com → example.com"),
        {"role": "assistant", "content": '{"act":"cli","data":{"cmd":"bv view"}}'},
        _bv_result("full second result", newest, "bv view → example.com"),
    ]
    freeze_work_turn(
        agent=agent, activity=activity, initial_len=0, context=steps, fingerprints=[], no_progress_checkpoints=0,
    )
    # The session ended (app restart): its screenshot folder is gone.
    Path(older).unlink()
    Path(newest).unlink()

    restored = restore_work_turn(activity=activity, context=[{"role": "system", "content": "sys"}, {"role": "user", "content": "go on"}])
    out = expand_attachment_messages(restored, model="vision-model")

    assert out[4]["content"][1] == {"type": "text", "text": SCREENSHOT_SESSION_ENDED_TEXT}
    assert not any(part.get("type") == "image_url" for m in out if isinstance(m["content"], list) for part in m["content"])
    assert out[2]["content"][1]["text"] == SCREENSHOT_SUPERSEDED_TEXT
