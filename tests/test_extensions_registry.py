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
    assert json.loads(db.get_settings("extensions")[0].value) == ["demo-ext"]
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
