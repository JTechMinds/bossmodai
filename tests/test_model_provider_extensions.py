"""The host supports independently installed providers without vendor branches."""

from __future__ import annotations
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from api.auth import LOCAL_API_TOKEN_HEADER, install_local_api_auth
from api.routes import router
from core import config
from core.extensions import loader, registry
from core.extensions.model_provider import ProviderError, provider_id, resolve_provider

_PROVIDER = """
from core.extensions.contract import SetupStatus
from core.extensions.model_provider import ProviderCompletion, ProviderStatus

def create(ctx):
    return DemoProvider(ctx)

class DemoProvider:
    def __init__(self, ctx): self.ctx = ctx
    def setup_status(self): return SetupStatus(state="not_required")
    def run_setup(self, log_path): pass
    def shutdown(self): pass
    def handle(self, ctx, parsed, body): pass
    def provider_status(self):
        return ProviderStatus(message="Demo provider ready", connected=True,
            actions=[{"key": "connect", "label": "Connect demo"}])
    def provider_action(self, action, values):
        self.ctx.model_provider.publish_connection("demo-model")
    async def provider_complete(self, request):
        return self.provider_complete_sync(request)
    def provider_complete_sync(self, request):
        self.last_request = request
        return ProviderCompletion("demo:" + request.messages[-1]["content"], request.model)
"""


@pytest.fixture()
def providers(monkeypatch, tmp_path):
    db.close_connection()
    path = Path(os.environ["BOSSMOD_DB_PATH"])
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    db.init_db()
    config.reload()
    for ext_id in ("alpha-model", "beta-model"):
        folder = tmp_path / ext_id
        folder.mkdir()
        (folder / "__init__.py").write_text(_PROVIDER)
        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "id": ext_id,
                    "name": ext_id,
                    "version": "1.0.0",
                    "description": "Independent provider",
                    "command": {
                        "name": ext_id,
                        "summary": "Demo",
                        "usage": "demo",
                        "help": "Demo",
                    },
                    "setup": {"required": False},
                    "model_provider": {"connection_name": "Demo Connection"},
                }
            )
        )
    monkeypatch.setattr(
        registry, "_discovery", registry.discover(tmp_path, frozenset())
    )
    monkeypatch.setattr(loader, "_loaded", {})
    monkeypatch.setattr(loader, "_contract_failures", {})
    for ext_id in ("alpha-model", "beta-model"):
        # Force package imports to use this test's folder even on a repeat run.
        import sys

        monkeypatch.delitem(sys.modules, loader.package_name(ext_id), raising=False)
        registry.set_enabled(ext_id, True)
    yield
    db.close_connection()


def test_scoped_services_keep_providers_and_connections_independent(providers):
    first, second = resolve_provider("alpha-model"), resolve_provider("beta-model")
    first.provider_action("connect", {})
    second.provider_action("connect", {})
    first.provider_action("connect", {})
    assert {c.api_base_url for c in db.list_connections()} == {
        "extension://alpha-model",
        "extension://beta-model",
    }
    assert len(db.list_connections()) == 2
    registry.set_enabled("alpha-model", False)
    with pytest.raises(ProviderError, match="Enable"):
        resolve_provider("alpha-model")
    assert resolve_provider("beta-model") is second


@pytest.mark.asyncio
async def test_any_declared_provider_drives_agent_and_system_calls(providers):
    provider = resolve_provider("beta-model")
    provider.provider_action("connect", {})
    from core.llm.client import completion
    from core.llm.system_completion import complete_text

    result = await completion(
        "demo-model",
        [{"role": "user", "content": "task"}],
        api_base="extension://beta-model",
        temperature=0.25,
        max_tokens=123,
    )
    assert result.content == "demo:task"
    assert (
        provider.last_request.temperature == 0.25
        and provider.last_request.max_tokens == 123
    )
    assert complete_text([{"role": "user", "content": "route"}]) == "demo:route"


def test_generic_api_discovers_and_dispatches_another_provider(providers):
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    with TestClient(
        app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}
    ) as client:
        listed = client.get("/api/extensions").json()
        assert all(
            item["model_provider"]["connection_name"] == "Demo Connection"
            for item in listed
        )
        status = client.get("/api/extensions/alpha-model/provider").json()
        assert status["actions"][0]["label"] == "Connect demo"
        assert (
            client.post(
                "/api/extensions/alpha-model/provider/actions",
                json={"action": "connect"},
            ).status_code
            == 200
        )
        connection = client.get("/api/connections").json()[0]
        assert connection["managed_by"] == "alpha-model"
        cid = connection["id"]
        assert (
            client.patch("/api/connections/" + cid, json={"model": "other"}).status_code
            == 409
        )
        assert client.post("/api/connections/" + cid + "/duplicate").status_code == 409
        registry.set_enabled("alpha-model", False)
        assert client.get("/api/extensions/alpha-model/provider").status_code == 409


def test_missing_folder_fails_closed_without_loading_vendor_code(
    providers, monkeypatch
):
    resolve_provider("alpha-model").provider_action("connect", {})
    monkeypatch.setattr(registry, "_discovery", registry.Discovery(entries=()))
    with pytest.raises(ProviderError, match="missing"):
        resolve_provider("alpha-model")
    assert len(db.list_connections()) == 1


def test_loader_refuses_an_incomplete_declared_capability(providers):
    entry = registry.get_discovery().get("alpha-model")
    assert "model-provider contract" in loader._broken_contract(
        entry.manifest, object()
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "extension://a/b",
        "extension://alpha-model?token=x",
        "extension://user:password@alpha-model",
    ],
)
def test_invalid_extension_endpoints_are_rejected(endpoint):
    with pytest.raises(ProviderError):
        provider_id(endpoint)


def test_vendor_code_is_confined_to_its_extension():
    root = Path(__file__).resolve().parents[1]
    for folder in ("core", "api", "ui/static/js"):
        for path in (root / folder).rglob("*"):
            if path.suffix in {".py", ".js"}:
                assert "chatgpt" not in path.read_text().lower(), path
    for path in (root / "extensions/chatgpt").glob("*.py"):
        import ast

        module = ast.parse(path.read_text())
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                assert all(alias.name != "db" for alias in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("db", "core.llm", "core.config")
                ), path
