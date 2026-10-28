"""A self-contained model-provider extension; the host knows no ChatGPT details."""

from __future__ import annotations
import asyncio
from core.bm_cli.types import BossModCliResult
from core.extensions.contract import SetupError, SetupStatus
from core.extensions.model_provider import ProviderStatus
from core.models.thinking import THINKING_LEVELS
from .auth import CATALOG_VERSION, ChatGPTSession, catalog_entry
from .capabilities import Capability, host_key
from .config import ChatGPTConfig, ChatGPTError
from .provider import ResponsesAdapter
from .store import CredentialStore

_IMAGE_LABELS = {
    "supported": "Image input (Auto: on)",
    "unsupported": "Image input (Auto: off; this model doesn't accept images)",
    "unknown": "Image input (Auto: off; support unknown)",
}


def _capability_status(account: dict) -> tuple[list[str], str]:
    """Plain-language capability sentences and the image-choice label for the selected model.

    Pure: reads only the stored catalog in ``account_status()``, so status
    polling never fetches. Tiers are host keys in host vocabulary order, so
    the extension needs no label table.

    Args:
        account: ``ChatGPTSession.account_status()`` for a connected account
            with at least one model.

    Returns:
        ``(sentences, image_label)``. Sentences cover the selected model's
        thinking tiers, names BossMod can't offer yet, how many models listed
        thinking options, a refresh hint for a pre-upgrade catalog, and one
        sentence per image conflict.
    """
    models = account["models"]
    entry = catalog_entry(account, account["model"] or models[0]["slug"])
    capability = Capability.from_entry(entry) if entry is not None else None
    if account["catalog_version"] != CATALOG_VERSION:
        sentences = ["Thinking: Server default only.", "Refresh models to load thinking options."]
    else:
        if capability is None:
            sentences = ["The selected model is no longer offered by your account; choose another."]
        else:
            levels = capability.thinking_levels()
            tiers = [key for key in THINKING_LEVELS if key in levels]
            uses = (f" (ChatGPT uses {host_key(capability.default_effort)})"
                    if capability.default_effort else "")
            sentences = [f"Thinking: Server default{uses}, {', '.join(tiers)}." if tiers
                         else f"Thinking: Server default only{uses}."]
            if not capability.listed:
                sentences.append("ChatGPT didn't list thinking options for this model.")
            if capability.omitted:
                sentences.append("Not available in BossMod yet: " + ", ".join(capability.omitted) + ".")
        loaded = sum(Capability.from_entry(m).listed for m in models)
        sentences.append(f"Thinking options loaded for {loaded} of {len(models)} models.")
    sentences.extend(f"ChatGPT says {name} doesn't accept images, so image input stays off "
                     "although it's set to Enabled." for name in account["image_conflicts"])
    return sentences, _IMAGE_LABELS[capability.images if capability is not None else "unknown"]


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
        account = self.session.account_status()
        pending, connected = account["pending"], account["connected"]
        message = (
            "Finish signing in in your browser…"
            if pending
            else (
                f"Connected{(' as ' + account['email']) if account['email'] else ''}. Choose Chat GPT when creating or editing an agent."
                if connected
                else "Sign in to add the Chat GPT connection. Eligible ChatGPT plan usage and limits apply."
            )
        )
        if self.notice:
            message = self.notice
        if pending:
            actions = [{"key": "cancel", "label": "Cancel sign-in"}]
            choices = []
        else:
            saved = account["accounts"]
            selected = account["active_account"] or (saved[0]["id"] if saved else "")
            actions = [
                {
                    "key": "login",
                    "label": "Continue with ChatGPT",
                    "values": {"account_id": selected},
                }
            ]
            choices = []
            if saved:
                actions.append(
                    {
                        "key": "login",
                        "label": "Add another account",
                        "values": {"account_id": ""},
                    }
                )
                choices.append(
                    {
                        "label": "ChatGPT account",
                        "value": selected,
                        "action": "login",
                        "parameter": "account_id",
                        "options": [
                            {
                                "value": a["id"],
                                "label": f"{a['label']} ({a['id'][-8:]})",
                            }
                            for a in saved
                        ],
                    }
                )
            if connected and account["models"]:
                choices.append(
                    {
                        "label": "Model controls (does not change agents)",
                        "value": account["model"] or account["models"][0]["slug"],
                        "action": "model",
                        "parameter": "model",
                        "options": [
                            {"value": m["slug"], "label": m["display_name"]}
                            for m in account["models"]
                        ],
                    }
                )
                sentences, image_label = _capability_status(account)
                choices.append({"label": image_label,
                                "value": account["image_policy"], "action": "images", "parameter": "policy",
                                "options": [{"value": p, "label": p.title()} for p in ("auto", "enabled", "disabled")]})
                actions.append({"key": "refresh", "label": "Refresh models"})
                actions.append({"key": "logout", "label": "Sign out"})
                message += " " + " ".join(sentences) + " Last catalog refresh: " + str(account["refreshed_at"] or "unknown")
        return ProviderStatus(
            connected=connected,
            pending=pending,
            message=message,
            error=account["error"],
            actions=actions,
            choices=choices,
        )

    def provider_action(self, action, values):
        self.notice = None
        if action == "login" and set(values) <= {"account_id"}:
            self.session.begin_login(values.get("account_id") or None)
        elif action == "model" and set(values) == {"model"}:
            self.session.select_model(values["model"])
        elif action == "refresh" and not values:
            self.session.refresh_models()
        elif action == "images" and set(values) == {"policy"}:
            self.session.set_image_policy(values["policy"])
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
