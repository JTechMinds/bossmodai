"""Optional extension capabilities: command-less manifests, the new Protocol checks, the service host,
the shared settings-form rules and the extension-wide settings store."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.bm_cli.command_registry import CORE_COMMAND_NAMES
from core.extensions import loader, prompt_blocks
from core.extensions.cli_bridge import enabled_extension_command_meta, extension_command_meta, extension_handlers
from core.extensions.config_values import ConfigValueError, masked_fields, resolve_values
from core.extensions.loader import ExtensionLoadError, load_extension
from core.extensions.manifest import ConfigSpec, ExtensionManifest, ManifestError, config_value, load_manifest
from core.extensions.registry import discover, set_enabled
from core.extensions.service_host import ExtensionServiceHost


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


_COUNTER = iter(range(1, 100_000))


def _unique_id(prefix: str = "cap") -> str:
    # The loader keeps one imported package per id for the process.
    return f"{prefix}-{next(_COUNTER)}"


def _base(ext_id: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": ext_id, "name": "Cap", "version": "1.0.0", "description": "A test extension.",
        "summary": "s", "author": "Tests", "category": "testing",
    }
    data.update(overrides)
    return data


_COMMAND = {"name": "cap", "summary": "s", "usage": "cap go", "help": "h"}
_SETTINGS = {
    "label": "Cap settings",
    "help": "Choose things.",
    "fields": [
        {"key": "port", "label": "Port", "kind": "number", "required": False, "default": "40000", "min": 1024, "max": 65535},
        {"key": "token", "label": "Token", "kind": "secret", "required": False},
        {"key": "name", "label": "Name", "kind": "text", "required": False},
    ],
}


def _write(root: Path, ext_id: str, manifest: dict[str, Any], body: str) -> Path:
    folder = root / ext_id
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "__init__.py").write_text(body, encoding="utf-8")
    return folder


_EMPTY = "class _Ext:\n    def shutdown(self):\n        pass\ndef create(ctx):\n    return _Ext()\n"


# ─── manifest ───


def test_a_command_less_extension_with_service_panel_and_settings_is_valid() -> None:
    manifest = ExtensionManifest.model_validate(_base("quiet", service=True, panel=True, settings=_SETTINGS))
    assert manifest.command is None
    assert manifest.setup.required is False, "setup defaults to not required"
    assert manifest.service and manifest.panel and manifest.settings is not None


@pytest.mark.parametrize("part", [
    {"prompt": "prompt.md"},
    {"agent_config": {"label": "L", "help": "H", "fields": [{"key": "a", "label": "A", "kind": "text"}]}},
    {"agent_view": {"label": "Open", "views": [{"key": "inbox", "label": "Inbox"}], "requires_config": False}},
    {"wake": {"interval_seconds": 30}},
    {"live_view": {"label": "View", "icon": "monitor"}},
    {"requires": {"image_model": True}},
])
def test_agent_facing_parts_without_a_command_are_refused(part: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="agent-facing parts need a command"):
        ExtensionManifest.model_validate(_base("half-agent", **part))


def test_the_same_parts_are_fine_with_a_command() -> None:
    ExtensionManifest.model_validate(_base("agentish", command=_COMMAND, live_view={"label": "View", "icon": "monitor"}))


def test_service_cannot_be_combined_with_required_setup() -> None:
    with pytest.raises(ValueError, match="service cannot be combined with setup.required"):
        ExtensionManifest.model_validate(_base("bad", service=True, setup={"required": True}))
    ExtensionManifest.model_validate(_base("fine", service=True, setup={"required": False}))


def test_load_manifest_names_the_problem_for_a_half_agent_extension(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_base("half", prompt="x.md")), encoding="utf-8")
    with pytest.raises(ManifestError, match="agent-facing parts need a command: prompt"):
        load_manifest(path)


def test_config_value_reads_a_settings_default_and_refuses_an_undeclared_key() -> None:
    spec = ConfigSpec.model_validate(_SETTINGS)
    assert config_value(spec, {}, "port") == "40000"
    assert config_value(spec, {"port": "41000"}, "port") == "41000"
    with pytest.raises(KeyError):
        config_value(spec, {}, "name")
    with pytest.raises(KeyError):
        config_value(spec, {}, "nope")


# ─── loader contract checks ───

_FULL = '''
class _Ext:
    def __init__(self, ctx):
        self.ctx = ctx
    def handle(self, ctx, parsed, body):
        return None
    def run_setup(self, log_path):
        return None
    async def start_service(self, app):
        return None
    async def stop_service(self):
        return None
    def panel(self):
        return None
    def panel_action(self, key, values):
        return None
    def verify_settings(self, values):
        return "ok"
    def shutdown(self):
        pass
def create(ctx):
    return _Ext(ctx)
'''


def _load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: dict[str, Any], body: str):
    monkeypatch.setattr(loader, "_loaded", {})
    monkeypatch.setattr(loader, "_contract_failures", {})
    _write(tmp_path, manifest["id"], manifest, body)
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get(manifest["id"])
    assert entry is not None and entry.valid, entry
    return entry


@pytest.mark.parametrize("declared, missing_word", [
    ({"command": _COMMAND}, "command"),
    ({"setup": {"required": True}}, "setup.required"),
    ({"service": True}, "service"),
    ({"panel": True}, "panel"),
    ({"settings": _SETTINGS}, "settings"),
])
def test_the_loader_refuses_an_instance_missing_a_declared_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: dict[str, Any], missing_word: str,
) -> None:
    entry = _load(tmp_path, monkeypatch, _base(_unique_id(), **declared), _EMPTY)
    with pytest.raises(ExtensionLoadError, match=f"declares {missing_word}"):
        load_extension(entry)
    assert f"declares {missing_word}" in loader.contract_failure(entry.id)


@pytest.mark.parametrize("declared", [
    {"command": _COMMAND, "setup": {"required": True}, "panel": True, "settings": _SETTINGS},
    {"service": True, "panel": True, "settings": _SETTINGS},
])
def test_the_loader_accepts_an_instance_with_every_declared_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: dict[str, Any],
) -> None:
    entry = _load(tmp_path, monkeypatch, _base(_unique_id(), **declared), _FULL)
    assert load_extension(entry) is not None
    assert loader.contract_failure(entry.id) is None


def test_a_command_less_extension_loads_with_only_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _load(tmp_path, monkeypatch, _base(_unique_id("bare")), _EMPTY)
    assert load_extension(entry) is not None


_CTX_PROBE = '''
CAPTURED = []
class _Ext:
    def __init__(self, ctx):
        CAPTURED.append(ctx)
    def verify_settings(self, values):
        return "ok"
    def shutdown(self):
        pass
def create(ctx):
    return _Ext(ctx)
'''


def test_the_context_reads_settings_with_defaults_and_seals_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ext_id = _unique_id("ctx")
    entry = _load(tmp_path, monkeypatch, _base(ext_id, settings=_SETTINGS), _CTX_PROBE)
    load_extension(entry)
    ctx = loader.import_package(entry).CAPTURED[-1]

    # A field with neither a stored value nor a default raises; one with a default reads as it.
    with pytest.raises(KeyError):
        ctx.read_settings()
    db.set_extension_settings(ext_id, {"name": "x"})
    with pytest.raises(KeyError):
        ctx.read_settings()
    db.set_extension_settings(ext_id, {"token": "t", "name": "x"})
    assert ctx.read_settings() == {"port": "40000", "token": "t", "name": "x"}, "port reads as its declared default"
    db.set_extension_settings(ext_id, {"port": "41000", "token": "s3", "name": "n"})
    assert ctx.read_settings() == {"port": "41000", "token": "s3", "name": "n"}

    sealed = ctx.seal("plain")
    assert sealed != "plain" and sealed.startswith("bm1:")
    assert ctx.unseal(sealed) == "plain"


def test_read_settings_refuses_an_extension_that_declares_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _load(tmp_path, monkeypatch, _base(_unique_id("nos")), _CTX_PROBE)
    load_extension(entry)
    ctx = loader.import_package(entry).CAPTURED[-1]
    with pytest.raises(RuntimeError, match="declares no settings"):
        ctx.read_settings()


def test_a_hand_built_context_fails_loudly_when_it_uses_a_service_the_host_did_not_supply(tmp_path: Path) -> None:
    from core.extensions.contract import ExtensionContext

    manifest = ExtensionManifest.model_validate(_base("handmade"))
    ctx = ExtensionContext(manifest=manifest, data_dir=tmp_path, read_agent_config=lambda agent_id: None)
    with pytest.raises(RuntimeError, match="was not supplied by the host"):
        ctx.seal("x")


# ─── the CLI bridge, discovery and prompts with a command-less entry ───


def test_a_command_less_entry_adds_no_command_and_two_of_them_do_not_collide(tmp_path: Path) -> None:
    _write(tmp_path, "agent-ext", _base("agent-ext", command=_COMMAND), _EMPTY)
    _write(tmp_path, "quiet-a", _base("quiet-a", service=True), _EMPTY)
    _write(tmp_path, "quiet-b", _base("quiet-b", panel=True), _EMPTY)
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    assert all(entry.valid for entry in found.entries), [e.invalid_reason for e in found.entries]

    assert set(extension_handlers(found)) == {"cap"}
    assert set(extension_command_meta(found)) == {"cap"}
    set_enabled("quiet-a", True)
    set_enabled("agent-ext", True)
    assert set(enabled_extension_command_meta(found)) == {"cap"}


def test_a_command_less_extension_is_never_loaded_for_an_agents_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write(tmp_path, "quiet-prompt", _base("quiet-prompt", service=True), _EMPTY)
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    set_enabled("quiet-prompt", True)

    def refuse(entry):
        raise AssertionError("a command-less extension must not be loaded for a prompt")

    monkeypatch.setattr(prompt_blocks, "load_extension", refuse)
    agent = db.create_agent("Vera", role="Researcher")
    assert prompt_blocks._applies(found.get("quiet-prompt"), None, agent) is False
    assert prompt_blocks.render_extension_blocks(agent, found) is None


# ─── the service host ───

_SERVICE = '''
import asyncio
EVENTS = []
class _Ext:
    def __init__(self, ctx):
        self.mode = ctx.manifest.defaults.get("mode")
    async def start_service(self, app):
        EVENTS.append("start")
        await asyncio.sleep(0.02)
        if self.mode == "refuse":
            from core.extensions.contract import ServiceStartError
            raise ServiceStartError("Port 40000 is already in use.")
        if self.mode == "crash":
            raise ZeroDivisionError("secret internal detail")
    async def stop_service(self):
        EVENTS.append("stop")
    def shutdown(self):
        EVENTS.append("shutdown")
def create(ctx):
    return _Ext(ctx)
'''


def _service_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None = None):
    """A host over one service extension, an enabled set the test edits, and the extension's event list."""
    monkeypatch.setattr(loader, "_loaded", {})
    ext_id = _unique_id("svc")
    defaults = {"mode": mode} if mode else {}
    _write(tmp_path, ext_id, _base(ext_id, service=True, defaults=defaults), _SERVICE)
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    enabled: set[str] = set()
    host = ExtensionServiceHost(discovery=lambda: found, enabled=lambda: frozenset(enabled))
    host.attach(object())
    events = loader.import_package(found.get(ext_id)).EVENTS
    return host, ext_id, enabled, events


