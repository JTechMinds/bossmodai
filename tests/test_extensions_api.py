"""Extensions API: list, enable/disable, setup, and the settings guard."""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from tests._connections import model_connection
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
    (data_dir / "ready.json").write_text(json.dumps({"browser": "test", "browser_kind": "chromium"}), encoding="utf-8")


def _item(client: TestClient, ext_id: str = _BV) -> dict:
    response = client.get("/api/extensions")
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["id"] == ext_id)


def test_list_shows_browser_vision_with_setup_state_and_excluded_agents(client) -> None:
    db.set_supports_images("vision-model", True)
    db.create_agent("Seer", role="Researcher", connection_id=model_connection("vision-model"))
    blind = db.create_agent("Scribe", role="Writer", connection_id=model_connection("text-model"))

    item = _item(client)

    assert item["name"] == "Browser Vision"
    assert item["valid"] is True and item["invalid_reason"] is None
    assert item["enabled"] is False
    assert item["command"] == {"name": "bv", "summary": "Browse websites by screenshot + grid."}
    assert item["requires_image_model"] is True
    assert item["setup"] == {"state": "missing", "detail": None}
    assert item["setup_label"] == "Download browser (~200 MB)"
    assert item["excluded_agents"] == [{"id": blind.id, "name": "Scribe", "model": "text-model"}]


def test_an_install_from_before_full_chromium_shows_setup_again(client) -> None:
    """R35: the card reads ready_requires from the manifest, without importing the extension."""
    data_dir = extension_data_dir(_BV)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "ready.json").write_text(json.dumps({"playwright": "1.63.0", "browser": "153.0"}), encoding="utf-8")
    assert _item(client)["setup"] == {
        "state": "missing",
        "detail": "Setup needs to run again: the installed version is out of date.",
    }
    response = client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True})
    assert response.status_code == 409
    _mark_ready()
    assert _item(client)["setup"] == {"state": "ready", "detail": None}


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


