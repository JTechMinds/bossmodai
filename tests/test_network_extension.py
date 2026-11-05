"""The Local Network Access extension: access code, gate, a real listener on an ephemeral port, status and settings."""

from __future__ import annotations

import importlib
import logging
import os
import signal
import socket
import sys
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import InvalidStatus

import db
from core import config
from core.extensions import loader
from core.extensions.contract import ConfigError, ExtensionContext, ServiceStartError, SettingsIncompleteError
from core.extensions.registry import get_discovery
from core.extensions.service_host import ExtensionServiceHost

_ENTRY = get_discovery().get("network")
assert _ENTRY is not None and _ENTRY.valid, _ENTRY
_PACKAGE = loader.import_package(_ENTRY)
_access = importlib.import_module(f"{loader.package_name('network')}.access")
_gate = importlib.import_module(f"{loader.package_name('network')}.gate")
_addresses = importlib.import_module(f"{loader.package_name('network')}.addresses")
_status = importlib.import_module(f"{loader.package_name('network')}.status")

# A code an operator might choose: mixed case and an inner dash.
_CODE = "Boss-2026"


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


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# ─── the manifest ───


def test_the_manifest_declares_a_command_less_service_with_port_and_access_code_settings() -> None:
    manifest = _ENTRY.manifest
    assert manifest.command is None and manifest.service and manifest.panel
    assert manifest.prompt is None and manifest.agent_config is None and not manifest.setup.required
    assert set(manifest.requires.python_modules) == {"uvicorn"}
    port, code = manifest.settings.fields
    assert (port.key, port.kind, port.min, port.max, port.default) == ("port", "number", 1024, 65535, "38472")
    assert port.hint == "Change it only if another program already uses this port."
    assert (code.key, code.label, code.kind, code.required, code.min_length, code.default) == (
        "access_code", "Access code", "secret", True, 6, None,
    ), "the operator chooses the code: there is no default"
    assert code.hint == "At least 6 characters. Capital letters don’t matter. Changing it signs every device out."
    assert set(manifest.defaults) == {
        "bind_host", "login_max_failures", "login_lockout_seconds", "stop_timeout_seconds", "route_probe_address",
    }


# ─── access.py ───


def test_a_typed_code_matches_whatever_its_case_or_surrounding_spaces() -> None:
    assert _access.codes_match("boss-2026", "Boss-2026")
    assert _access.codes_match("  BOSS-2026 \n", "Boss-2026")
    assert _access.codes_match("Boss-2026", "  boss-2026 "), "the stored side is compared the same way"
    assert not _access.codes_match("boss2026", "Boss-2026"), "an inner dash makes a different code"
    assert not _access.codes_match("boss -2026", "Boss-2026"), "so does an inner space"
    assert not _access.codes_match("Boss-2027", "Boss-2026")
    assert not _access.codes_match("", "Boss-2026")
    assert _access.canonical_code("  Ab-C d ") == "ab-c d"


def test_a_session_value_belongs_to_one_code() -> None:
    first = _access.session_value("ABCD2345")
    assert first == _access.session_value("ABCD2345")
    assert first != _access.session_value("ABCD2346")
    assert "ABCD2345" not in first
    assert _access.session_matches(first, "ABCD2345")
    assert not _access.session_matches(first, "ABCD2346")
    assert not _access.session_matches(None, "ABCD2345") and not _access.session_matches("", "ABCD2345")


def test_the_throttle_locks_a_client_after_repeated_failures_and_releases_it() -> None:
    now = [100.0]
    throttle = _access.LoginThrottle(max_failures=3, lockout_seconds=60, clock=lambda: now[0])
    for _ in range(2):
        throttle.record_failure("10.0.0.5")
    assert throttle.retry_after("10.0.0.5") == 0
    throttle.record_failure("10.0.0.5")
    assert throttle.retry_after("10.0.0.5") == 60
    assert throttle.retry_after("10.0.0.6") == 0, "another client is unaffected"
    now[0] += 59.5
    assert throttle.retry_after("10.0.0.5") == 1
    now[0] += 1
    assert throttle.retry_after("10.0.0.5") == 0, "the lockout ends"
    throttle.record_failure("10.0.0.5")
    assert throttle.retry_after("10.0.0.5") == 0, "and the count starts again"