async def test_reconcile_starts_an_enabled_service_and_stops_it_when_turned_off(tmp_path, monkeypatch) -> None:
    host, ext_id, enabled, events = _service_host(tmp_path, monkeypatch)
    assert await host.reconcile() == {} and events == [], "off: nothing starts"
    enabled.add(ext_id)
    assert await host.reconcile() == {}
    assert host.is_running(ext_id) and events == ["start"]

    enabled.clear()
    await host.reconcile()
    assert not host.is_running(ext_id)
    assert events == ["start", "stop", "shutdown"], "stop_service, then shutdown"
    assert ext_id not in loader.loaded_extensions(), "unloaded so the next start is fresh"


async def test_reconcile_is_idempotent(tmp_path, monkeypatch) -> None:
    host, ext_id, enabled, events = _service_host(tmp_path, monkeypatch)
    enabled.add(ext_id)
    await host.reconcile()
    await host.reconcile()
    assert events == ["start"]


async def test_concurrent_reconciles_start_the_service_once(tmp_path, monkeypatch) -> None:
    host, ext_id, enabled, events = _service_host(tmp_path, monkeypatch)
    enabled.add(ext_id)
    await asyncio.gather(host.reconcile(), host.reconcile(), host.reconcile())
    assert events == ["start"]


async def test_a_refused_start_is_recorded_for_the_card_and_retried_next_time(tmp_path, monkeypatch) -> None:
    host, ext_id, enabled, events = _service_host(tmp_path, monkeypatch, mode="refuse")
    enabled.add(ext_id)
    assert await host.reconcile() == {ext_id: "Port 40000 is already in use."}
    assert host.error(ext_id) == "Port 40000 is already in use." and not host.is_running(ext_id)
    assert events == ["start", "stop", "shutdown"], "a failed start releases what it took"

    await host.reconcile()
    assert events.count("start") == 2, "an enabled service that is not running is tried again"

    enabled.clear()
    await host.reconcile()
    assert host.error(ext_id) is None, "a turned-off extension shows no failure"


