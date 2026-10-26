"""The Local Network Access extension: access code, gate, a real listener on an ephemeral port, panel and settings."""

from __future__ import annotations

import importlib
import os
import signal
import socket
import stat
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
from core.extensions.contract import ConfigError, ExtensionContext, PanelError, ServiceStartError
from core.extensions.paths import extension_data_dir
from core.extensions.registry import get_discovery
from core.extensions.service_host import ExtensionServiceHost

_ENTRY = get_discovery().get("network")
assert _ENTRY is not None and _ENTRY.valid, _ENTRY
_PACKAGE = loader.import_package(_ENTRY)
_access = importlib.import_module(f"{loader.package_name('network')}.access")
_gate = importlib.import_module(f"{loader.package_name('network')}.gate")
_addresses = importlib.import_module(f"{loader.package_name('network')}.addresses")


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


def test_the_manifest_declares_a_command_less_service_with_a_port_setting() -> None:
    manifest = _ENTRY.manifest
    assert manifest.command is None and manifest.service and manifest.panel
    assert manifest.prompt is None and manifest.agent_config is None and not manifest.setup.required
    assert set(manifest.requires.python_modules) == {"uvicorn"}
    (port,) = manifest.settings.fields
    assert (port.key, port.kind, port.min, port.max, port.default) == ("port", "number", 1024, 65535, "38472")
    assert set(manifest.defaults) == {
        "bind_host", "login_max_failures", "login_lockout_seconds", "code_length", "stop_timeout_seconds",
        "route_probe_address",
    }


# ─── access.py ───


def test_codes_use_the_unambiguous_alphabet_and_the_requested_length() -> None:
    code = _access.generate_code(40)
    assert len(code) == 40 and set(code) <= set(_access.CODE_ALPHABET)
    assert not set("0O1IL") & set(_access.CODE_ALPHABET)
    assert _access.generate_code(40) != code
    with pytest.raises(ValueError):
        _access.generate_code(0)


def test_a_typed_code_matches_however_it_is_spaced_or_cased() -> None:
    assert _access.codes_match("abcd-2345", "ABCD2345")
    assert _access.codes_match("  ab cd 23 45 ", "ABCD2345")
    assert not _access.codes_match("ABCD2346", "ABCD2345")
    assert not _access.codes_match("", "ABCD2345")


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


def _store(tmp_path: Path):
    return _access.AccessCodeStore(
        tmp_path / "data" / "access_code",
        seal=lambda value: "sealed:" + value[::-1],
        unseal=lambda value: _unseal(value),
        length=8,
    )


def _unseal(value: str) -> str:
    if not value.startswith("sealed:"):
        raise RuntimeError("not sealed")
    return value[len("sealed:"):][::-1]


def test_the_code_is_stored_sealed_with_owner_only_permissions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    code = store.load_or_create()
    path = tmp_path / "data" / "access_code"
    assert len(code) == 8
    assert code not in path.read_text(encoding="utf-8"), "never plain on disk"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load_or_create() == code, "a second load reads the same code"
    assert not list(path.parent.glob("*.tmp")), "no temp file is left behind"