class _RecordingServices:
    """Stands in for ``runtime_services``: records the commands the routes file."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def extensions_reconcile(self) -> None:
        self.calls.append(("extensions_reconcile",))

    def extension_config_changed(self, extension_id: str, agent_id: str) -> None:
        self.calls.append(("extension_config_changed", extension_id, agent_id))


@pytest.fixture()
def services(monkeypatch) -> _RecordingServices:
    recording = _RecordingServices()
    monkeypatch.setattr("api.routes.extensions.runtime_services", recording)
    return recording


def test_enable_and_disable_ask_the_worker_to_reconcile(client, services) -> None:
    refused = client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True})
    assert refused.status_code == 409 and services.calls == []
    _mark_ready()
    assert client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True}).status_code == 200
    assert services.calls == [("extensions_reconcile",)]
    assert client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": False}).status_code == 200
    assert services.calls == [("extensions_reconcile",), ("extensions_reconcile",)]


def test_enable_and_disable_announce_the_live_view_change(client, monkeypatch) -> None:
    from api.websocket import manager

    calls: list[tuple] = []

    async def _record(extension_id, agent_id):
        calls.append((extension_id, agent_id))

    monkeypatch.setattr(manager, "broadcast_extension_live", _record)
    refused = client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True})
    assert refused.status_code == 409 and calls == []
    _mark_ready()
    assert client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True}).status_code == 200
    assert client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": False}).status_code == 200
    assert calls == [(_BV, None), (_BV, None)]


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
        "    def handle(self, ctx, parsed, body):\n"
        "        return None\n"
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
    assert item["live_view"] is None
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


def _write_shot(
    agent_id: str, command: str, taken_at: str, *, marker: bool = True, url: str = "https://example.com",
) -> Path:
    """One screenshot in the agent's session folder, with a live marker (this process) unless told not to."""
    if marker:
        _write_marker(agent_id, os.getpid())
    folder = extension_data_dir(_BV) / "shots" / agent_id / f"session-{agent_id}"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = taken_at.replace(":", "").replace("-", "")[:15]
    png = folder / f"{stamp}.png"
    png.with_suffix(".json").write_text(json.dumps({
        "command": command, "url": url, "title": "Example",
        "window": "desktop 1280x800", "view": "full page", "image": "1280x800",
        "marks": "4", "taken_at": taken_at,
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


def test_the_list_item_names_its_live_view_or_has_none(client) -> None:
    assert _item(client)["live_view"] == {"label": "Browser view", "icon": "globe"}
    assert _item(client, "ms365-mail")["live_view"] is None


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
    assert first["facts"] == [
        {"label": "resolution", "value": "1280x800"},
        {"label": None, "value": "full page"},
        {"label": "marks", "value": "4"},
    ]
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


@pytest.fixture()
def launched(monkeypatch) -> list[tuple[Path, str]]:
    """Record file-manager launches instead of opening one."""
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        "api.routes._shared._launch_file_explorer",
        lambda path, *, opener: calls.append((path, opener)),
    )
    return calls


def test_live_open_folder_opens_the_folder_of_the_agents_file_page(client, live_shots, launched, tmp_path) -> None:
    agent = db.create_agent("Seer", role="Researcher")
    page = tmp_path / "pages" / "hud.png"
    page.parent.mkdir()
    page.write_bytes(b"png")
    _write_shot(agent.id, "bv open hud.png", "2026-09-28T10:00:00+00:00", url=page.as_uri())
    _enable_bv(client)

    response = client.post(f"/api/extensions/{_BV}/live/{agent.id}/open-folder")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "path": str(page.parent)}
    assert launched == [(page.parent, "auto")]


def test_live_open_folder_refuses_a_page_that_is_not_a_local_file(client, live_shots, launched) -> None:
    agent = db.create_agent("Seer", role="Researcher")
    _write_shot(agent.id, "bv open example.com", "2026-09-28T10:00:00+00:00", url="https://example.com")
    _enable_bv(client)

    response = client.post(f"/api/extensions/{_BV}/live/{agent.id}/open-folder")

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "LIVE_URL_NOT_FILE"
    assert launched == []


def test_live_open_folder_is_404_for_a_missing_folder_or_an_unknown_agent(client, live_shots, launched, tmp_path) -> None:
    agent = db.create_agent("Seer", role="Researcher")
    gone = tmp_path / "gone" / "page.html"
    _write_shot(agent.id, "bv open page.html", "2026-09-28T10:00:00+00:00", url=gone.as_uri())
    _enable_bv(client)

    missing = client.post(f"/api/extensions/{_BV}/live/{agent.id}/open-folder")
    unknown = client.post(f"/api/extensions/{_BV}/live/unknown-agent/open-folder")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "That folder no longer exists."
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "No live view for that agent"
    assert launched == []


def test_live_open_folder_asks_for_a_file_manager_when_the_launch_fails(client, live_shots, monkeypatch, tmp_path) -> None:
    def _refuse(path, *, opener):
        raise OSError("no such file manager")

    monkeypatch.setattr("api.routes._shared._launch_file_explorer", _refuse)
    agent = db.create_agent("Seer", role="Researcher")
    _write_shot(agent.id, "bv open x", "2026-09-28T10:00:00+00:00", url=tmp_path.as_uri())
    _enable_bv(client)

    response = client.post(f"/api/extensions/{_BV}/live/{agent.id}/open-folder")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "desk_open_folder_handler_invalid"
    assert detail["message"] == "no such file manager"
    assert isinstance(detail["options"], list)


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
        "setup": {"required": False}, "live_view": {"label": "Liar view", "icon": "monitor"},
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


# A live-view extension whose output has no address (like a desktop): its
# item carries url=None. The agent id is read from a file the test writes.
_ADDRESSLESS_INIT = '''
from datetime import datetime, timezone
from core.extensions.contract import LiveViewFact, LiveViewItem

class _Ext:
    def __init__(self, ctx):
        self.ctx = ctx
    def handle(self, ctx, parsed, body):
        raise AssertionError("not used")
    def shutdown(self):
        pass
    def live_view(self):
        agent_id = (self.ctx.data_dir / "agent.txt").read_text(encoding="utf-8")
        shot = self.ctx.data_dir / "shots" / (agent_id + ".png")
        shot.parent.mkdir(parents=True, exist_ok=True)
        shot.write_bytes(b"\\x89PNG-desk")
        return [LiveViewItem(
            agent_id=agent_id, image_path=shot,
            taken_at=datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc),
            command="desk view", url=None, title="Mozilla Firefox",
            facts=[LiveViewFact(label="screen", value="1280x800")],
        )]

def create(ctx):
    return _Ext(ctx)
'''


def test_a_live_item_without_an_address_has_a_null_url_and_no_folder(client, monkeypatch, tmp_path, launched) -> None:
    ext_id = f"desk-view-{next(_fake_counter)}"
    folder = tmp_path / ext_id
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "id": ext_id, "name": "Desk View", "version": "1.0.0", "description": "d",
        "command": {"name": ext_id.replace("-", "")[:15], "summary": "s", "usage": "u", "help": "h"},
        "setup": {"required": False}, "live_view": {"label": "Desktop view", "icon": "monitor"},
    }), encoding="utf-8")
    (folder / "__init__.py").write_text(_ADDRESSLESS_INIT, encoding="utf-8")
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    monkeypatch.setattr("api.routes.extensions.get_discovery", lambda: found)
    assert client.put(f"/api/extensions/{ext_id}/enabled", json={"enabled": True}).status_code == 200
    agent = db.create_agent("Seer", role="Researcher")
    extension_data_dir(ext_id).mkdir(parents=True, exist_ok=True)
    (extension_data_dir(ext_id) / "agent.txt").write_text(agent.id, encoding="utf-8")

    assert _item(client, ext_id)["live_view"] == {"label": "Desktop view", "icon": "monitor"}
    [row] = client.get(f"/api/extensions/{ext_id}/live").json()["items"]
    assert row["url"] is None and row["title"] == "Mozilla Firefox" and row["agent_name"] == "Seer"
    assert row["facts"] == [{"label": "screen", "value": "1280x800"}]

    response = client.post(f"/api/extensions/{ext_id}/live/{agent.id}/open-folder")
    assert response.status_code == 409
    assert response.json()["detail"] == {"error": "LIVE_URL_NOT_FILE", "message": "That page is not a local file."}
    assert launched == []


