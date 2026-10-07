"""BossMod AI — the ``view`` virtual command shows an image file to the agent."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

import db
from core import config
from core.attachments import storage_dir
from core.bm_cli.floor_roots import floor_root
from core.bm_cli.policy_engine import policy_engine
from core.bm_cli.results import wrap_cli_tool_message
from core.bm_cli.runtime import execute_bm_cli
from core.llm.attachment_parts import expand_attachment_messages
from db.floors import LOBBY_ID
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
    policy_engine.reload()


def teardown_function() -> None:
    db.close_connection()


def _agent():
    agent = db.create_agent("Ada", role="Eng")
    state = db.get_agent_state(agent.id)
    assert state is not None
    return agent, state


def _attachment(agent_id: str, name: str, body: bytes) -> tuple[Path, str]:
    """Write a file where an operator upload to the agent's DM lands; return (real, virtual)."""
    # The agent lives in Lobby, so its /projects is Lobby's folder.
    folder = storage_dir(floor_root(LOBBY_ID), "direct", agent_id)
    folder.mkdir(parents=True, exist_ok=True)
    real = folder / name
    real.write_bytes(body)
    return real, f"/projects/.attachments/direct/{agent_id}/{name}"


def test_view_names_the_image_for_the_next_model_call() -> None:
    agent, state = _agent()
    real, virtual = _attachment(agent.id, "u_pic.png", _PNG)

    result = execute_bm_cli(agent, state, f"view {virtual}")

    assert result.ok and result.kind == "image"
    assert result.image_paths == (str(real),)
    assert result.summary == f"view {virtual}"
    assert result.detail == f"Ada viewed {virtual}"
    assert f"path: {virtual}" in result.prompt_content
    assert "type: image/png" in result.prompt_content
    assert f"size: {len(_PNG)} B" in result.prompt_content


def test_view_outside_the_roots_is_denied() -> None:
    agent, state = _agent()

    result = execute_bm_cli(agent, state, "view /etc/hostname")

    assert not result.ok
    assert result.image_paths == ()
    assert "'/etc/hostname' is outside /me and /projects" in result.detail


@pytest.mark.parametrize(("name", "body", "mime"), [
    ("notes.txt", b"hello", "text/plain"),
    ("logo.svg", b"<svg/>", "image/svg+xml"),
    ("scan.tiff", b"II*\x00", "image/tiff"),
])
def test_view_refuses_files_models_cannot_see(name: str, body: bytes, mime: str) -> None:
    agent, state = _agent()
    _real, virtual = _attachment(agent.id, name, body)

    result = execute_bm_cli(agent, state, f"view {virtual}")

    assert not result.ok and result.image_paths == ()
    assert result.data == {"error": f"Cannot view {virtual}: its type is {mime}; view accepts PNG, JPEG, GIF or WebP images."}


def test_a_refused_format_is_not_handed_to_the_shell() -> None:
    # A virtual error stays a virtual error with the shell on: the format
    # refusal is reported by view, never re-run on the host shell.
    db.set_setting("cli_shell_enabled", "true", "cli_policy")
    config.reload()
    policy_engine.reload()
    agent, state = _agent()
    _real, virtual = _attachment(agent.id, "logo.svg", b"<svg/>")

    result = execute_bm_cli(agent, state, f"view {virtual}")

    assert result.executor == "virtual"
    assert result.data["error"].startswith(f"Cannot view {virtual}")


def test_view_refuses_an_image_over_the_upload_limit() -> None:
    db.set_setting("bossmod.attach.max_size_mb", "1", "advanced")
    config.reload()
    agent, state = _agent()
    _real, virtual = _attachment(agent.id, "big.png", b"\0" * (1024 * 1024 + 1))
    _ok_real, ok_virtual = _attachment(agent.id, "edge.png", b"\0" * (1024 * 1024))

    result = execute_bm_cli(agent, state, f"view {virtual}")

    assert not result.ok and result.image_paths == ()
    assert result.data == {"error": f"Cannot view {virtual}: it is 1.0 MB, over the 1 MB image limit."}
    assert execute_bm_cli(agent, state, f"view {ok_virtual}").ok


def test_view_refuses_a_missing_file_a_directory_and_a_wrong_arg_count() -> None:
    agent, state = _agent()
    _real, virtual = _attachment(agent.id, "pic.png", _PNG)
    folder = virtual.rsplit("/", 1)[0]

    missing = execute_bm_cli(agent, state, "view /me/nope.png")
    directory = execute_bm_cli(agent, state, f"view {folder}")
    bare = execute_bm_cli(agent, state, "view")

    assert missing.data == {"error": "File not found: /me/nope.png"}
    assert directory.data == {"error": f"Cannot view a directory: {folder}"}
    assert bare.data == {"error": '"view" requires exactly one path argument.'}
    assert all(not r.ok and r.image_paths == () for r in (missing, directory, bare))


def test_a_view_result_reaches_the_model_as_an_image_or_the_cannot_view_notice() -> None:
    agent, state = _agent()
    _real, virtual = _attachment(agent.id, "pic.png", _PNG)
    result = execute_bm_cli(agent, state, f"view {virtual}")
    message = wrap_cli_tool_message(result.prompt_content, image_paths=result.image_paths, summary=result.summary)
    set_supports_images("vision-model", True)
    set_supports_images("text-model", False)

    seen = expand_attachment_messages([message], model="vision-model")[0]["content"]
    blind = expand_attachment_messages([message], model="text-model")[0]["content"]

    assert seen[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + base64.b64encode(_PNG).decode()},
    }
    assert blind[1] == {
        "type": "text",
        "text": "[An image was loaded, but your model cannot view images. Tell the boss you can't see it.]",
    }
