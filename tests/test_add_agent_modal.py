"""Add agent modal: rename strings, browse states, pack hydrate, blank path."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HERE = Path(__file__).resolve().parent

HARNESS_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "format.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    JS / "context" / "agent-api.js",
    JS / "context" / "agent-form-hydrate.js",
    JS / "context" / "agent-form-catalog.js",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _harness() -> dict:
    args = ["node", str(HERE / "js_add_agent_harness.cjs")] + [str(path) for path in HARNESS_MODULES]
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_hire_and_onboard_copy_is_gone_from_add_agent_flow() -> None:
    """The create flow is Add agent. Hire / Onboard are not operator-facing here."""
    surfaces = [
        JS / "context" / "agent-edit.js",
        JS / "context" / "agent-form-catalog.js",
        JS / "shell" / "roster.js",
        JS / "context" / "mini-office.js",
        JS / "places" / "office" / "org-view.js",
    ]
    joined = "\n".join(_read(path) for path in surfaces)
    assert "Hire someone" not in joined
    assert "Onboard" not in joined
    assert "HIRE_TITLE = 'Add agent'" in _read(JS / "context" / "agent-edit.js")
    assert "'Add agent'" in _read(JS / "shell" / "roster.js")
    catalog = _read(JS / "context" / "agent-form-catalog.js")
    assert "Browse packs" in catalog
    assert "Start blank" in catalog
    assert "Loading packs…" in catalog
    assert "No packs yet. Start blank, or check the catalog repo." in catalog
    assert "Couldn’t load packs. Start blank, or try again." in catalog


def test_browse_load_empty_fail_pick_and_blank() -> None:
    payload = _harness()
    assert payload["renameStrings"] is True
    assert payload["loadingCopy"] is True
    assert payload["emptyCopy"] is True
    assert payload["failCopy"] is True
    assert payload["authorIsBlueLink"] is True
    assert payload["plainAuthorUnlinked"] is True
    assert payload["categories"] is True
    assert payload["hydrated"] is True
    assert payload["operatorFieldsUntouched"] is True
    assert payload["importUsedCatalogApi"] is True
    assert payload["blankClearsPackOnly"] is True
    assert payload["blankDoorHidesBrowse"] is True
    assert payload["fromPackCopy"] is True
    assert payload["subtitleUnderTitle"] is True
    assert payload["createFooterUnchanged"] is True


def test_import_client_never_sends_agent_id() -> None:
    api = _read(JS / "context" / "agent-api.js")
    import_fn = api.split("async function importPack(body) {", 1)[1].split("return {", 1)[0]
    assert "delete payload.agent_id" in import_fn
    assert "/api/agent-packs/import" in import_fn
    assert "fetchCatalog" in api
    assert "/api/agent-packs" in api


def test_catalog_pin_is_dcc94ca() -> None:
    github = _read(ROOT / "core" / "agent_pack" / "github.py")
    assert 'DEFAULT_CATALOG_PIN = "dcc94ca"' in github
    settings = _read(ROOT / "db" / "settings.py")
    assert '("agent_pack_catalog_pin", "dcc94ca", "agent_packs")' in settings
