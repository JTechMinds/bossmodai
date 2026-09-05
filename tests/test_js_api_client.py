"""HA-STRUCT-P1-08 — shared JS API client; no leftover raw /api fetch."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HTML = ROOT / "ui" / "templates" / "index.html"

APP_JS_FILES = [
    path
    for path in sorted(JS.rglob("*.js"))
    if "vendor" not in path.parts and path.name not in {"api-auth.js", "api-client.js"}
]

# Acceptance surfaces from the backlog: pause, chat, files, settings, simulator.
CRITICAL_CALL_SITES = {
    "app.js": [
        "apiFetch('/api/runtime/state'",
    ],
    "agent-context.js": [
        "apiFetch(`/api/agents/${selectedAgent.id}/activate`",
        "apiFetch(`/api/agents/${agentId}/messages?limit=50`",
    ],
    "company-files.js": [
        "apiFetch(`/api/company/files?path=",
        "apiFetch('/api/company/files/open-folder'",
    ],
    "settings-connections.js": [
        "apiFetch('/api/connections')",
        "apiFetch('/api/connections/test'",
    ],
    "cli-policy-simulator.js": [
        "apiFetch('/api/cli-policy/simulator/execute'",
    ],
}

RAW_FETCH_API = re.compile(
    r"""fetch\s*\(\s*(['"`])/api"""
)
RAW_FETCH_CALL = re.compile(r"(?<![\w.])fetch\s*\(")


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def _script_sources() -> list[str]:
    return re.findall(r"static_url\('([^']+)'\)", HTML.read_text(encoding="utf-8"))


def test_api_client_defines_apifetch_and_delegates_to_window_fetch() -> None:
    source = _read("api-client.js")
    assert "function apiFetch(" in source
    assert "function apiFetchOk(" in source
    assert "function formatApiError(" in source
    assert "function apiFetchBlobUrl(" in source
    assert "window.apiFetch = apiFetch" in source
    assert "window.apiFetchOk = apiFetchOk" in source
    assert "window.BossModApi" in source
    assert "return window.fetch(input, withAuthHeaders(input, init))" in source
    assert "X-BossMod-Token" in source
    assert "BOSSMOD_API_TOKEN" in source
    assert "function withAuthHeaders(" in source
    # apiFetch stays non-throwing so existing callers can inspect res.ok.
    assert "function apiFetch(input, init)" in source
    assert "if (!res.ok)" in source


def test_api_auth_token_wrap_still_patches_window_fetch() -> None:
    source = _read("api-auth.js")
    assert "const originalFetch = window.fetch.bind(window)" in source
    assert "window.fetch = function bossmodFetch(input, init)" in source
    assert "headers.set(TOKEN_HEADER, token)" in source
    assert "X-BossMod-Token" in source
    assert "window.WebSocket = function BossModWebSocket" in source
    assert "searchParams.set('token', token)" in source


def test_index_loads_api_client_after_auth_and_before_app() -> None:
    sources = _script_sources()
    assert "js/api-auth.js" in sources
    assert "js/api-client.js" in sources
    assert sources.index("js/api-auth.js") < sources.index("js/api-client.js")
    assert sources.index("js/api-client.js") < sources.index("js/utils.js")
    assert sources.index("js/api-client.js") < sources.index("js/app.js")
    assert sources.index("js/api-client.js") < sources.index("js/agent-context.js")
    assert sources.index("js/api-client.js") < sources.index("js/company-files.js")
    assert sources.index("js/api-client.js") < sources.index("js/cli-policy-simulator.js")
    assert sources.index("js/api-client.js") < sources.index("js/settings-connections.js")


def test_no_raw_fetch_api_outside_helpers() -> None:
    """rg \"fetch('/api\" ui/static/js only hits helper comments if anywhere."""
    hits: list[str] = []
    for path in JS.rglob("*.js"):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in RAW_FETCH_API.finditer(text):
            hits.append(f"{path.relative_to(JS)}:{text[: match.start()].count(chr(10)) + 1}")
    assert hits == []


def test_app_js_has_no_raw_fetch_calls() -> None:
    leftovers: list[str] = []
    for path in APP_JS_FILES:
        text = path.read_text(encoding="utf-8")
        assert "XMLHttpRequest" not in text, path.name
        if RAW_FETCH_CALL.search(text):
            leftovers.append(path.name)
        if "/api/" in text:
            assert "apiFetch(" in text, path.name
    assert leftovers == []


def test_critical_pause_chat_files_settings_simulator_use_api_fetch() -> None:
    for name, needles in CRITICAL_CALL_SITES.items():
        source = _read(name)
        for needle in needles:
            assert needle in source, f"{name} missing {needle}"


def test_api_fetch_attaches_token_and_still_calls_wrapped_fetch() -> None:
    """Behavioral check: apiFetch sets the token and goes through window.fetch."""
    harness = Path(__file__).resolve().parent / "js_api_client_harness.cjs"
    result = subprocess.run(
        [
            "node",
            str(harness),
            str(JS / "api-auth.js"),
            str(JS / "api-client.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"ok": True, "calls": 3, "wrapped": True}


def test_api_fetch_ok_throws_on_http_error_with_parsed_detail() -> None:
    """apiFetch returns 4xx; apiFetchOk throws so save UIs cannot flash success."""
    harness = Path(__file__).resolve().parent / "js_api_fetch_ok_harness.cjs"
    result = subprocess.run(
        [
            "node",
            str(harness),
            str(JS / "api-auth.js"),
            str(JS / "api-client.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["apiFetchDoesNotThrowOn400"] is True
    assert payload["stringDetail"] == "Host workspace root must be an absolute path"
    assert payload["listDetail"] == "Field required"
    assert payload["emptyDetail"] == "Request failed (500)"
    assert payload["formatted"] == "Host workspace root must be an absolute path"


# Settings / CLI mutating saves that previously ignored res.ok (false-green).
_SAVE_OK_SITES = {
    "settings-connections.js": [
        "apiFetchOk(`/api/connections/${conn.id}`",
        "apiFetchOk('/api/connections'",
        "apiFetchOk(`/api/connections/${btn.dataset.deleteConn}`",
    ],
    "cli-policy-section.js": [
        "apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(newVal)}&category=cli_policy`",
        "apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=cli_policy`",
        "apiFetchOk('/api/cli-policy/rules'",
        "applySettingSaveResult(card, false",
    ],
    "settings-system.js": [
        "apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=${encodeURIComponent(category)}`",
    ],
    "settings-advanced.js": [
        "apiFetchOk(`/api/settings/diagnostics_enabled?value=${newValue}&category=advanced`",
        "apiFetchOk(`/api/settings/diagnostics_retention_limit?value=${encodeURIComponent(value)}&category=advanced`",
        "apiFetchOk(`/api/settings/cli_max_read_lines?value=${encodeURIComponent(value)}&category=advanced`",
        "apiFetchOk(`/api/settings/desktop_open_folder_handler?value=${encodeURIComponent(resolvedValue)}&category=advanced`",
    ],
}

_UNGUARDED_MUTATING_FETCH = re.compile(
    r"await apiFetch(?!Ok)\("
    r"(?:[^;]|\n){0,500}?"
    r"method:\s*'(?:PUT|POST|PATCH|DELETE)'",
    re.S,
)

# These already inspect res.ok before success UI; leave the explicit check.
_ALLOWED_UNGUARDED_MUTATING = {
    "settings-advanced.js": {
        "await apiFetch('/api/agents'",
        "await apiFetch('/api/settings/reseed-application'",
    },
    # Test-connection already branches on resp.ok / result.ok before any success UI.
    "settings-connections.js": {
        "await apiFetch('/api/connections/test'",
    },
}


def test_settings_cli_saves_use_apifetch_ok() -> None:
    for name, needles in _SAVE_OK_SITES.items():
        source = _read(name)
        for needle in needles:
            assert needle in source, f"{name} missing {needle}"
        leftover = []
        for match in _UNGUARDED_MUTATING_FETCH.finditer(source):
            snippet = " ".join(match.group(0).split())
            allowed = _ALLOWED_UNGUARDED_MUTATING.get(name, set())
            if not any(token in match.group(0) for token in allowed):
                leftover.append(snippet[:160])
        assert leftover == [], f"{name} still has unguarded mutating apiFetch: {leftover}"
