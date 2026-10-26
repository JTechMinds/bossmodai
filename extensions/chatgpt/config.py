"""ChatGPT protocol settings owned by the extension manifest."""

from pydantic import BaseModel, ConfigDict, Field
from core.extensions.model_provider import ProviderError, ProviderUsageLimitError


class ChatGPTError(ProviderError):
    """Credential-safe operator error from the ChatGPT extension."""


class ChatGPTUsageLimitError(ChatGPTError, ProviderUsageLimitError):
    """The signed-in ChatGPT plan has no usage left for this request.

    Raised for OpenAI's ``subscription_sharing_usage_limit_exceeded``, which
    must pause plan-based requests rather than be retried. It stays a
    ``ChatGPTError`` so this extension's own handlers (such as the sign-in
    probe) still show its message, and it is a ``ProviderUsageLimitError`` so
    the host stops the work without learning any ChatGPT detail.
    """


class ChatGPTConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    issuer: str
    resource: str
    scopes: str
    agent_name: str
    login_timeout_seconds: float = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)
    probe_timeout_seconds: float = Field(gt=0)

    @property
    def authorize_url(self):
        return self.issuer + "/api/accounts/authorize"

    @property
    def token_url(self):
        return self.issuer + "/api/accounts/oauth/token"
