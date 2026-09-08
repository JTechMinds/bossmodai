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
    # Pause was app.js's; the header owns it since Phase 1, and app.js was
    # deleted in Phase 4. Same call, same endpoint, same reason it is listed:
    # the emergency stop must go through the token wrap like everything else.
    "shell/header.js": [
        "apiFetch('/api/runtime/state'",
    ],
    # The conversation sources take the helper through ctx.api, so their call
    # sites are spelled api(...). The DI chain is asserted separately below.
    "conversation/sources/agent-source.js": [
        "api(`/api/agents/${agentId}/activate`",
        "api(`/api/agents/${agentId}/messages?limit=50`",
    ],
    "conversation/sources/thread-source.js": [
        "api(`/api/channels/${threadId}/messages`",
    ],
    # Files left the manifest with the dock shell. Its reads and its
    # open-folder call are now DI'd, so they are spelled api(...); the DI chain
    # that binds that to apiFetch is asserted below.
    "places/files/files-data.js": [
        "api(directoryUrl(path), { cache: 'no-store' })",
        "`/api/company/files?path=${encodeURIComponent(path)}`",
    ],
    "places/files/folder-opener.js": [
        "api('/api/company/files/open-folder'",
    ],
    # Phase 3C split Connections at the list/form seam: the list reads, the
    # form writes and tests. Both halves are named so neither loses coverage.
    "settings/settings-connections.js": [
        "apiFetch('/api/connections')",
    ],
    "settings/settings-connections-form.js": [
        "apiFetch('/api/connections/test'",
    ],
    # Phase 3C: the simulator is a shell, a runner and an output painter. The
    # request lives in the runner.
    "settings/cli-policy/simulator-run.js": [
        "apiFetch('/api/cli-policy/simulator/execute'",
    ],
}

# Modules that receive the authenticated helper by injection instead of
# reaching for the global. They may name an /api/ path without naming
# apiFetch; that they never name it is asserted positively by
# test_conversation_sources_take_api_by_injection, and the DI chain that
# binds it to apiFetch is asserted there too.
API_BY_INJECTION = {
    "core/consent-card.js",
    # The rail's Threads half takes shell/roster.js's shared readJson helper,
    # which is the only thing in the rail that touches apiFetch. So does the
    # creation half it was split into, which is what POSTs /api/channels.
    "shell/roster-threads.js",
    "shell/thread-create.js",
    # The needs modules take `api` from the shell's ctx.
    "needs/needs-store.js",
    "needs/need-shape.js",
    # The context column takes `api` from the place ctx and hands it down.
    # The office summary is on the list because the ROOM LIST comes from
    # GET /api/map — the floor plan, not the roster — while who is standing in
    # each room still comes from the store.
    "context/mini-office.js",
    "context/desk-files.js",
    "context/desk-notes.js",
    "context/desk-opener.js",
    "context/desk-panel.js",
    "context/desk-tasks.js",
    "context/desk-actions.js",
    "conversation/conversation.js",
    "conversation/sources/agent-source.js",
    "conversation/sources/thread-source.js",
    "conversation/sources/thread-archive.js",
    # The Office and Board places take `api` from the shell's ctx and hand it
    # down; none of them names the global.
    "places/office/office-canvas.js",
    "places/office/org-view.js",
    "places/office/office-place.js",
    "places/board/board-data.js",
    "places/board/board-place.js",
    "places/board/task-deliverables.js",
    "places/board/task-events.js",
    "places/board/assign-form.js",
    "places/board/board-cancel.js",
    # The Files place and its dialogs take `api` from the shell's ctx and hand
    # it down; none of them names the global.
    "places/files/file-viewer.js",
    "places/files/files-data.js",
    # Metrics reads GET /api/metrics/dashboard through the injected helper.
    "places/metrics/metrics-place.js",
    # The Log reads both feeds through the injected helper.
    "places/log/log-source.js",
    "places/log/diagnostic-detail.js",
    "places/files/file-ops.js",
    "places/files/host-roots.js",
    "places/files/folder-opener.js",
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
    # utils.js was split into the three core modules in Phase 4; the load
    # order it stood for is asserted against each of them.
    for module in ("js/core/format.js", "js/core/agent-status.js", "js/core/specialty.js"):
        assert sources.index("js/api-client.js") < sources.index(module)
    # app.js, agent-context.js and company-files.js left the manifest with the
    # dock shell. The modules that call apiFetch in their place are asserted
    # instead, so every loaded consumer is still covered.
    assert sources.index("js/api-client.js") < sources.index("js/conversation/conversation.js")
    assert sources.index("js/api-client.js") < sources.index("js/shell/shell.js")
    assert sources.index("js/api-client.js") < sources.index("js/shell/header.js")
    assert sources.index("js/api-client.js") < sources.index("js/shell/roster.js")
    assert sources.index("js/api-client.js") < sources.index("js/shell/banners.js")
    assert sources.index("js/api-client.js") < sources.index("js/settings/cli-policy/simulator.js")
    assert sources.index("js/api-client.js") < sources.index("js/settings/settings-connections.js")


def test_modules_below_the_shell_take_api_by_injection() -> None:
    """A spelling assertion is weaker than the property it stands for.

    What actually guarantees the token wrap sits under every call is the DI
    chain: the shell injects apiFetch as ctx.api and no module below it names
    the global at all. Every entry API_BY_INJECTION excuses from naming
    apiFetch is asserted here to have earned it, so the exemption list can
    never become a way to opt out of the token wrap.
    """
    shell = _read("shell/shell.js")
    assert "api: apiFetch" in shell
    for name in ("context/desk-notes.js",
                 "conversation/conversation.js",
                 "conversation/sources/agent-source.js",
                 "conversation/sources/thread-source.js",
                 "conversation/sources/thread-archive.js",
                 "places/office/office-canvas.js",
                 "places/office/org-view.js",
                 "places/office/office-place.js",
                 "places/board/board-data.js",
                 "places/board/board-place.js",
                 "places/board/task-deliverables.js",
                 "places/board/task-events.js",
                 "places/board/assign-form.js",
                 "places/board/board-cancel.js"):
        source = _read(name)
        assert "apiFetch" not in source, f"{name} must take api from ctx"


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
            relative = path.relative_to(JS).as_posix()
            # apiFetchOk counts: api-client.js defines it as apiFetch plus a
            # res.ok check, so a module that only ever calls the throwing
            # variant is still entirely under the token wrap. rule-form.js is
            # the first module whose every call is a mutating save.
            assert (
                "apiFetch(" in text
                or "apiFetchOk(" in text
                or relative in API_BY_INJECTION
            ), path.name
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
    "settings/settings-connections.js": [
        "apiFetchOk(`/api/connections/${btn.dataset.deleteConn}`",
    ],
    "settings/settings-connections-form.js": [
        "apiFetchOk(`/api/connections/${conn.id}`",
        "apiFetchOk('/api/connections'",
    ],
    "settings/cli-policy/policy-settings.js": [
        "apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(newVal)}&category=cli_policy`",
        "apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=cli_policy`",
        "applySettingSaveResult(card, false",
    ],
    # The rule writes left cli-policy-section.js in Phase 3C. Every mutating
    # call site is named individually now rather than the create alone, so the
    # unguarded-mutation scan below still covers all of them.
    "settings/cli-policy/rules-tab.js": [
        "apiFetchOk(`/api/cli-policy/rules/${rule.id}`",
        "apiFetchOk(`/api/cli-policy/rules/${del.dataset.deleteRule}`",
        "apiFetchOk('/api/cli-policy/rules/seed-defaults'",
    ],
    "settings/cli-policy/rule-form.js": [
        "apiFetchOk(`/api/cli-policy/rules/${rule.id}`",
        "apiFetchOk('/api/cli-policy/rules'",
    ],
    "settings/settings-system.js": [
        "apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=${encodeURIComponent(category)}`",
    ],
    "settings/settings-advanced.js": [
        "apiFetchOk(`/api/settings/diagnostics_enabled?value=${newValue}&category=advanced`",
        "apiFetchOk(`/api/settings/diagnostics_retention_limit?value=${encodeURIComponent(value)}&category=advanced`",
        "apiFetchOk(`/api/settings/cli_max_read_lines?value=${encodeURIComponent(value)}&category=advanced`",
        "apiFetchOk(`/api/settings/desktop_open_folder_handler?value=${encodeURIComponent(resolvedValue)}&category=advanced`",
    ],
    # Re-pointed in Phase 3B. apiFetchOk is not injectable, so the guard it
    # provided is written out at each site and asserted by
    # test_injected_saves_check_the_response below — the property is unchanged:
    # a save UI cannot flash success on a response nobody read.
    "places/files/host-roots.js": [
        "`/api/settings/workspace_host_roots?value=${encodeURIComponent(value)}&category=cli_policy`",
        "if (!res.ok) throw new Error(await BossModFileOps.readApiError(res));",
    ],
    "context/desk-opener.js": [
        "`?value=${encodeURIComponent(chosen)}&category=advanced`",
        "if (!saved.ok) {",
        "onError('Could not save that folder opener.');",
    ],
    "places/files/folder-opener.js": [
        "FAILURE_COPY = 'Failed to open folder'",
        "onError,",
    ],
}