async def test_an_unexpected_failure_is_logged_with_its_traceback_and_shown_generically(
    tmp_path, monkeypatch, caplog,
) -> None:
    host, ext_id, enabled, _events = _service_host(tmp_path, monkeypatch, mode="crash")
    enabled.add(ext_id)
    with caplog.at_level(logging.ERROR):
        failed = await host.reconcile()
    assert "secret internal detail" not in failed[ext_id]
    assert "Couldn’t start" in failed[ext_id]
    record = next(r for r in caplog.records if "failed to start its service" in r.getMessage())
    assert record.exc_info is not None and record.exc_info[0] is ZeroDivisionError


async def test_restart_stops_then_starts_with_a_fresh_instance(tmp_path, monkeypatch) -> None:
    host, ext_id, enabled, events = _service_host(tmp_path, monkeypatch)
    assert await host.restart(ext_id) is None and events == [], "not enabled: nothing to restart"
    enabled.add(ext_id)
    await host.reconcile()
    first = loader.loaded_extensions()[ext_id]
    assert await host.restart(ext_id) is None
    assert events == ["start", "stop", "shutdown", "start"]
    assert loader.loaded_extensions()[ext_id] is not first


async def test_restart_returns_the_start_failure(tmp_path, monkeypatch) -> None:
    host, ext_id, enabled, _events = _service_host(tmp_path, monkeypatch, mode="refuse")
    enabled.add(ext_id)
    await host.reconcile()
    assert await host.restart(ext_id) == "Port 40000 is already in use."


