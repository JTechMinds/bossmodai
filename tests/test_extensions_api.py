"""Extensions API: list, enable/disable, setup, and the settings guard."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.command_registry import CORE_COMMAND_NAMES
from core.extensions.paths import extension_data_dir
from core.extensions.registry import discover, get_discovery

_BV = "browser-vision"


def setup_function() -> None:
    db.close_connection()
    db_path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    db.init_db()
    config.reload()
    for name in ("ready.json", "setup.lock", "setup.error", "setup.log"):
        (extension_data_dir(_BV) / name).unlink(missing_ok=True)


def teardown_function() -> None:
    db.close_connection()


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


def _mark_ready(ext_id: str = _BV) -> None:
    data_dir = extension_data_dir(ext_id)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "ready.json").write_text(json.dumps({"browser": "test"}), encoding="utf-8")


def _item(client: TestClient, ext_id: str = _BV) -> dict:
    response = client.get("/api/extensions")
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["id"] == ext_id)


def test_list_shows_browser_vision_with_setup_state_and_excluded_agents(client) -> None:
    db.set_supports_images("vision-model", True)
    db.create_agent("Seer", role="Researcher", model_work="vision-model")
    blind = db.create_agent("Scribe", role="Writer", model_work="text-model")

    item = _item(client)

    assert item["name"] == "Browser Vision"
    assert item["valid"] is True and item["invalid_reason"] is None
    assert item["enabled"] is False
    assert item["command"] == {"name": "bv", "summary": "Browse websites by screenshot + grid."}
    assert item["requires_image_model"] is True
    assert item["setup"] == {"state": "missing", "detail": None}
    assert item["setup_label"] == "Download browser (~120 MB)"
    assert item["excluded_agents"] == [{"id": blind.id, "name": "Scribe", "model": "text-model"}]


def test_enabling_before_setup_is_refused(client) -> None:
    response = client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "SETUP_REQUIRED"
    assert _item(client)["enabled"] is False


def test_enable_disable_round_trip_once_set_up(client) -> None:
    _mark_ready()
    on = client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True and on.json()["setup"]["state"] == "ready"
    assert json.loads(config.get_live("extensions_enabled")) == [_BV]
    off = client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert json.loads(config.get_live("extensions_enabled")) == []


def test_unknown_extension_is_404(client) -> None:
    assert client.put("/api/extensions/nope/enabled", json={"enabled": True}).status_code == 404
    assert client.post("/api/extensions/nope/setup", json={"enable_on_success": True}).status_code == 404


def test_setup_while_one_is_running_is_a_conflict(client) -> None:
    data_dir = extension_data_dir(_BV)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "setup.lock").write_text(str(os.getpid()), encoding="utf-8")

    assert _item(client)["setup"]["state"] == "installing"
    response = client.post(f"/api/extensions/{_BV}/setup", json={"enable_on_success": True})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "SETUP_RUNNING"


def test_a_lock_left_by_a_dead_process_reads_as_interrupted(client) -> None:
    data_dir = extension_data_dir(_BV)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "setup.lock").write_text("999999999", encoding="utf-8")
    setup = _item(client)["setup"]
    assert setup["state"] == "failed"
    assert "interrupted" in setup["detail"]


def test_the_generic_settings_put_refuses_the_enabled_set(client) -> None:
    response = client.put("/api/settings/extensions_enabled", params={"value": json.dumps([_BV]), "category": "extensions"})
    assert response.status_code == 400
    assert "Add → Extensions" in response.json()["detail"]
    assert json.loads(config.get_live("extensions_enabled")) == []


def _fake_setup_extension(root: Path, ext_id: str, *, fail: bool) -> None:
    folder = root / ext_id
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({
        "id": ext_id,
        "name": "Setup Demo",
        "version": "1.0.0",
        "description": "Needs a download.",
        "command": {"name": ext_id.replace("-", "")[:15], "summary": "s", "usage": "u", "help": "h"},
        "setup": {"required": True, "label": "Download thing"},
    }), encoding="utf-8")
    body = "raise SetupError('network unreachable')" if fail else (
        "(self.data_dir / 'ready.json').write_text('{}', encoding='utf-8')"
    )
    (folder / "__init__.py").write_text(
        "from core.extensions.contract import SetupError\n"
        "class _Ext:\n"
        "    def __init__(self, ctx):\n"
        "        self.data_dir = ctx.data_dir\n"
        "    def run_setup(self, log_path):\n"
        f"        {body}\n"
        "    def shutdown(self):\n"
        "        pass\n"
        "def create(ctx):\n"
        "    return _Ext(ctx)\n",
        encoding="utf-8",
    )


def _wait_for_setup(client: TestClient, ext_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        item = _item(client, ext_id)
        if item["setup"]["state"] != "installing":
            return item
        time.sleep(0.05)
    raise AssertionError("setup never finished")


@pytest.mark.parametrize("fail", [False, True])
def test_setup_runs_in_the_background_and_enables_on_success(client, monkeypatch, tmp_path, fail) -> None:
    ext_id = "setup-demo-fail" if fail else "setup-demo"
    _fake_setup_extension(tmp_path, ext_id, fail=fail)
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    monkeypatch.setattr("api.routes.extensions.get_discovery", lambda: found)

    started = client.post(f"/api/extensions/{ext_id}/setup", json={"enable_on_success": True})
    assert started.status_code == 202, started.text
    item = _wait_for_setup(client, ext_id)

    if fail:
        assert item["setup"] == {"state": "failed", "detail": "network unreachable"}
        assert item["enabled"] is False
    else:
        assert item["setup"]["state"] == "ready"
        assert item["enabled"] is True
    assert not (extension_data_dir(ext_id) / "setup.lock").exists()


def test_an_invalid_extension_cannot_be_enabled_or_set_up(client, monkeypatch, tmp_path) -> None:
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "manifest.json").write_text("{", encoding="utf-8")
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    monkeypatch.setattr("api.routes.extensions.get_discovery", lambda: found)

    item = _item(client, "broken")
    assert item["valid"] is False and "not valid JSON" in item["invalid_reason"]
    for response in (
        client.put("/api/extensions/broken/enabled", json={"enabled": True}),
        client.post("/api/extensions/broken/setup", json={"enable_on_success": True}),
    ):
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "INVALID_EXTENSION"


def test_the_policy_ui_lists_extension_commands_under_their_category(client) -> None:
    response = client.get("/api/cli-policy/virtual-commands")
    assert response.status_code == 200, response.text
    payload = response.json()
    bv = next(command for command in payload["commands"] if command["name"] == "bv")
    assert bv["category"] == "extensions"
    assert bv["usage_syntax"].startswith("bv open <url>")
    assert {"name": "extensions", "description": "Commands added by enabled extensions"} in payload["categories"]


# ─── live view (R8) ───


def _write_marker(agent_id: str, pid: int) -> None:
    """A session marker for ``agent_id`` (R29): the live view lists only these."""
    folder = extension_data_dir(_BV) / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{agent_id}.json").write_text(json.dumps({
        "session_id": f"session-{agent_id}", "pid": pid,
        "opened_at": "2026-09-28T09:00:00+00:00", "url": "https://example.com",
    }), encoding="utf-8")


def _write_shot(agent_id: str, command: str, taken_at: str, *, marker: bool = True) -> Path:
    """One screenshot in the agent's session folder, with a live marker (this process) unless told not to."""
    if marker:
        _write_marker(agent_id, os.getpid())
    folder = extension_data_dir(_BV) / "shots" / agent_id / f"session-{agent_id}"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = taken_at.replace(":", "").replace("-", "")[:15]
    png = folder / f"{stamp}.png"
    png.with_suffix(".json").write_text(json.dumps({
        "command": command, "url": "https://example.com", "title": "Example",
        "window": "desktop 1280x800", "view": "view: full page", "image": "1280x800",
        "marks": 4, "taken_at": taken_at,
    }), encoding="utf-8")
    png.write_bytes(b"\x89PNG-fixture-" + agent_id.encode())
    return png