def test_failures_expire_and_a_success_forgets_them() -> None:
    now = [0.0]
    throttle = _access.LoginThrottle(max_failures=2, lockout_seconds=30, clock=lambda: now[0])
    throttle.record_failure("a")
    now[0] += 31
    throttle.record_failure("a")
    assert throttle.retry_after("a") == 0, "the first failure is too old to count"
    throttle.record_success("a")
    throttle.record_failure("a")
    assert throttle.retry_after("a") == 0
    with pytest.raises(ValueError):
        _access.LoginThrottle(max_failures=0, lockout_seconds=1)


# ─── status.py (pure: the platform is an argument) ───

_INTRO = "Check that both devices are on the same network. If they are, this computer’s firewall may be blocking port {port}."


def _facts(group) -> list[tuple]:
    return [(fact.label, fact.value, fact.copyable, fact.secret, fact.hint) for fact in group.facts]


def test_the_running_status_on_linux_connects_a_device_then_offers_the_ufw_command() -> None:
    status = _status.running("http://192.168.10.118:38472", _CODE, 38472, "linux")
    assert status.message == "Other devices on your network can open BossMod." and status.error is None
    connect, trouble = status.groups
    assert (connect.title, connect.intro) == ("Connect another device", None)
    assert _facts(connect) == [
        ("Address", "http://192.168.10.118:38472", True, False, "Open this in a web browser on the other device."),
        ("Access code", _CODE, True, True, "Enter it when the other device asks. You can change it in Settings."),
    ]
    assert (trouble.title, trouble.intro) == ("If a device can’t connect", _INTRO.format(port=38472))
    assert _facts(trouble) == [(
        "Firewall command (ufw)", "sudo ufw allow 38472/tcp", True, False,
        "If your firewall is ufw, the one Ubuntu uses, run this in a terminal.",
    )]


def test_the_running_status_on_windows_offers_only_the_windows_command() -> None:
    connect, trouble = _status.running("http://10.0.0.7:40001", _CODE, 40001, "win32").groups
    assert [fact.label for fact in connect.facts] == ["Address", "Access code"]
    assert trouble.intro == _INTRO.format(port=40001)
    assert _facts(trouble) == [(
        "Firewall command (Windows)",
        'netsh advfirewall firewall add rule name="BossMod" dir=in action=allow protocol=TCP localport=40001',
        True, False, "Run this in Command Prompt opened as administrator.",
    )]


def test_the_running_status_on_a_mac_has_no_command_and_mentions_its_allow_prompt() -> None:
    _connect, trouble = _status.running("http://10.0.0.7:38472", _CODE, 38472, "darwin").groups
    assert trouble.facts == []
    assert trouble.intro == (
        _INTRO.format(port=38472) + " If your Mac asks whether to accept incoming connections, choose Allow."
    )


@pytest.mark.parametrize("platform", ["freebsd14", "cygwin"])
def test_the_running_status_elsewhere_gives_the_firewall_sentence_alone(platform: str) -> None:
    _connect, trouble = _status.running("http://10.0.0.7:38472", _CODE, 38472, platform).groups
    assert trouble.facts == [] and trouble.intro == _INTRO.format(port=38472)


def test_the_stopped_and_offline_statuses_are_one_sentence_each() -> None:
    assert (_status.stopped().message, _status.stopped().groups) == ("Network access isn’t running.", [])
    assert (_status.offline().message, _status.offline().groups) == (
        "This computer isn’t connected to a network. Connect it to share BossMod.", [],
    )


# ─── addresses.py ───