# ─── per-agent config and view (agent_config / agent_view) ───

_FAKE_SECRET = "fake-secret-value-123"
_fake_counter = iter(range(1, 10_000))

_FAKE_INIT = '''
import json
from core.extensions.contract import (
    ConfigError, AgentViewColumn, AgentViewError, AgentViewItem, AgentViewPage, AgentViewRow,
)

class _Ext:
    def __init__(self, ctx):
        self.ctx = ctx
    def handle(self, ctx, parsed, body):
        raise AssertionError("not used")
    def shutdown(self):
        pass
    def verify_agent_config(self, values):
        self.ctx.data_dir.mkdir(parents=True, exist_ok=True)
        (self.ctx.data_dir / "verified.json").write_text(json.dumps(dict(values)), encoding="utf-8")
        if values.get("token") == "bad":
            raise ConfigError("The client secret is wrong or has expired. Create a new secret and try again.")
        return "Connected to " + values.get("address", "nobody")
    def agent_view(self, agent_id, *, view, skip, top):
        assert view == "inbox"
        config = self.ctx.read_agent_config(agent_id)
        if config["token"] == "throttled":
            raise AgentViewError("GRAPH_THROTTLED", "retry after 5s")
        rows = [AgentViewRow(id="m1", cells={"subject": "Hello"}, emphasis=True)]
        return AgentViewPage(columns=[AgentViewColumn(key="subject", label="Subject")], rows=rows[:top],
                             has_more=skip == 0, caption="Inbox of " + config["address"])
    def agent_view_item(self, agent_id, item_id, *, view):
        assert view == "inbox"
        if item_id != "m1":
            raise AgentViewError("UNKNOWN_MESSAGE_ID", item_id + " is not listed")
        return AgentViewItem(title="Hello", facts=[("From", "alice@x.com")], body_text="Line 1\\nLine 2")

class _WakeExt(_Ext):
    def poll_wake(self, agent_id):
        raise AssertionError("not used")
    def commit_wake(self, batch):
        raise AssertionError("not used")
    def skip_wake(self, agent_id):
        raise AssertionError("not used")
    def describe_wake_error(self, exc):
        raise AssertionError("not used")

def create(ctx):
    return _WakeExt(ctx) if ctx.manifest.wake is not None else _Ext(ctx)
'''