async def test_stop_all_stops_every_running_service(tmp_path, monkeypatch) -> None:
    host, ext_id, enabled, events = _service_host(tmp_path, monkeypatch)
    enabled.add(ext_id)
    await host.reconcile()
    await host.stop_all()
    assert not host.is_running(ext_id) and events[-2:] == ["stop", "shutdown"]


async def test_starting_without_an_attached_app_is_an_error_not_a_silent_skip(tmp_path, monkeypatch) -> None:
    host, ext_id, enabled, _events = _service_host(tmp_path, monkeypatch)
    host._app = None
    assert await host.reconcile() == {}, "nothing needs starting while the extension is off"
    enabled.add(ext_id)
    with pytest.raises(RuntimeError, match="attach"):
        await host.reconcile()


# ─── the shared settings-form rules (parity with the agent behaviour they were lifted from) ───

_AGENT_SPEC = ConfigSpec.model_validate({
    "label": "Mailbox", "help": "h",
    "fields": [
        {"key": "tenant", "label": "Tenant ID", "kind": "text"},
        {"key": "secret", "label": "Client secret", "kind": "secret"},
        {"key": "mailbox", "label": "Mailbox address", "kind": "email"},
        {"key": "every", "label": "Check every", "kind": "number", "required": False, "min": 15, "max": 3600, "default": "90"},
    ],
})
_GOOD = {"tenant": " t1 ", "secret": "s3", "mailbox": "a@b.co", "every": ""}