def test_lan_urls_is_the_primary_outbound_address_only() -> None:
    seen: list[str] = []

    def probe(address: str) -> str:
        seen.append(address)
        return "192.168.10.118"

    assert _addresses.lan_urls(38472, "192.0.2.1", probe) == ["http://192.168.10.118:38472"]
    assert seen == ["192.0.2.1"], "the probe address comes from the caller"


def test_lan_urls_are_empty_when_there_is_no_route(caplog) -> None:
    def probe(address: str) -> str:
        raise OSError("Network is unreachable")

    with caplog.at_level("DEBUG"):
        assert _addresses.lan_urls(38472, "192.0.2.1", probe) == []
    assert any("Network is unreachable" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("found", ["127.0.0.1", "169.254.3.4", "0.0.0.0"])
def test_lan_urls_refuse_addresses_another_device_cannot_reach(found: str) -> None:
    assert _addresses.lan_urls(38472, "192.0.2.1", lambda address: found) == []


def test_the_real_probe_sends_nothing_and_answers_with_a_local_address_or_an_oserror() -> None:
    try:
        found = _addresses.outbound_ipv4("192.0.2.1")
    except OSError:
        return
    assert socket.inet_aton(found)


# ─── gate.py (in process, no socket) ───


def _inner_app() -> Starlette:
    async def home(request):
        return PlainTextResponse("home")

    async def api(request):
        return PlainTextResponse("api")

    async def own(request):
        return PlainTextResponse("own")

    async def asset(request):
        return PlainTextResponse("asset")

    async def socket_endpoint(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    return Starlette(routes=[
        Route("/", home),
        Route("/health", home),
        Route("/api/things", api),
        Route("/api/extensions/network/enabled", own, methods=["PUT"]),
        Route("/api/extensions/network", own),
        Route("/api/extensions/networking", api),
        Route("/static/app.js", asset),
        WebSocketRoute("/api/ws", socket_endpoint),
    ])


class _Gated:
    def __init__(self) -> None:
        self.code = "ABCD2345"
        self.now = [0.0]
        self.throttle = _access.LoginThrottle(max_failures=3, lockout_seconds=120, clock=lambda: self.now[0])
        self.app = self.gate(self.code)

    def gate(self, code: str):
        """A gate over the same app and throttle with its own code (a listener restarted on new settings)."""
        return _gate.LanGateMiddleware(
            _inner_app(), code=code, throttle=self.throttle, protected_prefix="/api/extensions/network",
        )

    def client(self, app=None) -> TestClient:
        return TestClient(app if app is not None else self.app, follow_redirects=False, client=("192.168.1.50", 5000))

    def login(self, client: TestClient, code: str | None = None):
        return client.post("/lan-login", data={"code": code or self.code})


@pytest.fixture()
def gated() -> _Gated:
    return _Gated()


def test_anonymous_pages_redirect_to_the_login_page_and_the_api_says_401(gated) -> None:
    client = gated.client()
    for path in ("/", "/health", "/anything"):
        response = client.get(path)
        assert response.status_code == 303 and response.headers["location"] == "/lan-login", path
    for path in ("/api/things", "/api/extensions/network/enabled", "/api"):
        assert client.get(path).status_code == 401, path
    assert client.put("/api/extensions/network/enabled").status_code == 401
    assert "home" not in client.get("/").text


def test_anonymous_websockets_are_closed_before_the_app_sees_them(gated) -> None:
    client = gated.client()
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/ws"):
            pass
    assert caught.value.code == 1008


def test_the_login_page_and_static_files_need_no_session(gated) -> None:
    client = gated.client()
    page = client.get("/lan-login")
    assert page.status_code == 200 and "Access code" in page.text and 'method="post"' in page.text
    assert page.headers["cache-control"] == "no-store"
    style = client.get("/lan-login/style.css")
    assert style.status_code == 200 and style.headers["content-type"].startswith("text/css")
    assert client.get("/static/app.js").text == "asset"


def test_a_wrong_code_shows_a_plain_message_and_sets_no_cookie(gated) -> None:
    client = gated.client()
    response = gated.login(client, "WRONG123")
    assert response.status_code == 401
    assert "That code isn’t right" in response.text
    assert "set-cookie" not in response.headers
    assert client.get("/").status_code == 303


def test_the_right_code_sets_a_locked_down_cookie_and_opens_the_app(gated) -> None:
    client = gated.client()
    response = gated.login(client)
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("bossmod_lan=") and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "ABCD2345" not in cookie
    assert "Max-Age" not in cookie and "expires" not in cookie.lower(), "a browser-session cookie"

    assert client.get("/").text == "home"
    assert client.get("/api/things").text == "api"
    assert client.get("/lan-login").status_code == 303, "signed in: the login page sends you on"
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_text() == "hello"


def test_a_typed_code_in_lower_case_with_surrounding_spaces_is_accepted(gated) -> None:
    assert gated.login(gated.client(), "  abcd2345 ").status_code == 303
    assert gated.login(gated.client(), "abcd 2345").status_code == 401, "an inner space is part of the code"


def test_a_gate_refuses_an_empty_code() -> None:
    with pytest.raises(ValueError, match="empty"):
        _Gated().gate("   ")


def test_repeated_wrong_codes_lock_the_client_out_even_for_the_right_code(gated) -> None:
    client = gated.client()
    assert gated.login(client, "AAAA1111").status_code == 401
    assert gated.login(client, "AAAA1111").status_code == 401
    locked = gated.login(client, "AAAA1111")
    assert locked.status_code == 429 and locked.headers["retry-after"] == "120"
    assert "Wait 2 minutes" in locked.text
    assert gated.login(client).status_code == 429, "the right code does not get past a lockout"
    gated.now[0] += 121
    assert gated.login(client).status_code == 303


def test_a_new_code_signs_every_device_out(gated) -> None:
    client = gated.client()
    cookie = {"cookie": f"bossmod_lan={gated.login(client).cookies['bossmod_lan']}"}
    assert client.get("/").text == "home"

    # Saving a new code restarts the listener, which builds a new gate.
    changed = gated.client(gated.gate("ZZZZ9999"))
    assert changed.get("/", headers=cookie).status_code == 303
    assert changed.get("/api/things", headers=cookie).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with changed.websocket_connect("/api/ws", headers=cookie):
            pass

    recased = gated.client(gated.gate("abcd2345"))
    assert recased.get("/", headers=cookie).text == "home", "changing only the code's case signs nobody out"


def test_the_extensions_own_routes_are_refused_to_network_sessions(gated) -> None:
    client = gated.client()
    gated.login(client)
    for method, path in (("PUT", "/api/extensions/network/enabled"), ("GET", "/api/extensions/network")):
        response = client.request(method, path)
        assert response.status_code == 403, path
        assert response.json() == {"detail": "This can only be changed on the computer running BossMod."}
    assert client.get("/api/extensions/networking").text == "api", "only the extension's own prefix"


def test_a_huge_login_body_and_other_methods_are_refused(gated) -> None:
    client = gated.client()
    assert client.post("/lan-login", content=b"code=" + b"A" * 10_000).status_code == 413
    assert client.delete("/lan-login").status_code == 405


def test_a_malformed_cookie_header_is_just_not_signed_in(gated) -> None:
    client = gated.client()
    assert client.get("/", headers={"cookie": "bossmod_lan"}).status_code == 303
    assert client.get("/", headers={"cookie": "bossmod_lan=garbage"}).status_code == 303


# ─── the extension itself ───


def _context(tmp_path: Path, settings: dict[str, str]) -> ExtensionContext:
    # No seal/unseal: the extension keeps nothing of its own (the host stores
    # the code), so a call to either would raise "not supplied".
    return ExtensionContext(
        manifest=_ENTRY.manifest,
        data_dir=tmp_path / "network-data",
        read_agent_config=lambda agent_id: None,
        read_settings=lambda: dict(settings),
    )


def _extension(tmp_path: Path, port: int, code: str = _CODE):
    settings = {"port": str(port), "access_code": code}
    return _PACKAGE.create(_context(tmp_path, settings)), settings


def test_creating_the_extension_touches_nothing(tmp_path: Path) -> None:
    extension, _settings = _extension(tmp_path, _free_port())
    assert not (tmp_path / "network-data").exists()
    extension.shutdown()


def test_bad_manifest_defaults_are_refused_at_create(tmp_path: Path) -> None:
    from dataclasses import replace

    bad = _ENTRY.manifest.model_copy(update={"defaults": {**_ENTRY.manifest.defaults, "login_max_failures": 0}})
    with pytest.raises(ValueError, match="defaults are invalid"):
        _PACKAGE.create(replace(_context(tmp_path, {"port": "1"}), manifest=bad))


def test_the_panel_of_a_stopped_extension_says_so_and_shows_no_groups(tmp_path: Path) -> None:
    extension, _settings = _extension(tmp_path, _free_port())
    status = extension.panel()
    assert status.message == "Network access isn’t running." and status.groups == []


def _code_of(status) -> str:
    return next(fact.value for group in status.groups for fact in group.facts if fact.label == "Access code")


async def test_a_listener_serves_the_login_page_rejects_strangers_and_admits_the_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_PACKAGE, "lan_urls", lambda port, probe_address: [f"http://192.168.1.20:{port}"])
    port = _free_port()
    extension, _settings = _extension(tmp_path, port)
    before = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))

    await extension.start_service(_inner_app())
    try:
        assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before, "signals left alone"
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(follow_redirects=False) as http:
            login = await http.get(f"{base}/lan-login")
            assert login.status_code == 200 and "Access code" in login.text
            anonymous = await http.get(f"{base}/")
            assert anonymous.status_code == 303 and anonymous.headers["location"] == "/lan-login"
            assert (await http.get(f"{base}/api/things")).status_code == 401
            assert (await http.get(f"{base}/health")).status_code == 303

            with pytest.raises(InvalidStatus) as refused:
                async with ws_connect(f"ws://127.0.0.1:{port}/api/ws"):
                    pass
            assert refused.value.response.status_code == 403

            wrong = await http.post(f"{base}/lan-login", data={"code": "WRONG999"})
            assert wrong.status_code == 401
            ok = await http.post(f"{base}/lan-login", data={"code": _CODE.upper()})
            assert ok.status_code == 303, "the code from settings, typed in another case"
            cookie = ok.cookies["bossmod_lan"]
            assert (await http.get(f"{base}/")).text == "home"
            async with ws_connect(f"ws://127.0.0.1:{port}/api/ws", additional_headers={"Cookie": f"bossmod_lan={cookie}"}) as ws:
                assert await ws.recv() == "hello"

            # The panel shows the address and the code (masked, copyable) from settings.
            connect, _trouble = extension.panel().groups
            address, code = connect.facts
            assert address.value == f"http://192.168.1.20:{port}"
            assert (code.label, code.value, code.copyable, code.secret) == ("Access code", _CODE, True, True)
    finally:
        await extension.stop_service()

    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before
    await extension.stop_service()  # idempotent


