"""The host supports independently installed providers without vendor branches."""

from __future__ import annotations
import json
import os
from datetime import datetime, timedelta, timezone
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
from datetime import datetime, timezone
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
            actions=[{"key": "connect", "label": "Connect demo"}],
            accounts=[{"label": "demo@example.com", "active": True, "signed_in": True}],
            models=[{"key": "demo-model", "name": "Demo model", "enabled": True, "thinking": ["low", "high"],
                     "thinking_default": "low", "images": "supported", "image_policy": "auto",
                     "image_choices": ["auto", "enabled", "disabled"], "note": "A demo."}],
            models_updated_at=datetime(2026, 10, 29, 9, 30, tzinfo=timezone.utc),
            model_actions=[{"key": "refresh", "label": "Refresh demo"}])
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
                    "summary": "Demo",
                    "author": "Tests",
                    "category": "testing",
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
        connection_id="test-connection",
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


def test_a_structured_status_round_trips_through_the_api(providers):
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    with TestClient(
        app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}
    ) as client:
        status = client.get("/api/extensions/alpha-model/provider").json()
    # ISO 8601 in UTC, which the UI's date formatter reads.
    assert status["models_updated_at"] == "2026-10-29T09:30:00Z"
    assert datetime.fromisoformat(status["models_updated_at"]) == datetime(
        2026, 10, 29, 9, 30, tzinfo=timezone.utc
    )
    assert status["accounts"] == [
        {"label": "demo@example.com", "active": True, "signed_in": True, "action": None}
    ]
    assert status["models"] == [
        {
            "key": "demo-model", "name": "Demo model", "enabled": True, "thinking": ["low", "high"],
            "thinking_default": "low", "images": "supported", "image_policy": "auto",
            "image_choices": ["auto", "enabled", "disabled"], "note": "A demo.",
        }
    ]
    assert status["actions"] == [
        {"key": "connect", "label": "Connect demo", "values": {}, "primary": False}
    ]
    assert status["model_actions"] == [
        {"key": "refresh", "label": "Refresh demo", "values": {}, "primary": False}
    ]
    assert "choices" not in status


def test_the_status_contract_refuses_an_ambiguous_shape():
    from pydantic import ValidationError
    from core.extensions.model_provider import ProviderModel, ProviderStatus
    model = {"key": "m", "name": "M", "enabled": True, "images": "unknown", "image_policy": "auto"}
    account = {"label": "a@example.com", "active": True, "signed_in": True}
    primary = {"key": "a", "label": "A", "primary": True}
    with pytest.raises(ValidationError, match="model keys must be unique"):
        ProviderStatus(message="x", models=[model, model])
    with pytest.raises(ValidationError, match="at most one account may be active"):
        ProviderStatus(message="x", accounts=[account, {**account, "label": "b@example.com"}])
    with pytest.raises(ValidationError, match="at most one action may be primary"):
        ProviderStatus(message="x", actions=[primary, {**primary, "key": "b"}])
    with pytest.raises(ValidationError, match="not one of image_choices"):
        ProviderModel(**{**model, "image_policy": "enabled", "image_choices": ["auto", "disabled"]})
    with pytest.raises(ValidationError, match="must not repeat"):
        ProviderModel(**{**model, "image_choices": ["auto", "auto"]})
    with pytest.raises(ValidationError, match="ultra"):
        ProviderModel(**{**model, "thinking": ["ultra"]})
    # Whether a model is on has no default: a provider must say.
    with pytest.raises(ValidationError, match=r"enabled\n  Field required"):
        ProviderModel(**{key: value for key, value in model.items() if key != "enabled"})
    with pytest.raises(ValidationError, match="enabled"):
        ProviderModel(**{**model, "enabled": None})
    with pytest.raises(ValidationError, match="timezone info"):
        ProviderStatus(message="x", models_updated_at=datetime(2026, 10, 29, 9, 30))
    with pytest.raises(ValidationError, match="must be in UTC"):
        ProviderStatus(
            message="x",
            models_updated_at=datetime(2026, 10, 29, 9, 30, tzinfo=timezone(timedelta(hours=2))),
        )
    # Empty choices mean read-only, whatever the policy in effect.
    assert ProviderModel(**{**model, "image_policy": "enabled"}).image_choices == []
    # One of each is fine.
    ProviderStatus(
        message="x",
        models=[model, {**model, "key": "n"}],
        accounts=[account, {**account, "label": "b@example.com", "active": False}],
        actions=[primary, {"key": "b", "label": "B"}],
    )


def test_the_flat_choice_contract_is_gone():
    from core.extensions import model_provider
    assert not hasattr(model_provider, "ProviderChoice")
    assert not hasattr(model_provider, "ProviderOption")
    assert model_provider.IMAGE_POLICY_ACTION == "image_policy"
    assert model_provider.MODEL_ENABLED_ACTION == "model_enabled"


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


def test_managed_model_refuses_a_level_key_the_host_does_not_know():
    from pydantic import ValidationError
    from core.extensions.model_provider import ManagedModel
    with pytest.raises(ValidationError, match="unknown level 'ultra'"):
        ManagedModel(key="k", name="K", model="k", thinking_levels={"ultra": {"effort": "ultra"}})
    with pytest.raises(ValidationError, match="non-empty JSON object"):
        ManagedModel(key="k", name="K", model="k", thinking_levels={"high": {}})
    assert ManagedModel(key="k", name="K", model="k", thinking_levels=None).thinking_levels == {}
    assert ManagedModel(key="k", name="K", model="k").thinking_levels == {}


