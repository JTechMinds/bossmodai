"""A self-contained model-provider extension; the host knows no ChatGPT details."""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from core.bm_cli.types import BossModCliResult
from core.extensions.contract import SetupError, SetupStatus
from core.extensions.model_provider import (
    IMAGE_POLICY_ACTION,
    ProviderAccount,
    ProviderAction,
    ProviderModel,
    ProviderStatus,
)
from core.models.thinking import THINKING_LEVELS
from .auth import CATALOG_VERSION, ChatGPTSession
from .capabilities import ACCEPTED_EFFORTS, Capability, host_key
from .config import ChatGPTConfig, ChatGPTError
from .provider import ResponsesAdapter
from .store import CredentialStore

# The image policies the operator may pick: all of them, or only Auto and
# Disabled for a model ChatGPT says takes no images.
_ALL_POLICIES = ["auto", "enabled", "disabled"]
_NO_IMAGE_POLICIES = ["auto", "disabled"]


def _spoken_list(words: list[str]) -> str:
    """``A``, ``A and B``, ``A, B and C``: names as a sentence reads them."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _account_rows(account: dict) -> list[ProviderAccount]:
    """The saved accounts as the operator knows them, never by id.

    Pure: reads only ``ChatGPTSession.account_status()``. A label an earlier
    account already uses gets " (2)", " (3)"… in saved order, so two accounts
    with one email stay distinguishable without exposing their ids.

    Args:
        account: ``ChatGPTSession.account_status()``.

    Returns:
        One row per saved account, in saved order. Each row's button is
        Switch (another account), Sign in again (the active account, signed
        out) or none (the active account, signed in).
    """
    seen: dict[str, int] = {}
    rows = []
    for saved in account["accounts"]:
        seen[saved["label"]] = seen.get(saved["label"], 0) + 1
        count = seen[saved["label"]]
        active = saved["id"] == account["active_account"]
        values = {"account_id": saved["id"]}
        if not active:
            action = ProviderAction(key="login", label="Switch", values=values)
        elif not saved["signed_in"]:
            action = ProviderAction(key="login", label="Sign in again", values=values)
        else:
            action = None
        rows.append(ProviderAccount(
            label=saved["label"] if count == 1 else f"{saved['label']} ({count})",
            active=active, signed_in=saved["signed_in"], action=action))
    return rows


def _account_actions(account: dict) -> list[ProviderAction]:
    """The account-level buttons: one sign-in state, one set.

    Args:
        account: ``ChatGPTSession.account_status()``.

    Returns:
        Cancel sign-in while one is open; the primary Sign in with ChatGPT
        with no saved account; otherwise Add another account, and Sign out
        while the active account is signed in.
    """
    if account["pending"]:
        return [ProviderAction(key="cancel", label="Cancel sign-in")]
    if not account["accounts"]:
        return [ProviderAction(key="login", label="Sign in with ChatGPT",
                               values={"account_id": ""}, primary=True)]
    actions = [ProviderAction(key="login", label="Add another account", values={"account_id": ""})]
    if any(saved["id"] == account["active_account"] and saved["signed_in"]
           for saved in account["accounts"]):
        actions.append(ProviderAction(key="logout", label="Sign out"))
    return actions


def _model_row(entry: dict, stored_policy: str, current_catalog: bool) -> ProviderModel:
    """One catalog model as the Models section shows it.

    Pure: reads only the stored catalog entry, so status polling never
    fetches. Thinking levels are host keys in host vocabulary order, so the
    extension needs no label table.

    Args:
        entry: A stored catalog entry (``catalog.normalize``, or a
            pre-upgrade entry with only ``slug`` and ``display_name``).
        stored_policy: The operator's stored image policy for it.
        current_catalog: False for a catalog stored before capability
            fields existed (``CATALOG_VERSION``).

    Returns:
        The model with its thinking levels, its default, its image policy in
        effect and the policies it may take, and a plain note: thinking names
        BossMod can't offer yet, a catalog that listed no thinking options or
        needs a refresh, and an Enabled that ChatGPT's catalog overrides.
    """
    capability = Capability.from_entry(entry)
    offered = {host_key(effort) for effort in capability.offered}
    notes = []
    if capability.omitted:
        names = _spoken_list([name[:1].upper() + name[1:] for name in capability.omitted])
        notes.append(f"{names} thinking isn't available in BossMod yet.")
    if not current_catalog:
        notes.append("Refresh models to load thinking options.")
    elif not capability.listed:
        notes.append("ChatGPT didn't list thinking options for this model.")
    if capability.image_conflict(stored_policy):
        notes.append("ChatGPT says this model can't take images, so image input is off.")
    default = capability.default_effort
    return ProviderModel(
        key=entry["slug"],
        name=entry["display_name"],
        thinking=[key for key in THINKING_LEVELS if key in offered],
        thinking_default=host_key(default) if default in ACCEPTED_EFFORTS else None,
        images=capability.images,
        image_policy=capability.published_image_policy(stored_policy),
        image_choices=_NO_IMAGE_POLICIES if capability.images == "unsupported" else _ALL_POLICIES,
        note=" ".join(notes) or None,
    )


def create(ctx):
    if ctx.model_provider is None:
        raise ChatGPTError("The host does not support model-provider extensions.")
    config = ChatGPTConfig.model_validate(ctx.manifest.defaults)
    adapter = ResponsesAdapter(config)
    store = CredentialStore(
        ctx.data_dir, ctx.model_provider.seal, ctx.model_provider.unseal
    )
    session = ChatGPTSession(config, store, ctx.model_provider, adapter.probe)
    return ChatGPTExtension(session, adapter)


class ChatGPTExtension:
    def __init__(self, session, adapter):
        self.session, self.adapter = session, adapter
        self.notice = None

    def setup_status(self):
        return SetupStatus(state="not_required")

    def run_setup(self, log_path):
        raise SetupError(
            "No download is required. Sign in through Extensions → ChatGPT."
        )

    def shutdown(self):
        self.session.cancel_login()

    def handle(self, ctx, parsed, body):
        ready = self.session.account_status()["connected"]
        valid = parsed.args == ("status",)
        detail = (
            (
                "ChatGPT is connected."
                if ready
                else "Ask the operator to sign in through Extensions → ChatGPT."
            )
            if valid
            else "Usage: chatgpt status"
        )
        return BossModCliResult(
            command=parsed.raw, ok=valid, detail=detail, prompt_content=detail
        )

    def provider_status(self):
        """The operator's view of ChatGPT: the account state, the saved accounts and the models.

        Pure over ``ChatGPTSession.account_status()``: never fetches. A sign-out
        result (``self.notice``) replaces the state sentence until the next
        action.

        Returns:
            ProviderStatus with models, their refresh time and Refresh models
            only while connected.
        """
        account = self.session.account_status()
        pending, connected = account["pending"], account["connected"]
        if self.notice:
            message = self.notice
        elif pending:
            message = "Finish signing in in your browser."
        elif connected:
            message = f"Signed in as {account['email']}." if account["email"] else "Signed in."
        else:
            message = "Sign in to let agents use your ChatGPT plan."
        current = account["catalog_version"] == CATALOG_VERSION
        policies = account["image_policies"]
        models = [_model_row(entry, policies.get(entry["slug"], "auto"), current)
                  for entry in account["models"]] if connected else []
        refreshed = account["refreshed_at"]
        return ProviderStatus(
            connected=connected,
            pending=pending,
            message=message,
            error=account["error"],
            actions=_account_actions(account),
            accounts=_account_rows(account),
            models=models,
            models_updated_at=(datetime.fromtimestamp(refreshed, timezone.utc)
                               if refreshed is not None else None),
            model_actions=[ProviderAction(key="refresh", label="Refresh models")] if connected else [],
        )

    def provider_action(self, action, values):
        self.notice = None
        if action == "login" and set(values) <= {"account_id"}:
            self.session.begin_login(values.get("account_id") or None)
        elif action == "refresh" and not values:
            self.session.refresh_models()
        elif action == IMAGE_POLICY_ACTION and set(values) == {"model", "policy"}:
            self.session.set_image_policy(values["model"], values["policy"])
        elif action == "cancel" and not values:
            self.session.cancel_login()
        elif action == "logout" and not values:
            self.notice = self.session.sign_out()["message"]
        else:
            raise ChatGPTError("Unknown ChatGPT action or invalid parameters.")

    async def provider_complete(self, request):
        token = await asyncio.to_thread(self.session.validate_request, request)
        return await self.adapter.complete(token, request)

    def provider_complete_sync(self, request):
        return self.adapter.complete_sync(self.session.validate_request(request), request)