async def test_the_panel_says_when_there_is_no_network_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_PACKAGE, "lan_urls", lambda port, probe_address: [])
    extension, _settings = _extension(tmp_path, _free_port())
    await extension.start_service(_inner_app())
    try:
        status = extension.panel()
        assert status.message == "This computer isn’t connected to a network. Connect it to share BossMod."
        assert status.groups == []
    finally:
        await extension.stop_service()


async def test_the_listener_uses_the_code_from_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_PACKAGE, "lan_urls", lambda port, probe_address: [f"http://192.168.1.20:{port}"])
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    extension, settings = _extension(tmp_path, port, code="first-code")
    await extension.start_service(_inner_app())
    try:
        assert _code_of(extension.panel()) == "first-code"
        async with httpx.AsyncClient(follow_redirects=False) as http:
            assert (await http.post(f"{base}/lan-login", data={"code": "first-code"})).status_code == 303
    finally:
        await extension.stop_service()

    # A settings save restarts the listener; the new start reads the new code.
    settings["access_code"] = "second-code"
    await extension.start_service(_inner_app())
    try:
        assert _code_of(extension.panel()) == "second-code"
        async with httpx.AsyncClient(follow_redirects=False) as http:
            assert (await http.post(f"{base}/lan-login", data={"code": "first-code"})).status_code == 401
            assert (await http.post(f"{base}/lan-login", data={"code": "second-code"})).status_code == 303
    finally:
        await extension.stop_service()


