"""Optional model-provider contract and generic host dispatch.

The host owns connection rows, call budgets and deadlines. Extensions own
accounts, authentication, model transports, and what the operator is told.

A provider describes itself to the operator through ``ProviderStatus``: one
plain sentence about the account state, the saved accounts and the actions
that act on them, and the models it publishes — each with what it can do and
its one operator setting, image input — with the actions that act on the
list. The host renders that structure as the extension's Account and Models
sections without knowing any provider, and posts the operator's choices back
through ``provider_action``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Mapping, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from core.models.thinking import ThinkingLevel, ThinkingLevels, parse_thinking_levels

# Whether a model accepts image input, as its provider reports it.
ImageSupport = Literal["unknown", "supported", "unsupported"]
# The operator's image-input setting for one model. ``auto`` follows
# ImageSupport (on only when ``supported``).
ImagePolicy = Literal["auto", "enabled", "disabled"]

IMAGE_POLICY_ACTION = "image_policy"
"""The provider action the host posts when the operator changes one model's image input.

Its values are ``{"model": <ProviderModel.key>, "policy": <ImagePolicy>}``.
Every model provider's ``provider_action`` must accept it, refusing an unknown
model or a policy outside that model's ``image_choices`` with a
``ProviderError``. The host UI mirrors this name (``IMAGE_POLICY_ACTION`` in
ui/static/js/extensions/provider-sections.js).
"""


class ProviderError(Exception):
    """Safe operator-facing provider failure (never include credentials)."""


class ProviderTimeout(ProviderError):
    def __init__(self, seconds: float, kind: str):
        self.seconds, self.kind = seconds, kind
        super().__init__(f"Model request timed out ({kind}).")


class ProviderUsageLimitError(ProviderError):
    """The provider account has no usage left for this request.

    Raised by a provider when its service rejects a request because the
    account's usage allowance is exhausted. The host must stop the work that
    made the request and tell the operator; it never retries it, because no
    retry can succeed until the operator's allowance resets or changes.

    A provider must not raise it for temporary unavailability (an overloaded
    or briefly unreachable service); that stays a plain ``ProviderError``.
    The message is operator-facing and must never include credentials.
    """


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderAction(_Strict):
    """One button the operator may press; the host posts it back unchanged.

    Attributes:
        key: The action name ``provider_action`` receives.
        label: The button's text, in plain language.
        values: The parameters ``provider_action`` receives with ``key``.
        primary: Draw this as the section's one primary button (e.g. the
            first sign-in). ``ProviderStatus`` allows at most one among its
            ``actions``.
    """

    key: str
    label: str
    values: dict[str, str] = Field(default_factory=dict)
    primary: bool = False


class ProviderAccount(_Strict):
    """One account the provider has saved, as the operator recognizes it.

    Attributes:
        label: What the operator knows the account by, such as its email.
            Never an internal id (a client id, a subject): two accounts with
            the same label are told apart by the provider, e.g. a " (2)".
        active: The account the provider's models run on. At most one is.
        signed_in: Whether the account holds a usable sign-in.
        action: The row's one button, such as "Switch" or "Sign in again";
            None for the active account when it is signed in.
    """

    label: str = Field(min_length=1)
    active: bool
    signed_in: bool
    action: ProviderAction | None = None


class ProviderModel(_Strict):
    """One model the provider publishes, and what the operator may set on it.

    Attributes:
        key: The ``ManagedModel.key`` it is published under; sent back as
            ``values["model"]`` with ``IMAGE_POLICY_ACTION``.
        name: Its display name.
        thinking: The host thinking levels it offers, in host order.
        thinking_default: The level the provider's own default uses, when the
            provider says.
        images: Whether it accepts image input.
        image_policy: The image-input policy in effect, which may differ from
            what the operator stored when the provider overrides it.
        image_choices: The policies the operator may pick now; empty means
            image input is read-only for this model.
        note: One or two plain sentences about this model, or None.

    Raises:
        pydantic.ValidationError: When ``image_choices`` repeats a policy, or
            is non-empty and does not include ``image_policy``.
    """

    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    thinking: list[ThinkingLevel] = Field(default_factory=list)
    thinking_default: ThinkingLevel | None = None
    images: ImageSupport
    image_policy: ImagePolicy
    image_choices: list[ImagePolicy] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _choices_hold_the_policy(self) -> "ProviderModel":
        if len(set(self.image_choices)) != len(self.image_choices):
            raise ValueError("image_choices must not repeat a policy")
        if self.image_choices and self.image_policy not in self.image_choices:
            raise ValueError(
                f"image_policy {self.image_policy!r} is not one of image_choices"
            )
        return self


class ProviderStatus(_Strict):
    """What a model provider shows the operator: its account and its models.

    Attributes:
        connected: An account is signed in and its models are usable.
        pending: A sign-in is open and waiting on the operator.
        message: One short plain sentence about the account state.
        error: Why the last operation failed, or None.
        actions: Account-level actions: sign in, add another account, cancel
            a sign-in, sign out.
        accounts: The saved accounts, in the provider's order.
        models: The models the provider publishes.
        models_updated_at: When the models were last loaded (timezone-aware
            UTC), or None when they never were.
        model_actions: Actions on the model list, such as refreshing it.

    Raises:
        pydantic.ValidationError: When two models share a key, more than one
            account is active, more than one of ``actions`` is primary, or
            ``models_updated_at`` is not UTC.
    """

    connected: bool = False
    pending: bool = False
    message: str
    error: str | None = None
    actions: list[ProviderAction] = Field(default_factory=list)
    accounts: list[ProviderAccount] = Field(default_factory=list)
    models: list[ProviderModel] = Field(default_factory=list)
    models_updated_at: AwareDatetime | None = None
    model_actions: list[ProviderAction] = Field(default_factory=list)

    @field_validator("models_updated_at")
    @classmethod
    def _utc(cls, value):
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("models_updated_at must be in UTC")
        return value

    @model_validator(mode="after")
    def _one_of_each(self) -> "ProviderStatus":
        keys = [model.key for model in self.models]
        if len(set(keys)) != len(keys):
            raise ValueError("model keys must be unique")
        if sum(account.active for account in self.accounts) > 1:
            raise ValueError("at most one account may be active")
        if sum(action.primary for action in self.actions) > 1:
            raise ValueError("at most one action may be primary")
        return self


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


class ManagedModel(_Strict):
    """One model a provider publishes as a host connection.

    ``thinking_levels`` uses the host vocabulary only. Connection reads
    re-validate the stored map, so an unknown level key is refused here, at
    the contract boundary, rather than stored where it would break every
    read of that provider's connections.
    """

    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    thinking_levels: ThinkingLevels = Field(default_factory=dict)
    images: ImageSupport = "unknown"
    image_policy: ImagePolicy = "auto"
    available: bool = True
    reason: str | None = None

    @field_validator("thinking_levels", mode="before")
    @classmethod
    def _host_levels(cls, value: Any) -> ThinkingLevels:
        # None and {} both mean "no levels offered"; the parser raises on
        # unknown keys and empty fragments.
        return parse_thinking_levels(value) or {}


@dataclass(frozen=True)
class ProviderServices:
    """Host operations scoped to one extension; injected by the loader."""

    publish_connection: Callable[[str], str]
    is_enabled: Callable[[], bool]
    seal: Callable[[str], str]
    unseal: Callable[[str], str]
    reconcile_connections: Callable[[list[ManagedModel]], dict[str, str]] | None = None


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

    def reconcile(entries):
        if not is_enabled(entry.id):
            raise ProviderError("Enable this extension before refreshing models.")
        entries = [ManagedModel.model_validate(item) for item in entries]
        if len({item.key for item in entries}) != len(entries):
            raise ProviderError("Duplicate managed model keys.")
        if any(item.images == "unsupported" and item.image_policy == "enabled" for item in entries):
            raise ProviderError("Image input cannot be enabled for an unsupported model.")
        from db.managed_connections import reconcile as reconcile_rows
        return reconcile_rows(entry.id, spec.connection_name, entries)

    return ProviderServices(
        publish, lambda: is_enabled(entry.id), encrypt_secret, decrypt_secret, reconcile
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