def test_unrelated_providers_reconcile_a_valid_level_map(providers):
    from core.extensions.model_provider import ManagedModel, services_for
    levels = {"off": {"think": False}, "high": {"think": True, "budget": 8}}
    published = {}
    for ext_id in ("alpha-model", "beta-model"):
        services = services_for(registry.get_discovery().get(ext_id))
        ids = services.reconcile_connections(
            [ManagedModel(key="demo", name="Demo", model="demo-model", thinking_levels=levels)]
        )
        published[ext_id] = ids["demo"]
    assert len(set(published.values())) == 2
    for ext_id, connection_id in published.items():
        connection = db.get_connection_by_id(connection_id)
        assert connection.api_base_url == f"extension://{ext_id}"
        assert connection.thinking_levels == levels


def test_unrelated_legacy_provider_keeps_global_images(providers):
    from db.managed_connections import image_capability, routed_images, agent_images
    from db.model_capabilities import set_supports_images
    first = resolve_provider('alpha-model')
    first.provider_action('connect', {})
    connection = next(c for c in db.list_connections() if c.api_base_url == 'extension://alpha-model')
    set_supports_images(connection.model, True)
    assert image_capability(connection)
    assert routed_images(connection.model, connection.api_base_url)
    from types import SimpleNamespace
    assert agent_images(SimpleNamespace(connection_id=connection.id))
    set_supports_images(connection.model, False)
    assert not image_capability(connection)
    set_supports_images(connection.model, True)
    registry.set_enabled('alpha-model', False)
    assert not image_capability(connection)


def _publish(ext_id, *entries):
    """Reconcile ``(key, available)`` pairs for one provider; returns key → connection id."""
    from core.extensions.model_provider import ManagedModel, services_for
    services = services_for(registry.get_discovery().get(ext_id))
    return services.reconcile_connections([
        ManagedModel(key=key, name=key.title(), model=key, available=on,
                     reason=None if on else "Turned off.")
        for key, on in entries
    ])


def test_models_in_use_names_the_agents_then_system_ai_per_managed_key(providers):
    from core.extensions.model_provider import services_for
    ids = _publish("alpha-model", ("kept", True), ("idle", True), ("off", False))
    other = _publish("beta-model", ("kept", True))
    db.create_agent("Ada", connection_id=ids["kept"])
    db.create_agent("Grace", connection_id=ids["kept"])
    db.create_agent("Linus", connection_id=ids["off"])
    # Another provider's connection is not this provider's to report.
    db.create_agent("Ken", connection_id=other["kept"])
    db.set_setting("system_ai_connection", ids["kept"], "llm")
    config.reload()
    services = services_for(registry.get_discovery().get("alpha-model"))
    assert services.models_in_use() == {"kept": ["Ada", "Grace", "System AI"], "off": ["Linus"]}
    # A key that has left the catalog is still reported while it is used.
    _publish("alpha-model", ("idle", True))
    assert services.models_in_use() == {"kept": ["Ada", "Grace", "System AI"], "off": ["Linus"]}
    beta = services_for(registry.get_discovery().get("beta-model"))
    assert beta.models_in_use() == {"kept": ["Ken"]}


def test_connection_list_hides_an_unavailable_connection_nobody_uses(providers):
    ids = _publish("alpha-model", ("on", True), ("off-idle", False), ("off-agent", False), ("off-system", False))
    ordinary = db.create_connection(name="Ordinary", api_base_url="https://example.com/v1", model="m")
    db.create_agent("Ada", connection_id=ids["off-agent"])
    db.set_setting("system_ai_connection", ids["off-system"], "llm")
    config.reload()
    app = FastAPI()
    app.include_router(router)
    install_local_api_auth(app)
    with TestClient(app, headers={LOCAL_API_TOKEN_HEADER: db.ensure_local_api_token()}) as client:
        listed = {row["id"]: row for row in client.get("/api/connections").json()}
        assert set(listed) == {ordinary.id, ids["on"], ids["off-agent"], ids["off-system"]}
        assert [listed[ids[key]]["available"] for key in ("on", "off-agent", "off-system")] == [True, False, False]
        assert listed[ids["off-agent"]]["unavailable_reason"] == "Turned off."
        # One connection is still read by id, listed or not.
        assert client.get("/api/connections/" + ids["off-idle"]).json()["available"] is False


def test_unset_system_ai_skips_unavailable_connections(providers):
    from api.routes.settings import _system_connection_after
    from core.llm.system_completion import first_available_connection, resolve_system_connection
    # Name order: "Demo Connection — Aa" sorts before "Demo Connection — Zz" and "Zed".
    ids = _publish("alpha-model", ("aa", False), ("zz", True))
    ordinary = db.create_connection(name="Zed", api_base_url="https://example.com/v1", model="m")
    config.reload()
    assert [c.id for c in db.list_connections()] == [ids["aa"], ids["zz"], ordinary.id]
    assert first_available_connection().id == ids["zz"]
    assert resolve_system_connection().id == ids["zz"]
    assert _system_connection_after("").id == ids["zz"]
    assert _system_connection_after("gone-id").id == ids["zz"]
    # A saved id is used as stored, available or not.
    assert _system_connection_after(ids["aa"]).id == ids["aa"]
    db.set_setting("system_ai_connection", ids["aa"], "llm")
    config.reload()
    assert resolve_system_connection().id == ids["aa"]
    # Nothing available: no fallback.
    _publish("alpha-model", ("aa", False), ("zz", False))
    db.delete_connection(ordinary.id)
    db.set_setting("system_ai_connection", "", "llm")
    config.reload()
    assert first_available_connection() is None
    assert resolve_system_connection() is None
    assert _system_connection_after("") is None