_INTERVAL_FIELD = {
    "key": "every", "label": "Check every (seconds)", "kind": "number",
    "min": 15, "max": 3600, "default": "90", "required": False,
}


def _install_fake(client, monkeypatch, tmp_path, *, wake: bool, fields=None, wake_block=None, extra=None):
    """A per-agent extension with a unique id (the loader caches instances per id).

    ``fields`` replaces the mailbox fields, ``wake_block`` the interval-field
    wake, and ``extra`` adds manifest keys (e.g. ``requires``).
    """
    ext_id = f"fake-mail-{next(_fake_counter)}"
    folder = tmp_path / ext_id
    folder.mkdir()
    if fields is None:
        fields = [
            {"key": "token", "label": "Token", "kind": "secret"},
            {"key": "address", "label": "Address", "kind": "email", "summary": True},
            {"key": "note", "label": "Note", "kind": "text", "required": False},
        ] + ([_INTERVAL_FIELD] if wake and wake_block is None else [])
    manifest = {
        "id": ext_id, "name": "Fake Mail", "version": "1.0.0", "description": "d",
        "command": {"name": ext_id.replace("-", "")[:15], "summary": "s", "usage": "u", "help": "h"},
        "setup": {"required": False},
        "agent_config": {"label": "Fake mailbox", "help": "Para one.\n\nPara two.", "fields": fields},
        "agent_view": {"label": "Open inbox", "views": [{"key": "inbox", "label": "Inbox"}]},
        **(extra or {}),
    }
    if wake:
        manifest["wake"] = wake_block if wake_block is not None else {"interval_field": "every"}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "__init__.py").write_text(_FAKE_INIT, encoding="utf-8")
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    monkeypatch.setattr("api.routes.extensions.get_discovery", lambda: found)
    assert client.put(f"/api/extensions/{ext_id}/enabled", json={"enabled": True}).status_code == 200
    agent = db.create_agent("Iris", role="Researcher")
    return SimpleNamespace(id=ext_id, agent=agent, base=f"/api/extensions/{ext_id}/agents/{agent.id}")


@pytest.fixture()
def fake_mail(client, monkeypatch, tmp_path):
    """A per-agent extension without wake."""
    return _install_fake(client, monkeypatch, tmp_path, wake=False)


@pytest.fixture()
def fake_wake_mail(client, monkeypatch, tmp_path):
    """A per-agent extension that wakes agents, with a number interval field."""
    return _install_fake(client, monkeypatch, tmp_path, wake=True)


def _save(client, fake, **values):
    body = {"token": _FAKE_SECRET, "address": "reports@contoso.com", **values}
    return client.put(f"{fake.base}/config", json={"values": body})


def _verified(fake) -> dict:
    return json.loads((extension_data_dir(fake.id) / "verified.json").read_text(encoding="utf-8"))


def test_the_list_item_names_its_per_agent_surfaces(client, fake_mail) -> None:
    item = _item(client, fake_mail.id)
    assert item["agent_config"] == {"label": "Fake mailbox"}
    assert item["agent_view"] == {"label": "Open inbox", "views": [{"key": "inbox", "label": "Inbox"}]}


def test_an_extension_without_per_agent_surfaces_lists_them_as_null(client) -> None:
    item = _item(client)
    assert item["agent_config"] is None and item["agent_view"] is None
    mail = _item(client, "ms365-mail")
    assert mail["agent_config"] == {"label": "Microsoft 365 mailbox"}
    assert mail["agent_view"] == {"label": "Open inbox", "views": [
        {"key": "inbox", "label": "Inbox"}, {"key": "sent", "label": "Sent"},
    ]}


