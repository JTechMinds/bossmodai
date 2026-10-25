"""A self-contained model-provider extension; the host knows no ChatGPT details."""

from __future__ import annotations
import asyncio
from core.bm_cli.types import BossModCliResult
from core.extensions.contract import SetupError, SetupStatus
from core.extensions.model_provider import ProviderStatus
from .auth import ChatGPTSession
from .config import ChatGPTConfig, ChatGPTError
from .provider import ResponsesAdapter
from .store import CredentialStore


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
                from .capabilities import for_model
                capability = for_model(account["model"] or account["models"][0]["slug"])
                choices.append({"label": "Image input (Auto: " + ("on" if capability.images == "supported" else "off; support unknown") + ")",
                                "value": account["image_policy"], "action": "images", "parameter": "policy",
                                "options": [{"value": p, "label": p.title()} for p in ("auto", "enabled", "disabled")]})
                actions.append({"key": "refresh", "label": "Refresh models"})
                actions.append({"key": "logout", "label": "Sign out"})
                message += " Thinking: Server default" + (", " + ", ".join(capability.thinking_levels()) if capability.efforts else " only (capabilities unverified)") + ". Last catalog refresh: " + str(account["refreshed_at"] or "unknown")
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
