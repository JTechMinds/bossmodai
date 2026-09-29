"""Microsoft 365 Mailbox: client-credentials tokens over plain REST (auth.py)."""

from __future__ import annotations

import importlib
from urllib.parse import parse_qs

import httpx
import pytest

from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_ENTRY = get_discovery().get("ms365-mail")
_PACKAGE = import_package(_ENTRY)
auth = importlib.import_module(f"{_PACKAGE.__name__}.auth")

SECRET = "s3cr3t-Value~with.symbols"
AUTHORITY = "https://login.example.test"
SCOPE = "https://graph.example.test/.default"


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _provider(handler, clock=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return auth.RestTokenProvider(client, AUTHORITY, SCOPE, refresh_margin_s=300, clock=clock or _Clock())


def _ok(expires_in=3600):
    return httpx.Response(200, json={"access_token": "header.payload.sig", "expires_in": expires_in, "token_type": "Bearer"})


def test_the_form_fields_are_posted_to_the_tenant_token_endpoint() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    token = _provider(handler).acquire("contoso.onmicrosoft.com", "client-1", SECRET)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"{AUTHORITY}/contoso.onmicrosoft.com/oauth2/v2.0/token"
    form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
    assert form == {"grant_type": "client_credentials", "client_id": "client-1", "client_secret": SECRET, "scope": SCOPE}
    assert token.token == "header.payload.sig"


def test_a_token_is_cached_until_the_refresh_margin_then_fetched_again() -> None:
    calls = []
    clock = _Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _ok(expires_in=3600)

    provider = _provider(handler, clock)
    first = provider.acquire("t", "c", SECRET)
    clock.now += 3600 - 300 - 1  # one second before the margin
    assert provider.acquire("t", "c", SECRET) is first and len(calls) == 1
    clock.now += 1  # at the margin
    provider.acquire("t", "c", SECRET)
    assert len(calls) == 2


def test_each_credential_triple_has_its_own_cache_entry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _ok()

    provider = _provider(handler)
    provider.acquire("t", "c", SECRET)
    provider.acquire("t", "c", SECRET + "x")
    provider.acquire("t", "other", SECRET)
    assert len(calls) == 3


def test_an_aadsts_error_is_raised_with_its_description_and_never_the_secret() -> None:
    description = f"AADSTS7000215: Invalid client secret provided. Echo: {SECRET}\r\nTrace ID: abc"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client", "error_description": description})

    with pytest.raises(auth.GraphAuthError) as caught:
        _provider(handler).acquire("t", "c", SECRET)
    message = str(caught.value)
    assert message.startswith("AADSTS7000215: Invalid client secret provided.")
    assert SECRET not in message and "[secret]" in message


@pytest.mark.parametrize("response, expected", [
    (httpx.Response(200, text="<html>not json</html>"), "not JSON"),
    (httpx.Response(200, json={"expires_in": 3600}), "without an access_token"),
    (httpx.Response(200, json={"access_token": "a.b.c"}), "without a usable expires_in"),
    (httpx.Response(200, json={"access_token": "a.b.c", "expires_in": 0}), "without a usable expires_in"),
    (httpx.Response(500, json={}), "no error description"),
])
def test_a_malformed_answer_is_an_auth_error_naming_what_is_missing(response, expected) -> None:
    with pytest.raises(auth.GraphAuthError, match=expected):
        _provider(lambda request: response).acquire("t", "c", SECRET)


def test_azure_error_codes_are_parsed_onto_the_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={
            "error": "invalid_request",
            "error_description": "AADSTS90002: Tenant 'nope' not found.",
            "error_codes": [90002],
        })

    with pytest.raises(auth.GraphAuthError) as caught:
        _provider(handler).acquire("nope", "c", SECRET)
    assert caught.value.codes == (90002,)


@pytest.mark.parametrize("error_codes", [None, "90002", [True, "x"]])
def test_absent_or_malformed_error_codes_give_no_codes(error_codes) -> None:
    body = {"error": "invalid_request", "error_description": "AADSTS90002: Tenant 'nope' not found."}
    if error_codes is not None:
        body["error_codes"] = error_codes

    with pytest.raises(auth.GraphAuthError) as caught:
        _provider(lambda request: httpx.Response(400, json=body)).acquire("nope", "c", SECRET)
    assert caught.value.codes == ()


def test_a_transport_failure_is_unreachable_without_the_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed", request=request)

    with pytest.raises(auth.GraphUnreachable) as caught:
        _provider(handler).acquire("t", "c", SECRET)
    assert "name resolution failed" in str(caught.value) and SECRET not in str(caught.value)