def test_the_desk_lists_enabled_per_agent_extensions_with_their_state(client, fake_mail) -> None:
    listed = client.get(f"/api/agents/{fake_mail.agent.id}/extensions")
    assert listed.status_code == 200, listed.text
    assert listed.json() == [{
        "id": fake_mail.id, "name": "Fake Mail", "config_label": "Fake mailbox", "view_label": "Open inbox",
        "configured": False, "summary": None, "wakes": False, "wake": None,
    }]
    assert _save(client, fake_mail).status_code == 200
    assert client.get(f"/api/agents/{fake_mail.agent.id}/extensions").json()[0]["summary"] == "reports@contoso.com"
    client.put(f"/api/extensions/{fake_mail.id}/enabled", json={"enabled": False})
    assert client.get(f"/api/agents/{fake_mail.agent.id}/extensions").json() == []
    assert client.get("/api/agents/nobody/extensions").status_code == 404


def test_get_config_masks_secrets(client, fake_mail) -> None:
    empty = client.get(f"{fake_mail.base}/config").json()
    assert empty["configured"] is False and empty["updated_at"] is None
    assert empty["help"] == "Para one.\n\nPara two."
    assert _save(client, fake_mail, note="hi").status_code == 200
    response = client.get(f"{fake_mail.base}/config")
    assert _FAKE_SECRET not in response.text
    payload = response.json()
    assert payload["configured"] is True and payload["updated_at"]
    assert payload["fields"] == [
        {"key": "token", "label": "Token", "kind": "secret", "required": True, "set": True},
        {"key": "address", "label": "Address", "kind": "email", "required": True, "value": "reports@contoso.com"},
        {"key": "note", "label": "Note", "kind": "text", "required": False, "value": "hi"},
    ]


def test_put_verifies_then_stores_and_never_echoes_the_secret(client, fake_mail) -> None:
    response = _save(client, fake_mail)
    assert response.status_code == 200, response.text
    assert response.json()["verified"] == "Connected to reports@contoso.com"
    assert _FAKE_SECRET not in response.text
    assert db.get_extension_agent_config(fake_mail.id, fake_mail.agent.id) == {
        "token": _FAKE_SECRET, "address": "reports@contoso.com", "note": "",
    }


@pytest.mark.parametrize("values, fragment", [
    ({"surprise": "x"}, "Unknown field: surprise"),
    ({"address": ""}, "Address is required."),
    ({"address": "not-an-address"}, "Address is not an email address."),
])
def test_put_validation_is_422_config_invalid(client, fake_mail, values, fragment) -> None:
    response = _save(client, fake_mail, **values)
    assert response.status_code == 422
    assert response.json()["detail"] == {"error": "CONFIG_INVALID", "message": fragment}
    assert db.get_extension_agent_config(fake_mail.id, fake_mail.agent.id) is None


def test_a_failed_verification_stores_nothing(client, fake_mail) -> None:
    response = _save(client, fake_mail, token="bad")
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error": "CONFIG_VERIFY_FAILED", "message": "The client secret is wrong or has expired. Create a new secret and try again.",
    }
    assert db.get_extension_agent_config(fake_mail.id, fake_mail.agent.id) is None


def test_a_blank_secret_keeps_the_stored_one_and_is_verified_with_it(client, fake_mail) -> None:
    assert _save(client, fake_mail).status_code == 200
    response = _save(client, fake_mail, token="", address="other@contoso.com")
    assert response.status_code == 200, response.text
    assert _verified(fake_mail)["token"] == _FAKE_SECRET
    assert db.get_extension_agent_config(fake_mail.id, fake_mail.agent.id)["token"] == _FAKE_SECRET


def test_a_blank_secret_with_nothing_stored_is_422(client, fake_mail) -> None:
    response = _save(client, fake_mail, token="  ")
    assert response.status_code == 422
    assert response.json()["detail"] == {"error": "CONFIG_INVALID", "message": "Token is required."}


