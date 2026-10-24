"""ChatGPT connection extension. Authentication is an operator-only action."""
from core.bm_cli.types import BossModCliResult
from core.extensions.contract import SetupError, SetupStatus


def create(ctx):
    return ChatGPTExtension()


class ChatGPTExtension:
    def setup_status(self):
        return SetupStatus(state="not_required")

    def run_setup(self, log_path):
        raise SetupError("No download is required. Sign in through Extensions → ChatGPT.")

    def shutdown(self):
        pass

    def handle(self, ctx, parsed, body):
        # This command intentionally exposes no account info or credentials.
        from core.chatgpt.auth import status
        ready = status()["connected"]
        valid = parsed.args == ("status",)
        detail = ("ChatGPT is connected." if ready else "Ask the operator to sign in through Extensions → ChatGPT.") if valid else "Usage: chatgpt status"
        return BossModCliResult(command=parsed.raw, ok=valid, detail=detail, prompt_content=detail)
