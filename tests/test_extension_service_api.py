"""Extensions API for command-less service extensions: the enable toggle and its rollback,
extension-wide settings (off: stored unverified; on: verified first, secrets masked) and the panel routes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.bm_cli.command_registry import CORE_COMMAND_NAMES
from core.extensions import loader
from core.extensions.registry import discover
from core.extensions.service_host import ExtensionServiceHost

_COUNTER = iter(range(1, 100_000))

_STUB = '''
from core.extensions.contract import ConfigError, PanelError, ServiceStartError
from core.extensions.panel import PanelAction, PanelFact, PanelStatus

EVENTS = []

class _Ext:
    def __init__(self, ctx):
        self.ctx = ctx
        self.reads_settings = bool(ctx.manifest.defaults.get("reads_settings"))
    async def start_service(self, app):
        port = self.ctx.read_settings()["port"] if self.reads_settings else "-"
        EVENTS.append(("start", port))
        if port == "1999":
            raise ServiceStartError("Port 1999 is already in use.")
    async def stop_service(self):
        EVENTS.append(("stop",))
    def panel(self):
        return PanelStatus(
            message="Running.",
            facts=[PanelFact(label="Code", value="ABCD2345", copyable=True, secret=True)],
            actions=[PanelAction(key="new_code", label="Generate new code")],
        )
    def panel_action(self, key, values):
        EVENTS.append(("action", key, dict(values)))
        if key == "boom":
            raise PanelError("That didn’t work.")
    def verify_settings(self, values):
        EVENTS.append(("verify", dict(values)))
        if values.get("port") == "2000":
            raise ConfigError("Port 2000 isn’t available.")
        return "Port " + values["port"] + " is available."
    def shutdown(self):
        EVENTS.append(("shutdown",))

def create(ctx):
    return _Ext(ctx)
'''

_NUMBERS_ONLY = {
    "label": "Svc settings",
    "help": "Pick a port.",
    "fields": [{"key": "port", "label": "Port", "kind": "number", "required": False, "default": "40000", "min": 1024, "max": 65535}],
}
_WITH_SECRET = {
    "label": "Svc settings",
    "help": "Pick a port.",
    "fields": [
        *_NUMBERS_ONLY["fields"],
        {"key": "token", "label": "Token", "kind": "secret", "required": False},
    ],
}


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


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    return TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()})


class _RecordingServices:
    """Stands in for ``runtime_services``: records the commands the routes file."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def extensions_reconcile(self) -> None:
        self.calls.append("extensions_reconcile")


@pytest.fixture()
def runtime(monkeypatch) -> _RecordingServices:
    recording = _RecordingServices()
    monkeypatch.setattr("api.routes.extensions.runtime_services", recording)
    return recording


def _install(client, monkeypatch, tmp_path, **manifest_parts) -> SimpleNamespace:
    """A command-less service extension with a unique id, behind a fresh service host."""
    monkeypatch.setattr(loader, "_loaded", {})
    ext_id = f"svc-{next(_COUNTER)}"
    folder = tmp_path / ext_id
    folder.mkdir()
    manifest = {
        "id": ext_id, "name": "Svc", "version": "1.0.0", "description": "A service.",
        "service": True, "panel": True, "settings": _NUMBERS_ONLY, "defaults": {"reads_settings": True},
        **manifest_parts,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "__init__.py").write_text(_STUB, encoding="utf-8")
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    host = ExtensionServiceHost(discovery=lambda: found, enabled=lambda: _enabled())
    host.attach(object())
    monkeypatch.setattr("api.routes.extensions.get_discovery", lambda: found)
    monkeypatch.setattr("api.routes.extensions.extension_services", host)
    events = loader.import_package(found.get(ext_id)).EVENTS
    events.clear()
    return SimpleNamespace(id=ext_id, host=host, events=events)


def _enabled() -> frozenset[str]:
    from core.extensions.registry import enabled_ids

    return enabled_ids()


def _item(client, ext_id):
    return next(item for item in client.get("/api/extensions").json() if item["id"] == ext_id)


def _error(response) -> dict:
    return response.json()["detail"]