@pytest.fixture()
def live_shots():
    import shutil

    for name in ("shots", "sessions"):
        shutil.rmtree(extension_data_dir(_BV) / name, ignore_errors=True)
    yield
    for name in ("shots", "sessions"):
        shutil.rmtree(extension_data_dir(_BV) / name, ignore_errors=True)


def _enable_bv(client) -> None:
    _mark_ready()
    assert client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True}).status_code == 200


def test_the_list_item_says_whether_it_has_a_live_view(client) -> None:
    assert _item(client)["live_view"] is True


def test_live_lists_each_agents_latest_shot_newest_first(client, live_shots) -> None:
    older = db.create_agent("Seer", role="Researcher")
    newer = db.create_agent("Scout", role="Researcher")
    _write_shot(older.id, "bv open example.com", "2026-09-28T10:00:00+00:00")
    _write_shot(newer.id, "bv click 5", "2026-09-28T11:00:00+00:00")
    _enable_bv(client)

    response = client.get(f"/api/extensions/{_BV}/live")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["agent_name"] for item in items] == ["Scout", "Seer"]
    first = items[0]
    stamp = 1790593200000  # 2026-09-28T11:00:00Z in epoch ms
    assert first["image_url"] == f"/api/extensions/{_BV}/live/{newer.id}/image?t={stamp}"
    assert first["command"] == "bv click 5"
    assert first["caption_lines"] == ["window: desktop 1280x800", "view: full page", "image 1280x800", "marks: 4"]
    assert "agent_missing" not in first