def test_per_agent_routes_refuse_a_disabled_extension_and_unknowns(client, fake_mail) -> None:
    client.put(f"/api/extensions/{fake_mail.id}/enabled", json={"enabled": False})
    for response in (
        client.get(f"{fake_mail.base}/config"), _save(client, fake_mail),
        client.get(f"{fake_mail.base}/view", params={"view": "inbox"}),
    ):
        assert response.status_code == 409 and response.json()["detail"]["error"] == "EXTENSION_DISABLED"
    client.put(f"/api/extensions/{fake_mail.id}/enabled", json={"enabled": True})
    assert client.get(f"/api/extensions/{fake_mail.id}/agents/nobody/config").status_code == 404
    assert client.get(f"/api/extensions/nope/agents/{fake_mail.agent.id}/config").status_code == 404
    unknown_body = client.put(f"{fake_mail.base}/config", json={"values": {}, "extra": 1})
    assert unknown_body.status_code == 422


def test_an_extension_without_agent_config_is_409(client) -> None:
    _mark_ready()
    assert client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True}).status_code == 200
    agent = db.create_agent("Iris", role="Researcher")
    response = client.get(f"/api/extensions/{_BV}/agents/{agent.id}/config")
    assert response.status_code == 409 and response.json()["detail"]["error"] == "NO_AGENT_CONFIG"


def test_delete_config(client, fake_mail) -> None:
    assert client.delete(f"{fake_mail.base}/config").status_code == 404
    _save(client, fake_mail)
    assert client.delete(f"{fake_mail.base}/config").status_code == 204
    assert db.get_extension_agent_config(fake_mail.id, fake_mail.agent.id) is None


def test_saving_or_removing_a_config_tells_the_worker_which_pair_changed(client, services, fake_mail) -> None:
    assert services.calls == [("extensions_reconcile",)]  # the fixture turned the extension on
    services.calls.clear()
    changed = ("extension_config_changed", fake_mail.id, fake_mail.agent.id)
    assert _save(client, fake_mail, token="bad").status_code == 422
    assert client.delete(f"{fake_mail.base}/config").status_code == 404
    assert services.calls == [], "nothing stored or removed, nothing to apply"
    assert _save(client, fake_mail).status_code == 200
    assert services.calls == [changed]
    assert client.delete(f"{fake_mail.base}/config").status_code == 204
    assert services.calls == [changed, changed]


# ─── the Configure agents table (GET /extensions/{id}/agents) ───


def test_the_agents_table_lists_every_agent_with_its_state(client, fake_mail) -> None:
    db.set_supports_images("vision-model", True)
    seer = db.create_agent("Seer", role="Researcher", connection_id=model_connection("vision-model"))
    blind = db.create_agent("Scribe", role=None, connection_id=model_connection("text-model"))
    assert _save(client, fake_mail).status_code == 200

    response = client.get(f"/api/extensions/{fake_mail.id}/agents")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "label": "Fake mailbox",
        "help": "Para one.\n\nPara two.",
        "quick_enable": False,  # the token is a required secret
        "requires_image_model": False,
        "agents": [
            {"id": fake_mail.agent.id, "name": "Iris", "role": "Researcher", "color": fake_mail.agent.color,
             "configured": True, "summary": "reports@contoso.com", "image_capable": False},
            {"id": seer.id, "name": "Seer", "role": "Researcher", "color": seer.color,
             "configured": False, "summary": None, "image_capable": True},
            {"id": blind.id, "name": "Scribe", "role": None, "color": blind.color,
             "configured": False, "summary": None, "image_capable": False},
        ],
    }


def test_an_extension_whose_fields_are_all_optional_is_quick_enable(client, monkeypatch, tmp_path) -> None:
    fake = _install_fake(
        client, monkeypatch, tmp_path, wake=False,
        fields=[_INTERVAL_FIELD, {"key": "note", "label": "Note", "kind": "text", "required": False}],
        extra={"requires": {"image_model": True}},
    )
    db.set_supports_images("vision-model", True)
    seer = db.create_agent("Seer", role="Researcher", connection_id=model_connection("vision-model"))

    payload = client.get(f"/api/extensions/{fake.id}/agents").json()

    assert payload["quick_enable"] is True
    # What the table's switch then saves: {} stores every number default.
    assert client.put(f"{fake.base}/config", json={"values": {}}).status_code == 200
    assert db.get_extension_agent_config(fake.id, fake.agent.id) == {"every": "90", "note": ""}
    assert payload["requires_image_model"] is True
    assert [(agent["id"], agent["image_capable"]) for agent in payload["agents"]] == [
        (fake.agent.id, False), (seer.id, True),
    ]
    # image_capable is the same check that fills the card's excluded_agents.
    assert [agent["id"] for agent in _item(client, fake.id)["excluded_agents"]] == [fake.agent.id]