# ─── list item and the enable toggle ───


def test_the_list_item_of_a_command_less_service_names_its_surfaces(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    item = _item(client, svc.id)
    assert item["command"] is None
    assert item["panel"] is True and item["settings"] == {"label": "Svc settings"}
    assert item["service_error"] is None and item["valid"] is True


def test_turning_a_service_on_starts_it_and_off_stops_it(client, monkeypatch, tmp_path, runtime) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    on = client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True and svc.host.is_running(svc.id)
    assert svc.events == [("start", "40000")]
    assert runtime.calls == ["extensions_reconcile"]

    off = client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert not svc.host.is_running(svc.id)
    assert svc.events[-2:] == [("stop",), ("shutdown",)]


def test_a_failed_start_rolls_the_switch_back_and_says_why(client, monkeypatch, tmp_path, runtime) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    assert client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": "1999"}}).status_code == 200

    refused = client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": True})

    assert refused.status_code == 409
    assert _error(refused) == {"error": "SERVICE_START_FAILED", "message": "Port 1999 is already in use."}
    assert _item(client, svc.id)["enabled"] is False, "the flag is rolled back"
    assert json.loads(config.get_live("extensions_enabled")) == []
    assert not svc.host.is_running(svc.id)
    assert runtime.calls == ["extensions_reconcile"], "the worker is told the final state once"


def test_a_service_that_failed_at_boot_is_on_but_shows_its_error(client, monkeypatch, tmp_path) -> None:
    import asyncio

    from core.extensions.registry import set_enabled

    svc = _install(client, monkeypatch, tmp_path)
    client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": "1999"}})
    set_enabled(svc.id, True)
    assert asyncio.run(svc.host.reconcile()) == {svc.id: "Port 1999 is already in use."}

    item = _item(client, svc.id)
    assert item["enabled"] is True and item["service_error"] == "Port 1999 is already in use."


# ─── settings ───


def test_settings_before_anything_is_stored_read_as_defaults(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    got = client.get(f"/api/extensions/{svc.id}/settings")
    assert got.status_code == 200
    assert got.json() == {
        "label": "Svc settings", "help": "Pick a port.", "configured": False,
        "fields": [{"key": "port", "label": "Port", "kind": "number", "required": False,
                    "min": 1024, "max": 65535, "default": "40000", "value": "40000"}],
    }


def test_settings_saved_while_off_are_stored_without_loading_the_extension(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    saved = client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": "41000"}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["verified"] is None
    assert saved.json()["fields"][0]["value"] == "41000"
    assert db.get_extension_settings(svc.id) == {"port": "41000"}
    assert svc.events == [], "an extension that is off is never imported or asked"
    assert svc.id not in loader.loaded_extensions()


def test_settings_saved_while_on_are_verified_first_and_a_refusal_stores_nothing(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    assert client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": True}).status_code == 200
    svc.events.clear()

    refused = client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": "2000"}})
    assert refused.status_code == 422
    assert _error(refused) == {"error": "CONFIG_VERIFY_FAILED", "message": "Port 2000 isn’t available."}
    assert db.get_extension_settings(svc.id) is None
    assert svc.events == [("verify", {"port": "2000"})], "refused: no restart"

    accepted = client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": "41001"}})
    assert accepted.status_code == 200
    assert accepted.json()["verified"] == "Port 41001 is available."
    assert db.get_extension_settings(svc.id) == {"port": "41001"}


def test_a_running_service_restarts_on_the_new_settings(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": True})
    svc.events.clear()

    assert client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": "41002"}}).status_code == 200

    assert svc.events == [
        ("verify", {"port": "41002"}), ("stop",), ("shutdown",), ("start", "41002"),
    ]
    assert svc.host.is_running(svc.id)


def test_a_restart_that_fails_is_a_conflict_with_the_reason(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": True})

    response = client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": "1999"}})

    assert response.status_code == 409
    assert _error(response) == {"error": "SERVICE_START_FAILED", "message": "Port 1999 is already in use."}
    assert db.get_extension_settings(svc.id) == {"port": "1999"}, "the values are stored; the service is stopped"
    assert not svc.host.is_running(svc.id)


@pytest.mark.parametrize("values, message", [
    ({"port": "80"}, "Port must be a whole number from 1024 to 65535."),
    ({"port": "abc"}, "Port must be a whole number from 1024 to 65535."),
    ({"other": "1"}, "Unknown field: other"),
])
def test_settings_are_checked_against_the_declared_fields(client, monkeypatch, tmp_path, values, message) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    response = client.put(f"/api/extensions/{svc.id}/settings", json={"values": values})
    assert response.status_code == 422
    assert _error(response) == {"error": "CONFIG_INVALID", "message": message}
    assert db.get_extension_settings(svc.id) is None


def test_a_blank_number_is_stored_as_its_default(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    assert client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": ""}}).status_code == 200
    assert db.get_extension_settings(svc.id) == {"port": "40000"}


def test_secret_settings_are_masked_and_a_blank_keeps_the_stored_one(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path, settings=_WITH_SECRET, defaults={})
    first = client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": "41003", "token": "hunter2"}})
    assert first.status_code == 200
    assert "hunter2" not in first.text
    token = next(field for field in first.json()["fields"] if field["key"] == "token")
    assert token == {"key": "token", "label": "Token", "kind": "secret", "required": False, "set": True}
    assert "hunter2" not in client.get(f"/api/extensions/{svc.id}/settings").text

    client.put(f"/api/extensions/{svc.id}/settings", json={"values": {"port": "41004", "token": ""}})
    assert db.get_extension_settings(svc.id) == {"port": "41004", "token": "hunter2"}


def test_an_extension_without_settings_refuses_the_settings_routes(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path, settings=None)
    get = client.get(f"/api/extensions/{svc.id}/settings")
    put = client.put(f"/api/extensions/{svc.id}/settings", json={"values": {}})
    assert get.status_code == put.status_code == 409
    assert _error(get)["error"] == "NO_SETTINGS" and _error(put)["error"] == "NO_SETTINGS"
    assert client.get("/api/extensions/nope/settings").status_code == 404


# ─── panel ───


def test_the_panel_is_read_from_an_enabled_extension(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    assert _error(client.get(f"/api/extensions/{svc.id}/panel"))["error"] == "EXTENSION_DISABLED"
    client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": True})

    response = client.get(f"/api/extensions/{svc.id}/panel")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Running.", "error": None,
        "facts": [{"label": "Code", "value": "ABCD2345", "copyable": True, "secret": True}],
        "actions": [{"key": "new_code", "label": "Generate new code", "values": {}}],
    }


def test_a_panel_action_runs_then_returns_the_refreshed_panel_and_announces_it(client, monkeypatch, tmp_path) -> None:
    from api.websocket import manager

    announced: list[list[str]] = []

    async def record(surfaces):
        announced.append(surfaces)

    monkeypatch.setattr(manager, "broadcast_operator_invalidate", record)
    svc = _install(client, monkeypatch, tmp_path)
    client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": True})
    svc.events.clear()

    response = client.post(f"/api/extensions/{svc.id}/panel/actions", json={"action": "new_code", "values": {"a": "b"}})

    assert response.status_code == 200 and response.json()["message"] == "Running."
    assert svc.events == [("action", "new_code", {"a": "b"})]
    assert announced == [["extensions"]]


def test_a_failed_panel_action_is_a_conflict_with_the_extensions_message(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path)
    client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": True})
    response = client.post(f"/api/extensions/{svc.id}/panel/actions", json={"action": "boom"})
    assert response.status_code == 409
    assert _error(response) == {"error": "PANEL_FAILED", "message": "That didn’t work."}


def test_an_extension_without_a_panel_refuses_the_panel_routes(client, monkeypatch, tmp_path) -> None:
    svc = _install(client, monkeypatch, tmp_path, panel=False)
    client.put(f"/api/extensions/{svc.id}/enabled", json={"enabled": True})
    response = client.get(f"/api/extensions/{svc.id}/panel")
    assert response.status_code == 409 and _error(response)["error"] == "NO_PANEL"
    assert _item(client, svc.id)["panel"] is False
