"""ChatGPT protocol settings owned by the extension manifest."""

from pydantic import BaseModel, ConfigDict, Field
from core.extensions.model_provider import ProviderError


class ChatGPTError(ProviderError):
    """Credential-safe operator error from the ChatGPT extension."""


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