async def test_starting_without_an_access_code_is_refused_as_unset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real loader-bound settings reader: a port alone is not enough to start."""
    monkeypatch.setattr(loader, "_loaded", {})
    db.set_extension_settings("network", {"port": str(_free_port())})
    extension = loader.load_extension(_ENTRY)
    with pytest.raises(SettingsIncompleteError) as caught:
        await extension.start_service(_inner_app())
    assert caught.value.labels == ("Access code",)
    assert extension.panel().message == "Network access isn’t running."


async def test_the_host_asks_for_the_access_code_when_it_was_never_set(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setattr(loader, "_loaded", {})
    host = ExtensionServiceHost(discovery=get_discovery, enabled=lambda: frozenset({"network"}))
    host.attach(_inner_app())
    with caplog.at_level(logging.INFO):
        failed = await host.reconcile()
    assert failed == {"network": "Fill in “Access code” in Settings to start it."}
    assert host.error("network") == "Fill in “Access code” in Settings to start it."
    assert not host.is_running("network")
    assert not any(record.exc_info for record in caplog.records), "an operator state, not a fault: no traceback"


async def test_a_port_in_use_is_refused_with_the_reason(tmp_path: Path) -> None:
    with socket.socket() as holder:
        holder.bind(("0.0.0.0", 0))
        holder.listen()
        taken = holder.getsockname()[1]
        extension, _ = _extension(tmp_path, taken)
        with pytest.raises(ServiceStartError, match=f"^Port {taken} is already in use.$"):
            await extension.start_service(_inner_app())
        assert extension.panel().message == "Network access isn’t running."
        with pytest.raises(ConfigError, match=f"^Port {taken} is already in use.$"):
            extension.verify_settings({"port": str(taken)})


async def test_verifying_settings_probes_a_new_port_and_says_when_devices_need_the_new_code(tmp_path: Path) -> None:
    port, other = _free_port(), _free_port()
    extension, _ = _extension(tmp_path, port)
    assert extension.verify_settings({"port": str(other), "access_code": "anything"}) == f"Port {other} is available."
    await extension.start_service(_inner_app())
    try:
        assert extension.verify_settings({"port": str(port), "access_code": _CODE}) == "Saved."
        assert extension.verify_settings({"port": str(port), "access_code": _CODE.lower()}) == "Saved.", (
            "a change of case alone signs nobody out"
        )
        assert extension.verify_settings({"port": str(port), "access_code": "new-code"}) == (
            "Signed-in devices will need the new code."
        )
        assert extension.verify_settings({"port": str(other), "access_code": _CODE}) == f"Port {other} is available."
        assert extension.verify_settings({"port": str(other), "access_code": "new-code"}) == (
            f"Port {other} is available. Signed-in devices will need the new code."
        )
    finally:
        await extension.stop_service()


async def test_changing_the_port_restarts_the_listener_through_the_service_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real extension, loaded by the real loader, behind the host: new port in, old port closed."""
    monkeypatch.setattr(loader, "_loaded", {})
    first, second = _free_port(), _free_port()
    db.set_extension_settings("network", {"port": str(first), "access_code": _CODE})
    host = ExtensionServiceHost(discovery=get_discovery, enabled=lambda: frozenset({"network"}))
    host.attach(_inner_app())
    try:
        assert await host.reconcile() == {}
        async with httpx.AsyncClient(follow_redirects=False) as http:
            assert (await http.get(f"http://127.0.0.1:{first}/lan-login")).status_code == 200

            db.set_extension_settings("network", {"port": str(second), "access_code": _CODE})
            assert await host.restart("network") is None

            assert (await http.get(f"http://127.0.0.1:{second}/lan-login")).status_code == 200
            with pytest.raises(httpx.ConnectError):
                await http.get(f"http://127.0.0.1:{first}/lan-login")
    finally:
        await host.stop_all()
    assert not host.is_running("network")