def test_a_required_number_field_is_not_quick_enable(client, monkeypatch, tmp_path) -> None:
    # _resolve_values refuses a blank required field before a number default
    # could fill it, so saving {} would fail: the switch must open the dialog.
    required_number = {**_INTERVAL_FIELD, "required": True}
    fake = _install_fake(client, monkeypatch, tmp_path, wake=False, fields=[required_number])
    assert client.get(f"/api/extensions/{fake.id}/agents").json()["quick_enable"] is False
    refused = client.put(f"{fake.base}/config", json={"values": {}})
    assert refused.status_code == 422
    assert refused.json()["detail"] == {"error": "CONFIG_INVALID", "message": "Check every (seconds) is required."}


def test_the_agents_table_refuses_unknown_disabled_and_config_less_extensions(client, fake_mail) -> None:
    assert client.get("/api/extensions/nope/agents").status_code == 404
    client.put(f"/api/extensions/{fake_mail.id}/enabled", json={"enabled": False})
    disabled = client.get(f"/api/extensions/{fake_mail.id}/agents")
    assert disabled.status_code == 409 and disabled.json()["detail"]["error"] == "EXTENSION_DISABLED"


def test_the_agents_table_refuses_an_extension_without_agent_config(client) -> None:
    _mark_ready()
    assert client.put(f"/api/extensions/{_BV}/enabled", json={"enabled": True}).status_code == 200
    response = client.get(f"/api/extensions/{_BV}/agents")
    assert response.status_code == 409 and response.json()["detail"]["error"] == "NO_AGENT_CONFIG"


def test_the_agents_table_refuses_an_invalid_extension(client, monkeypatch, tmp_path) -> None:
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "manifest.json").write_text("{", encoding="utf-8")
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    monkeypatch.setattr("api.routes.extensions.get_discovery", lambda: found)
    response = client.get("/api/extensions/broken/agents")
    assert response.status_code == 409 and response.json()["detail"]["error"] == "INVALID_EXTENSION"


def test_view_and_item_need_a_config_then_answer(client, fake_mail) -> None:
    refused = client.get(f"{fake_mail.base}/view", params={"view": "inbox"})
    assert refused.status_code == 409 and refused.json()["detail"]["error"] == "NOT_CONFIGURED"
    _save(client, fake_mail)

    page = client.get(f"{fake_mail.base}/view", params={"view": "inbox", "skip": 0, "top": 25})
    assert page.status_code == 200, page.text
    assert page.json() == {
        "columns": [{"key": "subject", "label": "Subject"}],
        "rows": [{"id": "m1", "cells": {"subject": "Hello"}, "emphasis": True}],
        "has_more": True,
        "caption": "Inbox of reports@contoso.com",
    }
    item = client.get(f"{fake_mail.base}/view/m1", params={"view": "inbox"})
    assert item.status_code == 200
    assert item.json() == {"title": "Hello", "facts": [["From", "alice@x.com"]], "body_text": "Line 1\nLine 2"}

    missing = client.get(f"{fake_mail.base}/view/m9", params={"view": "inbox"})
    assert missing.status_code == 502
    assert missing.json()["detail"] == {"error": "UNKNOWN_MESSAGE_ID", "message": "m9 is not listed"}
    for bad in ({"top": 0}, {"top": 101}, {"skip": -1}):
        assert client.get(f"{fake_mail.base}/view", params={"view": "inbox", **bad}).status_code == 422, bad


def test_a_view_failure_is_502_with_its_code(client, fake_mail) -> None:
    _save(client, fake_mail, token="throttled")
    response = client.get(f"{fake_mail.base}/view", params={"view": "inbox"})
    assert response.status_code == 502
    assert response.json()["detail"] == {"error": "GRAPH_THROTTLED", "message": "retry after 5s"}


