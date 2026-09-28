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
from core.extensions.registry import discover

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