def test_resolve_values_trims_and_fills_a_blank_number_with_its_default() -> None:
    assert resolve_values(_AGENT_SPEC, _GOOD, None) == {"tenant": "t1", "secret": "s3", "mailbox": "a@b.co", "every": "90"}


@pytest.mark.parametrize("submitted, stored, message", [
    ({**_GOOD, "extra": "x"}, None, "Unknown field: extra"),
    ({**_GOOD, "tenant": ""}, None, "Tenant ID is required."),
    ({**_GOOD, "secret": ""}, None, "Client secret is required."),
    ({**_GOOD, "mailbox": "nope"}, None, "Mailbox address is not an email address."),
    ({**_GOOD, "every": "abc"}, None, "Check every must be a whole number from 15 to 3600."),
    ({**_GOOD, "every": "5"}, None, "Check every must be a whole number from 15 to 3600."),
    ({**_GOOD, "every": "9999"}, None, "Check every must be a whole number from 15 to 3600."),
])
def test_resolve_values_refuses_bad_input_with_the_same_sentences(submitted, stored, message) -> None:
    with pytest.raises(ConfigValueError) as caught:
        resolve_values(_AGENT_SPEC, submitted, stored)
    assert str(caught.value) == message


def test_a_blank_secret_keeps_the_stored_one() -> None:
    stored = {"tenant": "t0", "secret": "kept", "mailbox": "a@b.co", "every": "30"}
    resolved = resolve_values(_AGENT_SPEC, {**_GOOD, "secret": ""}, stored)
    assert resolved["secret"] == "kept"


def test_masked_fields_hide_secrets_and_read_old_configs_as_defaults() -> None:
    assert masked_fields(_AGENT_SPEC, None) == [
        {"key": "tenant", "label": "Tenant ID", "kind": "text", "required": True, "value": ""},
        {"key": "secret", "label": "Client secret", "kind": "secret", "required": True, "set": False},
        {"key": "mailbox", "label": "Mailbox address", "kind": "email", "required": True, "value": ""},
        {"key": "every", "label": "Check every", "kind": "number", "required": False,
         "min": 15, "max": 3600, "default": "90", "value": "90"},
    ]
    stored = {"tenant": "t1", "secret": "hunter2", "mailbox": "a@b.co"}
    fields = {item["key"]: item for item in masked_fields(_AGENT_SPEC, stored)}
    assert fields["secret"]["set"] is True and "value" not in fields["secret"]
    assert "hunter2" not in json.dumps(fields)
    assert fields["every"]["value"] == "90", "a config saved before the field existed reads as its default"


# ─── the extension-wide settings store ───


def test_extension_settings_round_trip_sealed_at_rest() -> None:
    assert db.get_extension_settings("demo") is None
    db.set_extension_settings("demo", {"port": "40001", "token": "hunter2"})
    assert db.get_extension_settings("demo") == {"port": "40001", "token": "hunter2"}
    from db.crud import query_one

    raw = query_one("SELECT config FROM extension_settings WHERE extension_id = $1", ["demo"])["config"]
    assert raw.startswith("bm1:") and "hunter2" not in raw

    db.set_extension_settings("demo", {"port": "40002"})
    assert db.get_extension_settings("demo") == {"port": "40002"}, "a save replaces the whole object"
    assert db.delete_extension_settings("demo") is True
    assert db.delete_extension_settings("demo") is False
    assert db.get_extension_settings("demo") is None


def test_a_corrupt_settings_row_is_surfaced() -> None:
    from db.crud import execute
    from db.secret_store import encrypt_secret

    execute(
        "INSERT INTO extension_settings (extension_id, config) VALUES ($1, $2)",
        ["broken", encrypt_secret(json.dumps({"port": 1}))],
    )
    with pytest.raises(ValueError, match="not an object of strings"):
        db.get_extension_settings("broken")