def test_the_view_route_needs_a_view_and_refuses_an_unknown_one(client, fake_mail) -> None:
    _save(client, fake_mail)
    assert client.get(f"{fake_mail.base}/view").status_code == 422
    assert client.get(f"{fake_mail.base}/view/m1").status_code == 422
    unknown = client.get(f"{fake_mail.base}/view", params={"view": "sent"})
    assert unknown.status_code == 404
    assert client.get(f"{fake_mail.base}/view/m1", params={"view": "sent"}).status_code == 404


# ─── number fields and wake status ───


def test_a_blank_number_field_stores_its_default(client, fake_wake_mail) -> None:
    response = _save(client, fake_wake_mail, every="")
    assert response.status_code == 200, response.text
    assert db.get_extension_agent_config(fake_wake_mail.id, fake_wake_mail.agent.id)["every"] == "90"
    field = next(item for item in response.json()["fields"] if item["key"] == "every")
    assert field == {
        "key": "every", "label": "Check every (seconds)", "kind": "number", "required": False,
        "min": 15, "max": 3600, "default": "90", "value": "90",
    }
    assert _save(client, fake_wake_mail, every=" 120 ").status_code == 200
    assert db.get_extension_agent_config(fake_wake_mail.id, fake_wake_mail.agent.id)["every"] == "120"


@pytest.mark.parametrize("raw", ["14", "3601", "1.5", "ten", "-20"])
def test_a_number_out_of_range_or_not_whole_is_422(client, fake_wake_mail, raw) -> None:
    response = _save(client, fake_wake_mail, every=raw)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error": "CONFIG_INVALID", "message": "Check every (seconds) must be a whole number from 15 to 3600.",
    }
    assert db.get_extension_agent_config(fake_wake_mail.id, fake_wake_mail.agent.id) is None


def test_a_config_saved_before_the_number_field_reads_its_default(client, fake_wake_mail) -> None:
    db.set_extension_agent_config(fake_wake_mail.id, fake_wake_mail.agent.id, {
        "token": _FAKE_SECRET, "address": "reports@contoso.com", "note": "",
    })
    fields = client.get(f"{fake_wake_mail.base}/config").json()["fields"]
    assert next(item for item in fields if item["key"] == "every")["value"] == "90"


def test_the_desk_item_carries_the_wake_status(client, fake_wake_mail) -> None:
    agent_id = fake_wake_mail.agent.id
    url = f"/api/agents/{agent_id}/extensions"
    assert _save(client, fake_wake_mail).status_code == 200
    first = client.get(url).json()[0]
    assert first["wakes"] is True and first["wake"] is None

    db.record_wake_check(fake_wake_mail.id, agent_id, ok=True, error=None, new_count=2)
    ok = client.get(url).json()[0]["wake"]
    assert ok["ok"] is True and ok["error"] is None and ok["last_new_count"] == 2
    assert ok["checked_at"].endswith("+00:00") and ok["last_new_at"].endswith("+00:00")

    db.record_wake_check(fake_wake_mail.id, agent_id, ok=False, error="Couldn't reach Microsoft 365.", new_count=0)
    failed = client.get(url).json()[0]["wake"]
    assert failed["ok"] is False and failed["error"] == "Couldn't reach Microsoft 365."
    # A failed check keeps when mail last arrived.
    assert failed["last_new_count"] == 2 and failed["last_new_at"] == ok["last_new_at"]


def test_a_wake_without_desk_status_shows_no_wake_line(client, monkeypatch, tmp_path) -> None:
    fake = _install_fake(
        client, monkeypatch, tmp_path, wake=True, wake_block={"interval_seconds": 60, "desk_status": False},
    )
    assert _save(client, fake).status_code == 200
    # Even a stray status row is not read: the desk shows no health line.
    db.record_wake_check(fake.id, fake.agent.id, ok=True, error=None, new_count=1)
    [item] = client.get(f"/api/agents/{fake.agent.id}/extensions").json()
    assert item["wakes"] is False and item["wake"] is None
