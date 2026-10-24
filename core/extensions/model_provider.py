"""Optional model-provider contract and generic host dispatch.

The host owns connection rows, call budgets and deadlines. Extensions own
accounts, authentication, model transports, and the declarative operator UI.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field


class ProviderError(Exception):
    """Safe operator-facing provider failure (never include credentials)."""


class ProviderTimeout(ProviderError):
    def __init__(self, seconds: float, kind: str):
        self.seconds, self.kind = seconds, kind
        super().__init__(f"Model request timed out ({kind}).")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderOption(_Strict):
    value: str
    label: str


class ProviderAction(_Strict):
    key: str
    label: str
    values: dict[str, str] = Field(default_factory=dict)


class ProviderChoice(_Strict):
    label: str
    value: str
    options: list[ProviderOption]
    action: str
    parameter: str


class ProviderStatus(_Strict):
    connected: bool = False
    pending: bool = False
    message: str
    error: str | None = None
    actions: list[ProviderAction] = Field(default_factory=list)
    choices: list[ProviderChoice] = Field(default_factory=list)


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    messages: list[dict[str, Any]]
    extra_body: str | None = None
    timeout_seconds: float = 30
    stall_seconds: float = 0
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class ProviderCompletion:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = "stop"


@dataclass(frozen=True)
class ProviderServices:
    """Host operations scoped to one extension; injected by the loader."""

    publish_connection: Callable[[str], str]
    is_enabled: Callable[[], bool]
    seal: Callable[[str], str]
    unseal: Callable[[str], str]


@runtime_checkable
class SupportsModelProvider(Protocol):
    def provider_status(self) -> ProviderStatus: ...
    def provider_action(self, action: str, values: Mapping[str, str]) -> None: ...
    async def provider_complete(
        self, request: ProviderRequest
    ) -> ProviderCompletion: ...
    def provider_complete_sync(
        self, request: ProviderRequest
    ) -> ProviderCompletion: ...


def provider_id(api_base: str | None) -> str | None:
    """Recognize only the host's extension routing scheme, never vendor URLs."""
    if not api_base or not api_base.startswith("extension:"):
        return None
    from core.extensions.manifest import _ID_PATTERN
    import re

    url = urlsplit(api_base)
    if (
        url.scheme != "extension"
        or url.path
        or url.query
        or url.fragment
        or not re.fullmatch(_ID_PATTERN, url.netloc)
    ):
        raise ProviderError("Invalid extension connection.")
    return url.netloc


def services_for(entry) -> ProviderServices:
    """Bind minimal host services to a validated provider extension."""
    import db
    from db.secret_store import encrypt_secret, decrypt_secret
    from core.extensions.registry import is_enabled

    spec = entry.manifest.model_provider
    endpoint = f"extension://{entry.id}"

    def publish(model: str) -> str:
        if not is_enabled(entry.id):
            raise ProviderError("Enable this extension before connecting a model.")
        connection = next(
            (c for c in db.list_connections() if c.api_base_url == endpoint), None
        )
        if connection is None:
            connection = db.create_connection(
                name=spec.connection_name, api_base_url=endpoint, model=model
            )
        else:
            db.update_connection(connection.id, model=model)
        return connection.id

    return ProviderServices(
        publish, lambda: is_enabled(entry.id), encrypt_secret, decrypt_secret
    )


def resolve_provider(ext_id: str) -> SupportsModelProvider:
    from core.extensions.loader import ExtensionLoadError, load_extension
    from core.extensions.registry import get_discovery, is_enabled

    entry = get_discovery().get(ext_id)
    if entry is None or not entry.valid or entry.manifest.model_provider is None:
        raise ProviderError("This model-provider extension is missing or unavailable.")
    if not is_enabled(ext_id):
        raise ProviderError(
            f"Enable {entry.manifest.name} in Extensions to use this connection."
        )
    try:
        provider = load_extension(entry)
    except ExtensionLoadError:
        raise ProviderError(
            f"Couldn’t load {entry.manifest.name}. Check Extensions."
        ) from None
    if not isinstance(provider, SupportsModelProvider):
        raise ProviderError(
            "This extension does not implement its model-provider contract."
        )
    return provider


async def complete(
    provider: SupportsModelProvider, request: ProviderRequest
) -> ProviderCompletion:
    """One generic budget/deadline wrapper for any extension provider."""
    from core.llm.call_budget import budget, current_turn_lane

    owned = None
    if current_turn_lane() is None:
        owned = await budget.acquire(kind="call", owner="extension-provider")
    try:
        async with asyncio.timeout(request.timeout_seconds):
            return await provider.provider_complete(request)
    except TimeoutError:
        raise ProviderTimeout(request.timeout_seconds, "backstop") from None
    finally:
        if owned is not None:
            budget.release(owned)