def test_a_deleted_agent_is_listed_as_missing(client, live_shots) -> None:
    _write_shot("agent-gone", "bv open example.com", "2026-09-28T10:00:00+00:00")
    _enable_bv(client)
    items = client.get(f"/api/extensions/{_BV}/live").json()["items"]
    assert items == [{**items[0], "agent_id": "agent-gone", "agent_name": "agent-gone", "agent_missing": True}]


def test_live_lists_only_agents_whose_session_is_live(client, live_shots) -> None:
    import subprocess
    import sys

    live = db.create_agent("Seer", role="Researcher")
    unmarked = db.create_agent("Scout", role="Researcher")
    dead = db.create_agent("Ghost", role="Researcher")
    _write_shot(live.id, "bv open example.com", "2026-09-28T10:00:00+00:00")
    _write_shot(unmarked.id, "bv open example.com", "2026-09-28T10:00:00+00:00", marker=False)
    dead_shot = _write_shot(dead.id, "bv open example.com", "2026-09-28T10:00:00+00:00", marker=False)
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    _write_marker(dead.id, finished.pid)
    _enable_bv(client)

    items = client.get(f"/api/extensions/{_BV}/live").json()["items"]

    assert [item["agent_id"] for item in items] == [live.id]
    for agent in (unmarked, dead):
        assert client.get(f"/api/extensions/{_BV}/live/{agent.id}/image").status_code == 404
    assert dead_shot.is_file()  # the app process never deletes


def test_live_is_refused_while_disabled_and_unknown_is_404(client, live_shots) -> None:
    _mark_ready()
    response = client.get(f"/api/extensions/{_BV}/live")
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "EXTENSION_DISABLED"
    assert client.get("/api/extensions/nope/live").status_code == 404


def test_live_image_is_served_uncached_and_refuses_anything_else(client, live_shots) -> None:
    agent = db.create_agent("Seer", role="Researcher")
    png = _write_shot(agent.id, "bv open example.com", "2026-09-28T10:00:00+00:00")
    _enable_bv(client)

    image = client.get(f"/api/extensions/{_BV}/live/{agent.id}/image")
    assert image.status_code == 200
    assert image.content == png.read_bytes()
    assert image.headers["cache-control"] == "no-store"
    assert image.headers["content-type"] == "image/png"

    for bad in ("unknown-agent", "..", "..%2F..%2Fsetup.log", f"{agent.id}%2F..%2F.."):
        assert client.get(f"/api/extensions/{_BV}/live/{bad}/image").status_code == 404, bad


def test_the_app_process_can_load_browser_vision_without_starting_a_browser(client, live_shots) -> None:
    import threading

    from core.extensions.loader import load_extension

    _enable_bv(client)
    assert client.get(f"/api/extensions/{_BV}/live").status_code == 200
    instance = load_extension(get_discovery().get(_BV))
    assert instance._host._thread is None and instance._host._loop is None
    assert not any(thread.name == "browser-vision" for thread in threading.enumerate())


def test_an_extension_that_broke_its_live_view_contract_is_listed_invalid(client, monkeypatch, tmp_path) -> None:
    folder = tmp_path / "liar"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "id": "liar", "name": "Liar", "version": "1.0.0", "description": "d",
        "command": {"name": "liar", "summary": "s", "usage": "u", "help": "h"},
        "setup": {"required": False}, "live_view": True,
    }), encoding="utf-8")
    (folder / "__init__.py").write_text(
        "class _Ext:\n    def shutdown(self):\n        pass\ndef create(ctx):\n    return _Ext()\n",
        encoding="utf-8",
    )
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    monkeypatch.setattr("api.routes.extensions.get_discovery", lambda: found)
    assert client.put("/api/extensions/liar/enabled", json={"enabled": True}).status_code == 200

    live = client.get("/api/extensions/liar/live")
    assert live.status_code == 409 and live.json()["detail"]["error"] == "INVALID_EXTENSION"
    item = _item(client, "liar")
    assert item["valid"] is False and item["enabled"] is False
    assert "no live_view() method" in item["invalid_reason"]
