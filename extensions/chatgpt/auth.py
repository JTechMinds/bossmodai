"""Extension-owned local OAuth session, with injected I/O and host services."""

from __future__ import annotations
import base64
import hashlib
import hmac
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
import httpx
import jwt
from .capabilities import Capability
from .config import ChatGPTConfig, ChatGPTError
from .store import CredentialStore
from core.extensions.model_provider import ProviderServices
from typing import Callable

PLAN_SCOPE = "chatgpt.tokens.use.direct"
# Stored catalogs carry per-model capability fields from this version on; an
# older stored catalog has slugs and names only until the next refresh.
CATALOG_VERSION = 2


def catalog_entry(account: dict, slug: str | None) -> dict | None:
    """The stored catalog entry for ``slug`` in ``account``, or None when it isn't listed."""
    return next((m for m in account.get("models", []) if m["slug"] == slug), None)


def image_conflicts(account: dict) -> list[str]:
    """Display names whose stored image policy is Enabled but whose catalog says no image input.

    Pure. Publication sends such a model as ``disabled``; status lists these so
    the operator sees the conflict instead of it being resolved silently.
    """
    policies = account.get("image_policies", {})
    return [m["display_name"] for m in account.get("models", [])
            if Capability.from_entry(m).image_conflict(policies.get(m["slug"], "auto"))]