async def test_the_host_reports_a_taken_port_as_the_start_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_loaded", {})
    with socket.socket() as holder:
        holder.bind(("0.0.0.0", 0))
        holder.listen()
        taken = holder.getsockname()[1]
        db.set_extension_settings("network", {"port": str(taken), "access_code": _CODE})
        host = ExtensionServiceHost(discovery=get_discovery, enabled=lambda: frozenset({"network"}))
        host.attach(_inner_app())
        assert await host.reconcile() == {"network": f"Port {taken} is already in use."}
        assert host.error("network") == f"Port {taken} is already in use."


async def test_the_real_app_behind_the_gate_hands_out_nothing_before_the_code(tmp_path: Path) -> None:
    """The point of the gate: ``GET /`` on the real app carries the API token, so it must not be reachable first."""
    from main import app

    port = _free_port()
    extension, _settings = _extension(tmp_path, port)
    await extension.start_service(app)
    try:
        token = db.ensure_local_api_token()
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(follow_redirects=False) as http:
            anonymous = await http.get(f"{base}/")
            assert anonymous.status_code == 303 and token not in anonymous.text
            assert (await http.get(f"{base}/api/extensions", headers={"X-BossMod-Token": token})).status_code == 401, (
                "even a caller who knows the API token needs the access code first"
            )

            assert (await http.post(f"{base}/lan-login", data={"code": _CODE})).status_code == 303
            page = await http.get(f"{base}/")
            assert page.status_code == 200 and token in page.text
            assert (await http.get(f"{base}/api/extensions")).status_code == 401, "the API token still gates /api"
            listing = await http.get(f"{base}/api/extensions", headers={"X-BossMod-Token": token})
            assert listing.status_code == 200
            own = await http.put(
                f"{base}/api/extensions/network/enabled", json={"enabled": False}, headers={"X-BossMod-Token": token},
            )
            assert own.status_code == 403, "a network session cannot switch the feature off"
    finally:
        await extension.stop_service()


async def test_the_extension_passes_its_platform_through_to_the_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_PACKAGE, "lan_urls", lambda port, probe_address: [f"http://192.168.1.20:{port}"])
    port = _free_port()
    url = f"http://192.168.1.20:{port}"

    created, _settings = _extension(tmp_path, port)
    await created.start_service(_inner_app())
    try:
        assert created.panel() == _status.running(url, _CODE, port, sys.platform), "create() hands over sys.platform"
    finally:
        await created.stop_service()

    defaults = _PACKAGE.NetworkDefaults.model_validate(_ENTRY.manifest.defaults)
    settings = {"port": str(port), "access_code": _CODE}
    windows = _PACKAGE.NetworkExtension(_context(tmp_path, settings), defaults, platform="win32")
    await windows.start_service(_inner_app())
    try:
        assert windows.panel() == _status.running(url, _CODE, port, "win32")
        assert windows.panel().groups[1].facts[0].label == "Firewall command (Windows)"
    finally:
        await windows.stop_service()
