"""Extension host: discovery, validation, the enabled set, and the CLI bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import db
from core import config
from core.bm_cli.command_registry import CORE_COMMAND_NAMES, VIRTUAL_CATEGORIES
from core.bm_cli.help_commands import handle_commands, handle_fsearch, handle_learn
from core.bm_cli.runtime import VIRTUAL_COMMANDS, execute_bm_cli
from core.bm_cli.types import CliExecutionContext, ParsedCliCommand
from core.extensions.cli_bridge import extension_handlers
from core.extensions.manifest import ManifestError, load_manifest
from core.extensions.registry import discover, enabled_ids, get_discovery, set_enabled
from core.extensions.setup_runner import OUT_OF_DATE_DETAIL, read_setup_status


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


def _manifest(ext_id: str = "demo-ext", command: str = "demo", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": ext_id,
        "name": "Demo",
        "version": "0.1.0",
        "description": "A test extension.",
        "command": {"name": command, "summary": "Demo things.", "usage": f"{command} go", "help": "Demo help."},
        "setup": {"required": False},
    }
    data.update(overrides)
    return data


def _write_ext(root: Path, folder: str, manifest: dict[str, Any] | None = None, *, raw: str | None = None) -> Path:
    path = root / folder
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(raw if raw is not None else json.dumps(manifest), encoding="utf-8")
    (path / "__init__.py").write_text(
        "class _Ext:\n"
        "    def __init__(self):\n"
        "        self.calls = []\n"
        "        self.shutdowns = 0\n"
        "    def handle(self, ctx, parsed, body):\n"
        "        from core.bm_cli.results import success_result\n"
        "        self.calls.append(parsed.raw)\n"
        "        return success_result(command=parsed.raw, detail='ran', kind='demo', data={}, sections=[('DEMO', ['ran'])], cwd=ctx.cwd)\n"
        "    def setup_status(self):\n"
        "        raise AssertionError('not used')\n"
        "    def run_setup(self, log_path):\n"
        "        raise AssertionError('not used')\n"
        "    def shutdown(self):\n"
        "        self.shutdowns += 1\n"
        "def create(ctx):\n"
        "    return _Ext()\n",
        encoding="utf-8",
    )
    return path


def _ctx() -> CliExecutionContext:
    agent = db.create_agent("Vera", role="Researcher")
    return CliExecutionContext(agent=agent, state=db.get_agent_state(agent.id), cwd="/me")


def _parsed(raw: str) -> ParsedCliCommand:
    tokens = raw.split()
    return ParsedCliCommand(raw=raw, name=tokens[0], args=tuple(tokens[1:]))


def test_a_valid_manifest_is_discovered(tmp_path: Path) -> None:
    _write_ext(tmp_path, "demo-ext", _manifest())
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    entry = found.get("demo-ext")
    assert entry is not None and entry.valid, entry
    assert entry.manifest.command.name == "demo"
    assert load_manifest(tmp_path / "demo-ext" / "manifest.json").name == "Demo"


def test_bad_json_is_listed_invalid_under_its_folder_name(tmp_path: Path) -> None:
    _write_ext(tmp_path, "broken", raw="{ not json")
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("broken")
    assert entry is not None and not entry.valid
    assert "not valid JSON" in entry.invalid_reason
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "broken" / "manifest.json")


def test_an_unknown_manifest_key_is_rejected(tmp_path: Path) -> None:
    _write_ext(tmp_path, "extra", _manifest(ext_id="extra", surprise=True))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("extra")
    assert entry is not None and not entry.valid
    assert "surprise" in entry.invalid_reason


def test_a_command_colliding_with_a_core_command_is_invalid(tmp_path: Path) -> None:
    _write_ext(tmp_path, "shadow", _manifest(ext_id="shadow", command="ls"))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("shadow")
    assert entry is not None and not entry.valid
    assert "collides with a core command" in entry.invalid_reason


def test_a_command_colliding_with_a_core_alias_is_invalid(tmp_path: Path) -> None:
    # "lines" is an alias of rr; the parser would route it to rr, never here.
    _write_ext(tmp_path, "alias", _manifest(ext_id="alias", command="lines"))
    assert not discover(tmp_path, CORE_COMMAND_NAMES).get("alias").valid


def test_two_extensions_with_one_command_are_both_invalid(tmp_path: Path) -> None:
    _write_ext(tmp_path, "one", _manifest(ext_id="one", command="same"))
    _write_ext(tmp_path, "two", _manifest(ext_id="two", command="same"))
    found = discover(tmp_path, CORE_COMMAND_NAMES)
    for ext_id in ("one", "two"):
        entry = found.get(ext_id)
        assert not entry.valid
        assert "collides with another extension" in entry.invalid_reason
    assert extension_handlers(found) == {}


def test_a_missing_python_module_is_invalid_and_named(tmp_path: Path) -> None:
    manifest = _manifest(requires={"python_modules": ["surely_not_installed_module_xyz"]})
    _write_ext(tmp_path, "needs", manifest)
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("demo-ext")
    assert not entry.valid
    assert entry.invalid_reason == "missing dependency surely_not_installed_module_xyz"


def test_the_enabled_set_round_trips_through_the_setting() -> None:
    assert enabled_ids() == frozenset()
    set_enabled("demo-ext", True)
    assert enabled_ids() == frozenset({"demo-ext"})
    stored = next(item for item in db.get_settings("extensions") if item.key == "extensions_enabled")
    assert json.loads(stored.value) == ["demo-ext"]
    set_enabled("demo-ext", False)
    assert enabled_ids() == frozenset()


def test_a_disabled_extension_handler_answers_extension_disabled(tmp_path: Path) -> None:
    _write_ext(tmp_path, "demo-ext", _manifest())
    handler = extension_handlers(discover(tmp_path, CORE_COMMAND_NAMES))["demo"]
    ctx = _ctx()
    result = handler(ctx, _parsed("demo go"), None)
    assert not result.ok
    assert "EXTENSION_DISABLED: Demo is off (Add → Extensions)" in result.prompt_content

    set_enabled("demo-ext", True)
    ran = handler(ctx, _parsed("demo go"), None)
    assert ran.ok and ran.kind == "demo"


def test_every_result_of_an_extension_command_is_stamped_with_its_id(tmp_path: Path) -> None:
    _write_ext(tmp_path, "demo-ext", _manifest())
    handler = extension_handlers(discover(tmp_path, CORE_COMMAND_NAMES))["demo"]
    ctx = _ctx()

    disabled = handler(ctx, _parsed("demo go"), None)
    assert not disabled.ok and disabled.data["extension_id"] == "demo-ext"
    assert disabled.data["error"].startswith("EXTENSION_DISABLED")  # the error data is kept

    set_enabled("demo-ext", True)
    ran = handler(ctx, _parsed("demo go"), None)
    assert ran.ok and ran.data == {"extension_id": "demo-ext"}


def test_a_load_failure_is_stamped_too(tmp_path: Path) -> None:
    _write_ext(tmp_path, "no-live2", _manifest(ext_id="no-live2", command="nolive2", live_view=True))
    set_enabled("no-live2", True)
    result = extension_handlers(discover(tmp_path, CORE_COMMAND_NAMES))["nolive2"](_ctx(), _parsed("nolive2 go"), None)
    assert "EXTENSION_LOAD_FAILED" in result.prompt_content
    assert result.data["extension_id"] == "no-live2"


def test_core_commands_are_not_stamped() -> None:
    agent = db.create_agent("Iris", role="Researcher")
    result = execute_bm_cli(agent, db.get_agent_state(agent.id), "pwd")
    assert "extension_id" not in (result.data or {})

def test_browser_vision_is_a_virtual_command_answering_disabled_by_default() -> None:
    entry = get_discovery().get("browser-vision")
    assert entry is not None and entry.valid, entry
    assert "bv" in VIRTUAL_COMMANDS
    agent = db.create_agent("Iris", role="Researcher")
    result = execute_bm_cli(agent, db.get_agent_state(agent.id), "bv open example.com")
    assert not result.ok
    assert result.executor == "virtual"
    assert "EXTENSION_DISABLED: Browser Vision is off (Add → Extensions)" in result.prompt_content


def test_help_hides_a_disabled_extensions_command_and_shows_it_once_enabled() -> None:
    assert "extensions" in VIRTUAL_CATEGORIES
    ctx = _ctx()

    categories = handle_commands(ctx, _parsed("categories"), None).prompt_content
    search = handle_fsearch(ctx, _parsed("fsearch bv"), None).prompt_content
    learn = handle_learn(ctx, _parsed("learn bv"), None)
    assert "extensions —" not in categories
    assert "bv open" not in search
    assert learn.data["type"] == "not_found"

    set_enabled("browser-vision", True)
    categories = handle_commands(ctx, _parsed("categories"), None).prompt_content
    search = handle_fsearch(ctx, _parsed("fsearch bv"), None).prompt_content
    learn = handle_learn(ctx, _parsed("learn bv"), None).prompt_content
    assert "extensions — Commands added by enabled extensions (bv)" in categories
    assert "bv open <url>" in search
    assert "Command:   bv" in learn and "Category:  extensions" in learn


def test_a_manifest_declaring_live_view_without_the_method_is_invalid_at_load(tmp_path: Path) -> None:
    from core.extensions.loader import ExtensionLoadError, contract_failure, load_extension

    _write_ext(tmp_path, "no-live", _manifest(ext_id="no-live", command="nolive", live_view=True))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("no-live")
    assert entry.valid and entry.manifest.live_view is True  # discovery does not import (D10)

    with pytest.raises(ExtensionLoadError, match="declares live_view but the extension has no live_view"):
        load_extension(entry)
    assert contract_failure("no-live") == "manifest declares live_view but the extension has no live_view() method"
    # It stays invalid for this process; the CLI bridge reports the load failure.
    with pytest.raises(ExtensionLoadError):
        load_extension(entry)
    set_enabled("no-live", True)
    result = extension_handlers(discover(tmp_path, CORE_COMMAND_NAMES))["nolive"](_ctx(), _parsed("nolive go"), None)
    assert "EXTENSION_LOAD_FAILED: manifest declares live_view" in result.prompt_content


# ─── agent_config / agent_view manifest blocks ───


def _agent_config(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "label": "Demo account",
        "help": "How to get the values.",
        "fields": [
            {"key": "token", "label": "Token", "kind": "secret"},
            {"key": "address", "label": "Address", "kind": "email", "summary": True},
            {"key": "note", "label": "Note", "kind": "text", "required": False},
        ],
    }
    block.update(overrides)
    return block


def test_a_manifest_with_agent_config_and_agent_view_is_valid(tmp_path: Path) -> None:
    _write_ext(tmp_path, "per-agent", _manifest(
        ext_id="per-agent", command="peragent", agent_config=_agent_config(),
        agent_view={"label": "Open it", "views": [{"key": "inbox", "label": "Inbox"}]},
    ))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("per-agent")
    assert entry.valid, entry.invalid_reason
    spec = entry.manifest.agent_config
    assert [f.key for f in spec.fields] == ["token", "address", "note"]
    assert spec.fields[0].required is True and spec.fields[2].required is False
    assert entry.manifest.agent_view.label == "Open it" and entry.manifest.agent_view.requires_config is True


@pytest.mark.parametrize("fields, fragment", [
    ([{"key": "a", "label": "A", "kind": "text"}, {"key": "a", "label": "B", "kind": "text"}], "duplicate field keys: a"),
    ([{"key": "a", "label": "A", "kind": "text", "summary": True},
      {"key": "b", "label": "B", "kind": "email", "summary": True}], "at most one field may set summary"),
    ([{"key": "Bad-Key", "label": "A", "kind": "text"}], "key"),
    ([{"key": "a", "label": "A", "kind": "password"}], "kind"),
    ([], "fields"),
])
def test_bad_agent_config_fields_are_invalid(tmp_path: Path, fields: list, fragment: str) -> None:
    _write_ext(tmp_path, "bad-config", _manifest(ext_id="bad-config", command="badconfig", agent_config=_agent_config(fields=fields)))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("bad-config")
    assert not entry.valid and fragment in entry.invalid_reason, entry.invalid_reason


def test_an_agent_view_that_needs_config_without_agent_config_is_invalid(tmp_path: Path) -> None:
    _write_ext(tmp_path, "view-only", _manifest(
        ext_id="view-only", command="viewonly", agent_view={"label": "Open", "views": [{"key": "all", "label": "All"}]},
    ))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("view-only")
    assert not entry.valid and "agent_view.requires_config needs an agent_config block" in entry.invalid_reason
    _write_ext(tmp_path, "view-free", _manifest(
        ext_id="view-free", command="viewfree",
        agent_view={"label": "Open", "views": [{"key": "all", "label": "All"}], "requires_config": False},
    ))
    assert discover(tmp_path, CORE_COMMAND_NAMES).get("view-free").valid


@pytest.mark.parametrize("block, method", [
    ({"agent_config": "config"}, "verify_agent_config"),
    ({"agent_view": "view"}, "agent_view"),
])
def test_a_declared_per_agent_capability_without_its_protocol_is_a_contract_failure(
    tmp_path: Path, block: dict[str, str], method: str,
) -> None:
    from core.extensions.loader import ExtensionLoadError, contract_failure, load_extension

    ext_id = f"no-{block[next(iter(block))]}"
    extra: dict[str, Any] = {"agent_config": _agent_config()}
    if "agent_view" in block:
        # The config half is implemented; only the view is missing.
        extra["agent_view"] = {"label": "Open", "views": [{"key": "all", "label": "All"}]}
    _write_ext(tmp_path, ext_id, _manifest(ext_id=ext_id, command=ext_id.replace("-", ""), **extra))
    if "agent_view" in block:
        init = tmp_path / ext_id / "__init__.py"
        init.write_text(init.read_text(encoding="utf-8").replace(
            "    def shutdown(self):",
            "    def verify_agent_config(self, values):\n        return 'ok'\n    def shutdown(self):",
        ), encoding="utf-8")
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get(ext_id)
    assert entry.valid  # discovery does not import (D10)
    with pytest.raises(ExtensionLoadError, match=method):
        load_extension(entry)
    assert method in contract_failure(ext_id)


# ─── agent_view views, number fields and wake ───


_NUMBER = {"key": "every", "label": "Every", "kind": "number", "min": 15, "max": 3600, "default": "90", "required": False}


@pytest.mark.parametrize("views, fragment", [
    ([], "views"),
    ([{"key": "a", "label": "A"}, {"key": "a", "label": "B"}], "duplicate view keys: a"),
])
def test_bad_agent_views_are_invalid(tmp_path: Path, views: list, fragment: str) -> None:
    _write_ext(tmp_path, "bad-views", _manifest(
        ext_id="bad-views", command="badviews", agent_config=_agent_config(), agent_view={"label": "Open", "views": views},
    ))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("bad-views")
    assert not entry.valid and fragment in entry.invalid_reason, entry.invalid_reason


@pytest.mark.parametrize("field, fragment", [
    ({**_NUMBER, "default": None}, "needs a default"),
    ({**_NUMBER, "default": "10"}, "outside min/max"),
    ({**_NUMBER, "default": "4000"}, "outside min/max"),
    ({**_NUMBER, "default": "ninety"}, "not a whole number"),
    ({**_NUMBER, "min": 100, "max": 50, "default": "60"}, "min is greater than max"),
    ({"key": "t", "label": "T", "kind": "text", "default": "x"}, "apply only to a number field"),
])
def test_a_number_field_needs_a_default_within_bounds(tmp_path: Path, field: dict, fragment: str) -> None:
    _write_ext(tmp_path, "bad-number", _manifest(
        ext_id="bad-number", command="badnumber", agent_config=_agent_config(fields=[field]),
    ))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("bad-number")
    assert not entry.valid and fragment in entry.invalid_reason, entry.invalid_reason


def test_wake_needs_agent_config(tmp_path: Path) -> None:
    _write_ext(tmp_path, "wake-bare", _manifest(ext_id="wake-bare", command="wakebare", wake={"interval_field": "every"}))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("wake-bare")
    assert not entry.valid and "wake needs an agent_config block" in entry.invalid_reason


@pytest.mark.parametrize("interval_field", ["note", "missing"])
def test_wake_interval_field_must_name_a_number_field(tmp_path: Path, interval_field: str) -> None:
    _write_ext(tmp_path, "wake-bad", _manifest(
        ext_id="wake-bad", command="wakebad",
        agent_config=_agent_config(fields=[*_agent_config()["fields"], _NUMBER]),
        wake={"interval_field": interval_field},
    ))
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("wake-bad")
    assert not entry.valid and "must name a number field of agent_config" in entry.invalid_reason


def test_wake_without_its_protocol_is_a_contract_failure(tmp_path: Path) -> None:
    from core.extensions.loader import ExtensionLoadError, contract_failure, load_extension

    _write_ext(tmp_path, "no-wake", _manifest(
        ext_id="no-wake", command="nowake",
        agent_config=_agent_config(fields=[*_agent_config()["fields"], _NUMBER]),
        wake={"interval_field": "every"},
    ))
    init = tmp_path / "no-wake" / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8").replace(
        "    def shutdown(self):",
        "    def verify_agent_config(self, values):\n        return 'ok'\n    def shutdown(self):",
    ), encoding="utf-8")
    entry = discover(tmp_path, CORE_COMMAND_NAMES).get("no-wake")
    assert entry.valid and entry.manifest.wake.interval_field == "every"
    with pytest.raises(ExtensionLoadError, match="declares wake"):
        load_extension(entry)
    assert "poll_wake" in contract_failure("no-wake")


def test_agent_config_value_reads_a_declared_default_for_an_absent_key(tmp_path: Path) -> None:
    from core.extensions.manifest import agent_config_value

    _write_ext(tmp_path, "defaults", _manifest(
        ext_id="defaults", command="defaults", agent_config=_agent_config(fields=[*_agent_config()["fields"], _NUMBER]),
    ))
    spec = discover(tmp_path, CORE_COMMAND_NAMES).get("defaults").manifest.agent_config
    assert agent_config_value(spec, {"every": "30"}, "every") == "30"
    assert agent_config_value(spec, {}, "every") == "90"
    with pytest.raises(KeyError):
        agent_config_value(spec, {}, "note")
    with pytest.raises(KeyError):
        agent_config_value(spec, {}, "undeclared")


# ─── setup.ready_requires (R35 amendment) ───


def _ready(tmp_path: Path, text: str) -> Path:
    (tmp_path / "ready.json").write_text(text, encoding="utf-8")
    return tmp_path


def test_no_ready_requirements_behave_as_before(tmp_path: Path) -> None:
    assert read_setup_status(tmp_path, required=True, ready_requires={}).state == "missing"
    _ready(tmp_path, "not json at all")
    assert read_setup_status(tmp_path, required=True, ready_requires={}).model_dump() == {"state": "ready", "detail": None}


def test_a_ready_file_meeting_the_requirements_is_ready(tmp_path: Path) -> None:
    _ready(tmp_path, json.dumps({"browser": "153", "browser_kind": "chromium"}))
    status = read_setup_status(tmp_path, required=True, ready_requires={"browser_kind": "chromium"})
    assert status.model_dump() == {"state": "ready", "detail": None}


@pytest.mark.parametrize("text", [
    json.dumps({"browser": "153"}),
    json.dumps({"browser_kind": "chromium-headless-shell"}),
    json.dumps(["browser_kind", "chromium"]),
    "{not json",
])
def test_a_ready_file_missing_or_differing_or_unreadable_is_out_of_date(tmp_path: Path, text: str) -> None:
    _ready(tmp_path, text)
    status = read_setup_status(tmp_path, required=True, ready_requires={"browser_kind": "chromium"})
    assert status.model_dump() == {
        "state": "missing",
        "detail": "Setup needs to run again: the installed version is out of date.",
    }
    assert status.detail == OUT_OF_DATE_DETAIL


def test_a_manifest_ready_requirement_must_be_strings(tmp_path: Path) -> None:
    data = _manifest()
    data["setup"] = {"required": True, "ready_requires": {"browser_kind": 3}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestError, match="ready_requires"):
        load_manifest(path)