def test_rotating_replaces_the_stored_code(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.load_or_create()
    second = store.rotate()
    assert second != first and store.load_or_create() == second
    assert stat.S_IMODE((tmp_path / "data" / "access_code").stat().st_mode) == 0o600


def test_a_damaged_code_file_is_replaced_and_the_replacement_is_logged(tmp_path: Path, caplog) -> None:
    store = _store(tmp_path)
    store.load_or_create()
    path = tmp_path / "data" / "access_code"
    path.write_text("garbage", encoding="utf-8")
    with caplog.at_level("WARNING"):
        code = store.load_or_create()
    assert len(code) == 8 and store.load_or_create() == code
    assert any("cannot be read" in record.getMessage() for record in caplog.records)

    path.write_text(_unseal_inverse("not a code!"), encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert store.load_or_create() != "not a code!"
    assert any("malformed" in record.getMessage() for record in caplog.records)


def _unseal_inverse(plain: str) -> str:
    return "sealed:" + plain[::-1]


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
        self.code = ["ABCD2345"]
        self.now = [0.0]
        self.throttle = _access.LoginThrottle(max_failures=3, lockout_seconds=120, clock=lambda: self.now[0])
        self.app = _gate.LanGateMiddleware(
            _inner_app(), code_provider=lambda: self.code[0], throttle=self.throttle,
            protected_prefix="/api/extensions/network",
        )

    def client(self) -> TestClient:
        return TestClient(self.app, follow_redirects=False, client=("192.168.1.50", 5000))

    def login(self, client: TestClient, code: str | None = None):
        return client.post("/lan-login", data={"code": code or self.code[0]})


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


def test_a_typed_code_in_lower_case_with_spaces_is_accepted(gated) -> None:
    assert gated.login(gated.client(), "abcd 2345").status_code == 303


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
    gated.login(client)
    assert client.get("/").text == "home"
    gated.code[0] = "ZZZZ9999"
    assert client.get("/").status_code == 303
    assert client.get("/api/things").status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws"):
            pass


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


def _context(tmp_path: Path, port_holder: dict[str, str]) -> ExtensionContext:
    return ExtensionContext(
        manifest=_ENTRY.manifest,
        data_dir=tmp_path / "network-data",
        read_agent_config=lambda agent_id: None,
        read_settings=lambda: dict(port_holder),
        seal=lambda value: "sealed:" + value[::-1],
        unseal=_unseal,
    )


def _extension(tmp_path: Path, port: int):
    holder = {"port": str(port)}
    return _PACKAGE.create(_context(tmp_path, holder)), holder


def test_creating_the_extension_touches_nothing(tmp_path: Path) -> None:
    extension, _holder = _extension(tmp_path, _free_port())
    assert not (tmp_path / "network-data").exists()
    extension.shutdown()


def test_bad_manifest_defaults_are_refused_at_create(tmp_path: Path) -> None:
    from dataclasses import replace

    bad = _ENTRY.manifest.model_copy(update={"defaults": {**_ENTRY.manifest.defaults, "code_length": 1}})
    with pytest.raises(ValueError, match="defaults are invalid"):
        _PACKAGE.create(replace(_context(tmp_path, {"port": "1"}), manifest=bad))


def test_the_panel_of_a_stopped_extension_says_so_and_offers_nothing(tmp_path: Path) -> None:
    extension, _holder = _extension(tmp_path, _free_port())
    status = extension.panel()
    assert status.message == "Network access isn’t running." and status.facts == [] and status.actions == []


def test_an_unknown_panel_action_is_refused(tmp_path: Path) -> None:
    extension, _holder = _extension(tmp_path, _free_port())
    with pytest.raises(PanelError):
        extension.panel_action("nope", {})


async def test_a_listener_serves_the_login_page_rejects_strangers_and_admits_the_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_PACKAGE, "lan_urls", lambda port, probe_address: [f"http://192.168.1.20:{port}"])
    port = _free_port()
    extension, _holder = _extension(tmp_path, port)
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

            code = extension.panel().facts[-1].value
            wrong = await http.post(f"{base}/lan-login", data={"code": "WRONG999"})
            assert wrong.status_code == 401
            ok = await http.post(f"{base}/lan-login", data={"code": code})
            assert ok.status_code == 303
            cookie = ok.cookies["bossmod_lan"]
            assert (await http.get(f"{base}/")).text == "home"
            async with ws_connect(f"ws://127.0.0.1:{port}/api/ws", additional_headers={"Cookie": f"bossmod_lan={cookie}"}) as ws:
                assert await ws.recv() == "hello"

            # The panel shows both addresses, the code (masked, copyable) and the action.
            status = extension.panel()
            assert [fact.value for fact in status.facts[:1]] == [f"http://192.168.1.20:{port}"]
            assert len(status.facts) == 2
            secret = status.facts[-1]
            assert (secret.label, secret.copyable, secret.secret) == ("Access code", True, True)
            assert [action.key for action in status.actions] == ["new_code"]

            extension.panel_action("new_code", {})
            assert extension.panel().facts[-1].value != code
            stale = await http.get(f"{base}/", headers={"Cookie": f"bossmod_lan={cookie}"})
            assert stale.status_code == 303, "the old session ends with the old code"
    finally:
        await extension.stop_service()

    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before
    await extension.stop_service()  # idempotent


async def test_the_panel_says_when_there_is_no_network_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_PACKAGE, "lan_urls", lambda port, probe_address: [])
    extension, _holder = _extension(tmp_path, _free_port())
    await extension.start_service(_inner_app())
    try:
        status = extension.panel()
        assert status.message.startswith("No network connection found")
        assert [fact.label for fact in status.facts] == ["Access code"]
    finally:
        await extension.stop_service()