# Files that take `api` by injection and still mutate. Every awaited mutating
# call in one of these must have its response checked within a few lines, which
# is what apiFetchOk did for the call sites that could reach the global.
_INJECTED_MUTATORS = (
    "places/files/file-ops.js",
    "places/files/file-viewer.js",
    "places/files/host-roots.js",
    "context/desk-opener.js",
)

_INJECTED_MUTATING_CALL = re.compile(
    r"await api\("
    r"(?:[^;]|\n){0,400}?"
    # `method,` is the shorthand file-ops.js's shared send() uses.
    r"method\s*[,:]",
    re.S,
)

_UNGUARDED_MUTATING_FETCH = re.compile(
    r"await apiFetch(?!Ok)\("
    r"(?:[^;]|\n){0,500}?"
    r"method:\s*'(?:PUT|POST|PATCH|DELETE)'",
    re.S,
)

def test_injected_saves_check_the_response() -> None:
    """apiFetchOk's guarantee, kept for the modules that cannot reach it.

    ctx.api is apiFetch, which does not throw, so a module below the shell has
    to read res.ok itself. company-files.js used apiFetchOk for its two setting
    writes; the ported modules check the response explicitly instead, and this
    asserts they all do — otherwise the exemption from apiFetchOk would quietly
    become an exemption from checking at all.
    """
    for name in _INJECTED_MUTATORS:
        source = _read(name)
        calls = list(_INJECTED_MUTATING_CALL.finditer(source))
        assert calls, f"{name} is listed as a mutator but makes no mutating call"
        for match in calls:
            window = source[match.end(): match.end() + 260]
            assert ".ok" in window, (
                f"{name} does not check the response of "
                f"{' '.join(match.group(0).split())[:90]}"
            )


# These already inspect res.ok before success UI; leave the explicit check.
_ALLOWED_UNGUARDED_MUTATING = {
    "settings/settings-advanced.js": {
        "await apiFetch('/api/agents'",
        "await apiFetch('/api/settings/reseed-application'",
    },
    # Test-connection already branches on resp.ok / result.ok before any success UI.
    "settings/settings-connections-form.js": {
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
