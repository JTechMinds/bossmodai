"""Official local-app OAuth flow, loopback callback, and renewable sessions.

No client secret, borrowed Codex credentials, or ChatGPT browser cookies.
The loopback listener is separate from the token-protected BossMod API.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from core.chatgpt import AUTHORIZE_URL, ChatGPTError, EXTENSION_ID, ISSUER, RESOURCE, SCOPES, TOKEN_URL
from core.chatgpt.store import locked_store, save_store

PLAN_SCOPE = "chatgpt.tokens.use.direct"
_LOGIN_LOCK = threading.RLock()
_pending = None
_login_error = None


def require_enabled() -> None:
    from core.extensions.registry import enabled_ids
    if EXTENSION_ID not in enabled_ids():
        raise ChatGPTError("Enable ChatGPT in Extensions first.")


def status() -> dict:
    with locked_store() as data:
        active = data["accounts"].get(data["active"], {})
        accounts = [
            {"id": key, "label": item.get("email") or item["subject"], "signed_in": bool(item.get("access_token"))}
            for key, item in data["accounts"].items()
        ]
        signed_in = bool(active.get("access_token"))
        connected = signed_in and PLAN_SCOPE in active.get("scopes", [])
        connection_id = data.get("connection_id")
        models = active.get("models", [])
        model = data.get("model")
        email = active.get("email")
        active_id = data["active"]
    with _LOGIN_LOCK:
        pending = _pending is not None
        error = _login_error
    return {"connected": connected, "pending": pending, "error": error, "email": email,
            "accounts": accounts, "active_account": active_id, "models": models,
            "connection_id": connection_id, "model": model}


def _validate_identity(token: str, client_id: str, nonce: str | None = None) -> dict:
    # Algorithm allowlist and all claims are verified, never decode-only.
    try:
        jwks = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", timeout=15)
        key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUER,
                            options={"require": ["exp", "iss", "aud", "sub"]})
        if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ChatGPTError("ChatGPT sign-in could not be verified. Please try again.")
        return claims
    except (jwt.PyJWTError, ValueError, OSError) as exc:
        raise ChatGPTError("ChatGPT sign-in could not be verified. Please try again.") from exc


def _token_request(fields: dict) -> dict:
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(TOKEN_URL, data={**fields, "resource": RESOURCE})
        if response.status_code != 200:
            raise ChatGPTError("ChatGPT authorization expired or was declined. Sign in again.")
        result = response.json()
        if not result.get("access_token") or str(result.get("token_type", "")).lower() != "bearer":
            raise ChatGPTError("ChatGPT returned an incomplete sign-in. Please try again.")
        return result
    except (httpx.HTTPError, ValueError) as exc:
        raise ChatGPTError("Couldn’t connect to ChatGPT. Check your connection and try again.") from exc


def _tokens(account: dict, tokens: dict) -> dict:
    scopes = tokens.get("scope")
    return {**account, **{key: tokens[key] for key in ("access_token", "refresh_token", "id_token") if key in tokens},
            "scopes": scopes.split() if isinstance(scopes, str) else account.get("scopes", []),
            "expires_at": time.time() + float(tokens.get("expires_in", 3600))}


def access_token() -> str:
    require_enabled()
    with locked_store() as data:
        account = data["accounts"].get(data["active"])
        if not account or not account.get("access_token"):
            raise ChatGPTError("Sign in to ChatGPT in Extensions to use this connection.")
        if PLAN_SCOPE not in account.get("scopes", []):
            raise ChatGPTError("Allow BossMod to use your ChatGPT plan, then sign in again.")
        if account["expires_at"] <= time.time() + 60:
            if not account.get("refresh_token"):
                raise ChatGPTError("Your ChatGPT session expired. Sign in again in Extensions.")
            tokens = _token_request({"grant_type": "refresh_token", "client_id": account["client_id"],
                                     "refresh_token": account["refresh_token"]})
            # Serialize rotating refreshes, and replace all token fields together.
            updated = _tokens(account, tokens)
            if PLAN_SCOPE not in updated["scopes"]:
                raise ChatGPTError("ChatGPT plan access is no longer enabled. Sign in again.")
            if tokens.get("id_token"):
                claims = _validate_identity(tokens["id_token"], account["client_id"])
                if claims["sub"] != account["subject"]:
                    raise ChatGPTError("ChatGPT returned a different account. Sign in again.")
            data["accounts"][data["active"]] = updated
            save_store(data)
            account = updated
        return account["access_token"]


def model_catalog(token: str) -> list[dict]:
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(RESOURCE + "/models", headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        models = [{"slug": item["slug"], "display_name": item.get("display_name") or item["slug"]}
                  for item in response.json().get("models", [])
                  if item.get("visibility") == "list" and isinstance(item.get("slug"), str)]
        if not models:
            raise ChatGPTError("No ChatGPT models are available for this account or workspace.")
        return models
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise ChatGPTError("Couldn’t load models for your ChatGPT account. Try signing in again.") from exc


def _ensure_connection(data: dict, model: str) -> None:
    import db
    from core.chatgpt import API_BASE
    data["model"] = model
    connection = db.get_connection_by_id(data.get("connection_id", ""))
    if connection is None:
        connection = next((item for item in db.list_connections() if item.api_base_url == API_BASE), None)
        if connection is not None:
            data["connection_id"] = connection.id
    if connection is not None and connection.api_base_url == API_BASE:
        db.update_connection(connection.id, model=model)
    else:
        connection = db.create_connection(name="Chat GPT", api_base_url=API_BASE, model=model)
        data["connection_id"] = connection.id


def accept_callback(query: dict[str, list[str]], attempt: dict) -> None:
    if any(len(values) != 1 for values in query.values()):
        raise ChatGPTError("ChatGPT returned an invalid callback. Please try again.")
    if not hmac.compare_digest(query.get("state", [""])[0], attempt["state"]):
        raise ChatGPTError("ChatGPT sign-in state did not match. Please try again.")
    if time.time() > attempt["expires_at"]:
        raise ChatGPTError("ChatGPT sign-in timed out. Please try again.")
    if query.get("error"):
        raise ChatGPTError("ChatGPT sign-in was cancelled or declined. You can try again.")
    client_id = query.get("client_id", [attempt.get("client_id")])[0]
    if not client_id or client_id == "dynamic_agent_client":
        raise ChatGPTError("ChatGPT registration did not finish. Please try again.")
    if attempt.get("client_id") and client_id != attempt["client_id"]:
        raise ChatGPTError("ChatGPT returned a different registration. Please try again.")
    code = query.get("code", [""])[0]
    if not code:
        raise ChatGPTError("ChatGPT did not return a sign-in code. Please try again.")
    tokens = _token_request({"grant_type": "authorization_code", "client_id": client_id, "code": code,
                             "code_verifier": attempt["verifier"], "redirect_uri": attempt["redirect_uri"]})
    claims = _validate_identity(tokens.get("id_token", ""), client_id, attempt["nonce"])
    if attempt.get("subject") and claims["sub"] != attempt["subject"]:
        raise ChatGPTError("Sign in with the selected ChatGPT account, or add another account.")
    account = _tokens({"client_id": client_id, "subject": claims["sub"], "email": claims.get("email")}, tokens)
    # Keep a verified registration even if plan access or the probe fails.
    # It must be reused on retry, without replacing an already-active account.
    with locked_store() as data:
        if client_id not in data["accounts"]:
            data["accounts"][client_id] = {"client_id": client_id, "subject": claims["sub"], "email": claims.get("email")}
            save_store(data)
    if PLAN_SCOPE not in account["scopes"]:
        raise ChatGPTError("Allow BossMod to use your ChatGPT plan during sign-in, then try again.")
    models = model_catalog(account["access_token"])
    # Catalog visibility isn't proof of inference entitlement. Complete a tiny
    # request before advertising this connection as ready.
    from core.chatgpt.provider import probe
    with locked_store() as saved:
        preferred = saved.get("model")
    model = preferred if preferred in {item["slug"] for item in models} else models[0]["slug"]
    probe(account["access_token"], model)
    account["models"] = models
    require_enabled()
    with locked_store() as data:
        data["accounts"][client_id] = account
        data["active"] = client_id
        _ensure_connection(data, model)
        save_store(data)


def begin_login(account_id: str | None = None) -> dict:
    global _pending, _login_error
    require_enabled()
    with _LOGIN_LOCK:
        if _pending is not None:
            raise ChatGPTError("A ChatGPT sign-in is already open. Finish it or cancel it first.")
        with locked_store() as data:
            account = data["accounts"].get(account_id) if account_id else None
            if account_id and account is None:
                raise ChatGPTError("That saved ChatGPT account no longer exists.")
            host_id = data["host_id"]
        attempt = {"state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
                   "verifier": secrets.token_urlsafe(64), "expires_at": time.time() + 600,
                   "client_id": account["client_id"] if account else None,
                   "subject": account["subject"] if account else None}

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # The query carries an authorization code: never log it.

            def do_GET(self):
                global _pending, _login_error
                url = urlsplit(self.path)
                if url.path != "/auth/callback":
                    self.send_error(404)
                    return
                query = parse_qs(url.query)
                # Wrong-state requests must not consume a legitimate attempt.
                with _LOGIN_LOCK:
                    if _pending is not attempt or not hmac.compare_digest(query.get("state", [""])[0], attempt["state"]):
                        self.send_error(400, "Invalid sign-in state")
                        return
                    _pending = None  # One-time consumption, including failures.
                    attempt["timer"].cancel()
                    try:
                        accept_callback(query, attempt)
                        _login_error = None
                        message = "Connected to ChatGPT. You can close this tab and return to BossMod."
                    except ChatGPTError as exc:
                        _login_error = str(exc)
                        message = _login_error
                    except Exception:
                        _login_error = "ChatGPT sign-in could not finish. Please try again."
                        message = _login_error
                payload = message.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                finally:
                    threading.Thread(target=server.shutdown, daemon=True).start()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
        server.daemon_threads = True
        attempt["server"] = server
        attempt["redirect_uri"] = f"http://127.0.0.1:{server.server_port}/auth/callback"
        params = {"client_id": attempt["client_id"] or "dynamic_agent_client", "ext_agent_host_id": host_id,
                  "response_type": "code", "redirect_uri": attempt["redirect_uri"], "scope": SCOPES,
                  "resource": RESOURCE, "state": attempt["state"], "nonce": attempt["nonce"],
                  "code_challenge_method": "S256",
                  "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(attempt["verifier"].encode()).digest()).decode().rstrip("=")}
        if account:
            if account.get("id_token"):
                params["id_token_hint"] = account["id_token"]
            if account.get("email"):
                params["login_hint"] = account["email"]
        else:
            params["agent_name_hint"] = "BossMod AI"
        _pending = attempt
        _login_error = None
        def serve():
            try:
                server.serve_forever(poll_interval=0.2)
            finally:
                server.server_close()
        threading.Thread(target=serve, daemon=True).start()
        timer = threading.Timer(600, lambda: cancel_login(attempt))
        timer.daemon = True
        timer.start()
        attempt["timer"] = timer
        try:
            opened = webbrowser.open(AUTHORIZE_URL + "?" + urlencode(params))
        except (webbrowser.Error, OSError):
            opened = False
        if not opened:
            cancel_login(attempt)
            raise ChatGPTError("Couldn’t open your system browser. Set a default browser and try again.")
    return status()


def cancel_login(expected=None) -> None:
    global _pending
    with _LOGIN_LOCK:
        if _pending is None or (expected is not None and _pending is not expected):
            return
        attempt, _pending = _pending, None
        attempt["timer"].cancel()
        attempt["server"].shutdown()


def select_model(model: str) -> None:
    require_enabled()
    token = access_token()
    probe_model = model
    # Only probe offered models, before committing a different model to agents.
    with locked_store() as data:
        account = data["accounts"].get(data["active"], {})
        if model not in {item["slug"] for item in account.get("models", [])}:
            raise ChatGPTError("Choose a model offered by your connected ChatGPT account.")
    from core.chatgpt.provider import probe
    probe(token, probe_model)
    with locked_store() as data:
        account = data["accounts"].get(data["active"], {})
        if not account.get("access_token") or model not in {item["slug"] for item in account.get("models", [])}:
            raise ChatGPTError("Choose a model offered by your connected ChatGPT account.")
        _ensure_connection(data, model)
        save_store(data)


def sign_out() -> dict:
    cancel_login()
    revoked = True
    with locked_store() as data:
        account = data["accounts"].get(data["active"], {})
        if account.get("refresh_token"):
            try:
                with httpx.Client(timeout=15) as client:
                    discovery = client.get(ISSUER + "/.well-known/openid-configuration")
                    discovery.raise_for_status()
                    endpoint = discovery.json()["revocation_endpoint"]
                    if urlsplit(endpoint).scheme != "https" or urlsplit(endpoint).netloc != "auth.openai.com":
                        raise ValueError("Untrusted revocation endpoint")
                    result = client.post(endpoint, data={"token": account["refresh_token"],
                        "token_type_hint": "refresh_token", "client_id": account["client_id"]})
                    revoked = result.status_code == 200
            except (httpx.HTTPError, ValueError, KeyError):
                revoked = False
        for key in ("access_token", "refresh_token", "id_token", "expires_at"):
            account.pop(key, None)
        save_store(data)
    return {"revoked": revoked, "message": "Signed out." if revoked else
            "Signed out locally. Remote revocation wasn’t confirmed; disconnect BossMod in ChatGPT Settings."}