async def test_the_code_survives_a_restart_of_the_listener(tmp_path: Path) -> None:
    port = _free_port()
    extension, _holder = _extension(tmp_path, port)
    await extension.start_service(_inner_app())
    code = extension.panel().facts[-1].value
    await extension.stop_service()
    await extension.start_service(_inner_app())
    try:
        assert extension.panel().facts[-1].value == code
    finally:
        await extension.stop_service()
    again, _ = _extension(tmp_path, port)
    await again.start_service(_inner_app())
    try:
        assert again.panel().facts[-1].value == code, "a new instance reads the stored code"
    finally:
        await again.stop_service()


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


async def test_verifying_settings_probes_a_free_port_and_lets_the_serving_port_keep_itself(tmp_path: Path) -> None:
    port, other = _free_port(), _free_port()
    extension, _ = _extension(tmp_path, port)
    assert extension.verify_settings({"port": str(other)}) == f"Port {other} is available."
    await extension.start_service(_inner_app())
    try:
        assert extension.verify_settings({"port": str(port)}) == f"Keeping port {port}."
        assert extension.verify_settings({"port": str(other)}) == f"Port {other} is available."
    finally:
        await extension.stop_service()


async def test_changing_the_port_restarts_the_listener_through_the_service_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real extension, loaded by the real loader, behind the host: new port in, old port closed."""
    monkeypatch.setattr(loader, "_loaded", {})
    first, second = _free_port(), _free_port()
    db.set_extension_settings("network", {"port": str(first)})
    host = ExtensionServiceHost(discovery=get_discovery, enabled=lambda: frozenset({"network"}))
    host.attach(_inner_app())
    try:
        assert await host.reconcile() == {}
        async with httpx.AsyncClient(follow_redirects=False) as http:
            assert (await http.get(f"http://127.0.0.1:{first}/lan-login")).status_code == 200

            db.set_extension_settings("network", {"port": str(second)})
            assert await host.restart("network") is None

            assert (await http.get(f"http://127.0.0.1:{second}/lan-login")).status_code == 200
            with pytest.raises(httpx.ConnectError):
                await http.get(f"http://127.0.0.1:{first}/lan-login")
    finally:
        await host.stop_all()
    assert not host.is_running("network")
    assert (extension_data_dir("network") / "access_code").is_file()


async def test_the_host_reports_a_taken_port_as_the_start_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_loaded", {})
    with socket.socket() as holder:
        holder.bind(("0.0.0.0", 0))
        holder.listen()
        taken = holder.getsockname()[1]
        db.set_extension_settings("network", {"port": str(taken)})
        host = ExtensionServiceHost(discovery=get_discovery, enabled=lambda: frozenset({"network"}))
        host.attach(_inner_app())
        assert await host.reconcile() == {"network": f"Port {taken} is already in use."}
        assert host.error("network") == f"Port {taken} is already in use."


async def test_the_real_app_behind_the_gate_hands_out_nothing_before_the_code(tmp_path: Path) -> None:
    """The point of the gate: ``GET /`` on the real app carries the API token, so it must not be reachable first."""
    from main import app

    port = _free_port()
    extension, _holder = _extension(tmp_path, port)
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

            code = extension.panel().facts[-1].value
            assert (await http.post(f"{base}/lan-login", data={"code": code})).status_code == 303
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
