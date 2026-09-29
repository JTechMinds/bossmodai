"""Microsoft 365 Mailbox: plain-language errors when verifying a mailbox on save."""

from __future__ import annotations

import importlib
import logging

import httpx
import pytest

from core.extensions.contract import AgentConfigError, ExtensionContext
from core.extensions.loader import import_package
from core.extensions.registry import get_discovery

_ENTRY = get_discovery().get("ms365-mail")
_PACKAGE = import_package(_ENTRY)
auth = importlib.import_module(f"{_PACKAGE.__name__}.auth")
graph = importlib.import_module(f"{_PACKAGE.__name__}.graph")

SECRET = "s3cr3t-Value~with.symbols"
MAILBOX = "reports@contoso.com"
CONFIG = {"tenant_id": "contoso.onmicrosoft.com", "client_id": "client-1", "client_secret": SECRET, "mailbox": MAILBOX}

TENANT = "We couldn't find that Microsoft 365 tenant. Check the Tenant ID."
APP = "We couldn't find that app in your tenant. Check the Client ID."
BAD_SECRET = "The client secret is wrong or has expired. Create a new secret and try again."
DENIED = "The app doesn't have permission to open this mailbox."
NO_MAILBOX = "We couldn't find that mailbox. Check the address."
UNREACHABLE = "Couldn't reach Microsoft 365. Check your internet connection and try again."
GENERIC = "Access to the mailbox failed. Check the details and try again."


def _auth(code: int) -> Exception:
    return auth.GraphAuthError(f"AADSTS{code}: raw Azure text", (code,))


@pytest.mark.parametrize("exc, expected", [
    (_auth(90002), TENANT),
    (_auth(900023), TENANT),
    (_auth(700016), APP),
    (_auth(7000215), BAD_SECRET),
    (_auth(7000222), BAD_SECRET),
    (graph.GraphHttpError(401, "InvalidAuthenticationToken", "raw"), DENIED),
    (graph.GraphHttpError(403, "ErrorAccessDenied", "raw"), DENIED),
    (graph.GraphHttpError(404, "ErrorInvalidUser", "raw"), NO_MAILBOX),
    (auth.GraphUnreachable("name resolution failed"), UNREACHABLE),
    (graph.GraphHttpError(429, "TooManyRequests", "raw", retry_after=5), GENERIC),
    (graph.GraphHttpError(503, "ServiceUnavailable", "raw"), GENERIC),
    (graph.GraphHttpError(200, "UnreadableResponse", "the response is not JSON"), GENERIC),
    (auth.GraphAuthError("token endpoint returned HTTP 200 with a body that is not JSON"), GENERIC),
])
def test_each_failure_maps_to_its_sentence(exc, expected) -> None:
    assert graph.friendly_config_error(exc) == expected


def test_an_unknown_aadsts_code_gives_the_generic_sentence() -> None:
    assert graph.friendly_config_error(_auth(50034)) == GENERIC


def _extension(tmp_path, handler):
    ctx = ExtensionContext(manifest=_ENTRY.manifest, data_dir=tmp_path, read_agent_config=lambda agent_id: CONFIG)
    return _PACKAGE.Ms365MailExtension(ctx, transport=httpx.MockTransport(handler))


def test_verify_shows_the_sentence_and_logs_the_detail_without_the_secret(tmp_path, caplog) -> None:
    description = f"AADSTS90002: Tenant 'contoso.onmicrosoft.com' not found. Echo: {SECRET}"

    def handler(request: httpx.Request) -> httpx.Response:
        # The real token path: Azure's error body goes through RestTokenProvider.
        return httpx.Response(400, json={"error": "invalid_request", "error_description": description, "error_codes": [90002]})

    extension = _extension(tmp_path, handler)
    with caplog.at_level(logging.WARNING, logger=_PACKAGE.__name__):
        with pytest.raises(AgentConfigError) as caught:
            extension.verify_agent_config(CONFIG)

    message = str(caught.value)
    assert message == TENANT
    assert SECRET not in message and "AADSTS" not in message and "not found." not in message
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING and record.name == _PACKAGE.__name__]
    assert len(warnings) == 1
    logged = warnings[0].getMessage()
    assert "AADSTS90002" in logged and "90002" in logged and MAILBOX in logged
    assert SECRET not in logged


def test_verify_logs_the_graph_status_and_code_for_an_inbox_failure(tmp_path, caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(404, json={"error": {"code": "ErrorInvalidUser", "message": "The requested user is invalid."}})

    extension = _extension(tmp_path, handler)
    with caplog.at_level(logging.WARNING, logger=_PACKAGE.__name__):
        with pytest.raises(AgentConfigError, match=f"^{NO_MAILBOX}$"):
            extension.verify_agent_config(CONFIG)
    logged = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("HTTP 404 ErrorInvalidUser" in line for line in logged)