class ChatGPTSession:

    def __init__(
        self,
        config: ChatGPTConfig,
        store: CredentialStore,
        services: ProviderServices,
        probe: Callable[[str, str], None],
        *,
        http_client_factory=httpx.Client,
        jwks_client_factory=jwt.PyJWKClient,
        decode_id_token=jwt.decode,
        open_browser=webbrowser.open,
        clock=time.time,
    ):
        self.config, self.store, self.services, self.probe = (
            config,
            store,
            services,
            probe,
        )
        self.http_client_factory = http_client_factory
        self.jwks_client_factory, self.decode_id_token = (
            jwks_client_factory,
            decode_id_token,
        )
        self.open_browser, self.clock = (open_browser, clock)
        self._pending = None
        self._login_error = None
        self._lock = threading.RLock()

    def require_enabled(self):
        if not self.services.is_enabled():
            raise ChatGPTError("Enable ChatGPT in Extensions first.")

    def account_status(self) -> dict:
        with self.store.locked() as data:
            active = data["accounts"].get(data["active"], {})
            accounts = [
                {
                    "id": key,
                    "label": item.get("email") or item["subject"],
                    "signed_in": bool(item.get("access_token")),
                }
                for key, item in data["accounts"].items()
            ]
            signed_in = bool(active.get("access_token"))
            connected = signed_in and PLAN_SCOPE in active.get("scopes", [])
            if connected and self.services.is_enabled():
                try:
                    # Reconcile local host state on every readiness check; no remote polling.
                    self._ensure_connection(data, data.get("model") or active["models"][0]["slug"])
                    self.store.save(data)
                    self._login_error = None
                except Exception:
                    connected = False
                    self._login_error = "Model publication needs repair. Retry Refresh models."
            connection_id = data.get("connection_id")
            models = active.get("models", [])
            model = data.get("model")
            # The operator's stored choice per model; status shows what is in
            # effect, which a conflict can override.
            policies = dict(active.get("image_policies", {}))
            refreshed = active.get("refreshed_at")
            catalog_version = active.get("catalog_version")
            conflicts = image_conflicts(active)
            email = active.get("email")
            active_id = data["active"]
        with self._lock:
            pending = self._pending is not None
            error = self._login_error
        return {
            "connected": connected,
            "pending": pending,
            "error": error,
            "email": email,
            "accounts": accounts,
            "active_account": active_id,
            "models": models,
            "connection_id": connection_id,
            "model": model,
            "image_policies": policies,
            "refreshed_at": refreshed,
            "catalog_version": catalog_version,
            "image_conflicts": conflicts,
        }

    def _validate_identity(
        self, token: str, client_id: str, nonce: str | None = None
    ) -> dict:
        try:
            jwks = self.jwks_client_factory(
                self.config.issuer + "/.well-known/jwks.json",
                timeout=self.config.request_timeout_seconds,
            )
            key = jwks.get_signing_key_from_jwt(token)
            claims = self.decode_id_token(
                token,
                key.key,
                algorithms=["RS256"],
                audience=client_id,
                issuer=self.config.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
            if nonce is not None and (
                not hmac.compare_digest(str(claims.get("nonce", "")), nonce)
            ):
                raise ChatGPTError(
                    "ChatGPT sign-in could not be verified. Please try again."
                )
            return claims
        except (jwt.PyJWTError, ValueError, OSError) as exc:
            raise ChatGPTError(
                "ChatGPT sign-in could not be verified. Please try again."
            ) from exc

    def _token_request(self, fields: dict) -> dict:
        try:
            with self.http_client_factory(
                timeout=self.config.request_timeout_seconds
            ) as client:
                response = client.post(
                    self.config.token_url,
                    data={**fields, "resource": self.config.resource},
                )
            if response.status_code != 200:
                raise ChatGPTError(
                    "ChatGPT authorization expired or was declined. Sign in again."
                )
            result = response.json()
            if (
                not result.get("access_token")
                or str(result.get("token_type", "")).lower() != "bearer"
            ):
                raise ChatGPTError(
                    "ChatGPT returned an incomplete sign-in. Please try again."
                )
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ChatGPTError(
                "Couldn’t connect to ChatGPT. Check your connection and try again."
            ) from exc

    def _tokens(self, account: dict, tokens: dict) -> dict:
        scopes = tokens.get("scope")
        return {
            **account,
            **{
                key: tokens[key]
                for key in ("access_token", "refresh_token", "id_token")
                if key in tokens
            },
            "scopes": (
                scopes.split() if isinstance(scopes, str) else account.get("scopes", [])
            ),
            "expires_at": self.clock() + float(tokens.get("expires_in", 3600)),
        }

    def access_token(self) -> str:
        self.require_enabled()
        with self.store.locked() as data:
            account = data["accounts"].get(data["active"])
            if not account or not account.get("access_token"):
                raise ChatGPTError(
                    "Sign in to ChatGPT in Extensions to use this connection."
                )
            if PLAN_SCOPE not in account.get("scopes", []):
                raise ChatGPTError(
                    "Allow BossMod to use your ChatGPT plan, then sign in again."
                )
            if account["expires_at"] <= self.clock() + 60:
                if not account.get("refresh_token"):
                    raise ChatGPTError(
                        "Your ChatGPT session expired. Sign in again in Extensions."
                    )
                tokens = self._token_request(
                    {
                        "grant_type": "refresh_token",
                        "client_id": account["client_id"],
                        "refresh_token": account["refresh_token"],
                    }
                )
                updated = self._tokens(account, tokens)
                if PLAN_SCOPE not in updated["scopes"]:
                    raise ChatGPTError(
                        "ChatGPT plan access is no longer enabled. Sign in again."
                    )
                if tokens.get("id_token"):
                    claims = self._validate_identity(
                        tokens["id_token"], account["client_id"]
                    )
                    if claims["sub"] != account["subject"]:
                        raise ChatGPTError(
                            "ChatGPT returned a different account. Sign in again."
                        )
                data["accounts"][data["active"]] = updated
                self.store.save(data)
                account = updated
            return account["access_token"]

    def model_catalog(self, token: str) -> list[dict]:
        try:
            with self.http_client_factory(
                timeout=self.config.request_timeout_seconds
            ) as client:
                response = client.get(
                    self.config.resource + "/models",
                    headers={"Authorization": f"Bearer {token}"},
                )
            response.raise_for_status()
            from .catalog import normalize
            return normalize(response.json())
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ChatGPTError(
                "Couldn’t load models for your ChatGPT account. Try signing in again."
            ) from exc

    def _ensure_connection(self, data, model):
        from core.extensions.model_provider import ManagedModel
        data["model"] = model
        account = data["accounts"][data["active"]]
        # Durable catalog precedes publication; load/status repairs interrupted publication.
        self.store.save(data)
        if self.services.reconcile_connections is None:
            data["connection_id"] = self.services.publish_connection(model)
            return
        policies = account.get("image_policies", {})
        entries = []
        for m in account.get("models", []):
            capability = Capability.from_entry(m)
            # A conflicting Enabled is published as disabled: the host refuses
            # Enabled on an unsupported model for the whole batch.
            entries.append(ManagedModel(
                key=m["slug"], model=m["slug"], name=m["display_name"],
                image_policy=capability.published_image_policy(policies.get(m["slug"], "auto")),
                thinking_levels=capability.thinking_levels(),
                images=capability.images))
        data["connection_ids"] = self.services.reconcile_connections(entries)
        data["connection_id"] = data["connection_ids"].get(model)

    def refresh_models(self):
        self.require_enabled()
        with self._lock:
            token = self.access_token()
            try:
                models = self.model_catalog(token)
                with self.store.locked() as data:
                    account = data["accounts"][data["active"]]
                    account["models"] = models
                    account["catalog_version"] = CATALOG_VERSION
                    account["refreshed_at"] = self.clock()
                    self._ensure_connection(data, data.get("model") or models[0]["slug"])
                    self.store.save(data)
                self._login_error = None
            except Exception:
                self._login_error = "Model refresh failed. Last successful catalog retained; retry Refresh models."
                raise

    def set_image_policy(self, model: str, policy: str) -> None:
        """Store the operator's image-input policy for one model and republish.

        Every model of the active account is republished, so the model's own
        connection carries the new policy at once.

        Args:
            model: A catalog slug of the active account (its ``ProviderModel.key``).
            policy: ``auto``, ``enabled`` or ``disabled``.

        Raises:
            ChatGPTError: For any other policy, a disabled extension, an active
                account that is not signed in, a model its catalog does not
                list, or ``enabled`` on a model ChatGPT says takes no images.
        """
        if policy not in {"auto", "enabled", "disabled"}:
            raise ChatGPTError("Choose Auto, Enabled, or Disabled.")
        self.require_enabled()
        with self.store.locked() as data:
            account = data["accounts"].get(data["active"], {})
            entry = catalog_entry(account, model)
            if not account.get("access_token") or entry is None:
                raise ChatGPTError("Sign in and choose an available model first.")
            if Capability.from_entry(entry).image_conflict(policy):
                raise ChatGPTError("ChatGPT reports this model doesn't accept images.")
            account.setdefault("image_policies", {})[model] = policy
            self._ensure_connection(data, data.get("model") or account["models"][0]["slug"])
            self.store.save(data)

    def validate_request(self, request):
        import json
        token = self.access_token()
        status = self.account_status()
        if not status["connected"]:
            raise ChatGPTError("Model publication needs repair. Retry Refresh models.")
        with self.store.locked() as data:
            account = data["accounts"].get(data["active"], {})
            entry = catalog_entry(account, request.model)
            if entry is None:
                raise ChatGPTError("This model is unavailable in the active ChatGPT account. Refresh models in Extensions.")
            try:
                extra = json.loads(request.extra_body or "{}")
                if not isinstance(extra, dict) or not isinstance(extra.get("reasoning", {}), dict):
                    raise ValueError
            except (ValueError, TypeError):
                raise ChatGPTError("ChatGPT connection options must be a JSON object with valid reasoning options.") from None
            capability = Capability.from_entry(entry)
            reasoning = extra.get("reasoning", {})
            if "effort" in reasoning and reasoning["effort"] not in capability.offered:
                raise ChatGPTError("Explicit thinking effort is not verified for this model. Choose a supported level or Server default.")
            images = capability.images_enabled(account.get("image_policies", {}).get(request.model, "auto"))
            for message in request.messages:
                content = message.get("content")
                if isinstance(content, list) and not images and any(isinstance(part, dict) and part.get("type") in {"image_url", "input_image", "image"} for part in content):
                    raise ChatGPTError("Image input is disabled for this ChatGPT model. Enable it in Extensions to assert support.")
        return token

    def accept_callback(self, query: dict[str, list[str]], attempt: dict) -> None:
        if any((len(values) != 1 for values in query.values())):
            raise ChatGPTError(
                "ChatGPT returned an invalid callback. Please try again."
            )
        if not hmac.compare_digest(query.get("state", [""])[0], attempt["state"]):
            raise ChatGPTError("ChatGPT sign-in state did not match. Please try again.")
        if self.clock() > attempt["expires_at"]:
            raise ChatGPTError("ChatGPT sign-in timed out. Please try again.")
        if query.get("error"):
            raise ChatGPTError(
                "ChatGPT sign-in was cancelled or declined. You can try again."
            )
        client_id = query.get("client_id", [attempt.get("client_id")])[0]
        if not client_id or client_id == "dynamic_agent_client":
            raise ChatGPTError("ChatGPT registration did not finish. Please try again.")
        if attempt.get("client_id") and client_id != attempt["client_id"]:
            raise ChatGPTError(
                "ChatGPT returned a different registration. Please try again."
            )
        code = query.get("code", [""])[0]
        if not code:
            raise ChatGPTError(
                "ChatGPT did not return a sign-in code. Please try again."
            )
        tokens = self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": attempt["verifier"],
                "redirect_uri": attempt["redirect_uri"],
            }
        )
        claims = self._validate_identity(
            tokens.get("id_token", ""), client_id, attempt["nonce"]
        )
        if attempt.get("subject") and claims["sub"] != attempt["subject"]:
            raise ChatGPTError(
                "Sign in with the selected ChatGPT account, or add another account."
            )
        account = self._tokens(
            {
                "client_id": client_id,
                "subject": claims["sub"],
                "email": claims.get("email"),
            },
            tokens,
        )
        with self.store.locked() as data:
            if client_id not in data["accounts"]:
                data["accounts"][client_id] = {
                    "client_id": client_id,
                    "subject": claims["sub"],
                    "email": claims.get("email"),
                }
                self.store.save(data)
        if PLAN_SCOPE not in account["scopes"]:
            raise ChatGPTError(
                "Allow BossMod to use your ChatGPT plan during sign-in, then try again."
            )
        models = self.model_catalog(account["access_token"])
        with self.store.locked() as saved:
            preferred = saved.get("model")
        model = (
            preferred
            if preferred in {item["slug"] for item in models}
            else models[0]["slug"]
        )
        self.probe(account["access_token"], model)
        account["models"] = models
        account["catalog_version"] = CATALOG_VERSION
        account["refreshed_at"] = self.clock()
        self.require_enabled()
        with self.store.locked() as data:
            previous = data["accounts"].get(client_id, {})
            if previous.get("subject") == account["subject"]:
                account["image_policies"] = previous.get("image_policies", {})
            data["accounts"][client_id] = account
            data["active"] = client_id
            self._ensure_connection(data, model)
            self.store.save(data)

    def begin_login(self, account_id: str | None = None) -> dict:
        _session = self
        self.require_enabled()
        with self._lock:
            if self._pending is not None:
                raise ChatGPTError(
                    "A ChatGPT sign-in is already open. Finish it or cancel it first."
                )
            with self.store.locked() as data:
                account = data["accounts"].get(account_id) if account_id else None
                if account_id and account is None:
                    raise ChatGPTError("That saved ChatGPT account no longer exists.")
                host_id = data["host_id"]
            attempt = {
                "state": secrets.token_urlsafe(32),
                "nonce": secrets.token_urlsafe(32),
                "verifier": secrets.token_urlsafe(64),
                "expires_at": self.clock() + self.config.login_timeout_seconds,
                "client_id": account["client_id"] if account else None,
                "subject": account["subject"] if account else None,
            }

            class Callback(BaseHTTPRequestHandler):

                def log_message(self, *args):
                    pass

                def do_GET(self):
                    url = urlsplit(self.path)
                    if url.path != "/auth/callback":
                        self.send_error(404)
                        return
                    query = parse_qs(url.query)
                    with _session._lock:
                        if _session._pending is not attempt or not hmac.compare_digest(
                            query.get("state", [""])[0], attempt["state"]
                        ):
                            self.send_error(400, "Invalid sign-in state")
                            return
                        _session._pending = None
                        attempt["timer"].cancel()
                        try:
                            _session.accept_callback(query, attempt)
                            _session._login_error = None
                            message = "Connected to ChatGPT. You can close this tab and return to BossMod."
                        except ChatGPTError as exc:
                            _session._login_error = str(exc)
                            message = _session._login_error
                        except Exception:
                            _session._login_error = (
                                "ChatGPT sign-in could not finish. Please try again."
                            )
                            message = _session._login_error
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
            attempt["redirect_uri"] = (
                f"http://127.0.0.1:{server.server_port}/auth/callback"
            )
            params = {
                "client_id": attempt["client_id"] or "dynamic_agent_client",
                "ext_agent_host_id": host_id,
                "response_type": "code",
                "redirect_uri": attempt["redirect_uri"],
                "scope": self.config.scopes,
                "resource": self.config.resource,
                "state": attempt["state"],
                "nonce": attempt["nonce"],
                "code_challenge_method": "S256",
                "code_challenge": base64.urlsafe_b64encode(
                    hashlib.sha256(attempt["verifier"].encode()).digest()
                )
                .decode()
                .rstrip("="),
            }
            if account:
                if account.get("id_token"):
                    params["id_token_hint"] = account["id_token"]
                if account.get("email"):
                    params["login_hint"] = account["email"]
            else:
                params["agent_name_hint"] = self.config.agent_name
            self._pending = attempt
            self._login_error = None

            def serve():
                try:
                    server.serve_forever(poll_interval=0.2)
                finally:
                    server.server_close()

            threading.Thread(target=serve, daemon=True).start()
            timer = threading.Timer(
                self.config.login_timeout_seconds, lambda: self.cancel_login(attempt)
            )
            timer.daemon = True
            timer.start()
            attempt["timer"] = timer
            try:
                opened = self.open_browser(
                    self.config.authorize_url + "?" + urlencode(params)
                )
            except (webbrowser.Error, OSError):
                opened = False
            if not opened:
                self.cancel_login(attempt)
                raise ChatGPTError(
                    "Couldn’t open your system browser. Set a default browser and try again."
                )
        return self.account_status()

    def cancel_login(self, expected=None) -> None:
        with self._lock:
            if self._pending is None or (
                expected is not None and self._pending is not expected
            ):
                return
            attempt, self._pending = (self._pending, None)
            attempt["timer"].cancel()
            attempt["server"].shutdown()

    def sign_out(self) -> dict:
        self.cancel_login()
        revoked = True
        with self.store.locked() as data:
            account = data["accounts"].get(data["active"], {})
            if account.get("refresh_token"):
                try:
                    with self.http_client_factory(
                        timeout=self.config.request_timeout_seconds
                    ) as client:
                        discovery = client.get(
                            self.config.issuer + "/.well-known/openid-configuration"
                        )
                        discovery.raise_for_status()
                        endpoint = discovery.json()["revocation_endpoint"]
                        if (
                            urlsplit(endpoint).scheme != "https"
                            or urlsplit(endpoint).netloc
                            != urlsplit(self.config.issuer).netloc
                        ):
                            raise ValueError("Untrusted revocation endpoint")
                        result = client.post(
                            endpoint,
                            data={
                                "token": account["refresh_token"],
                                "token_type_hint": "refresh_token",
                                "client_id": account["client_id"],
                            },
                        )
                        revoked = result.status_code == 200
                except (httpx.HTTPError, ValueError, KeyError):
                    revoked = False
            for key in ("access_token", "refresh_token", "id_token", "expires_at"):
                account.pop(key, None)
            self.store.save(data)
            if self.services.reconcile_connections is not None and self.services.is_enabled():
                self.services.reconcile_connections([])
        return {
            "revoked": revoked,
            "message": (
                "Signed out."
                if revoked
                else "Signed out locally. Remote revocation wasn’t confirmed; disconnect BossMod in ChatGPT Settings."
            ),
        }
