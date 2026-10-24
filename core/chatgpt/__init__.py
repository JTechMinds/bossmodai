"""Local Sign in with ChatGPT connection for BossMod."""

EXTENSION_ID = "chatgpt"
API_BASE = "chatgpt://local"
RESOURCE = "https://api.openai.com/v1"
ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


class ChatGPTError(Exception):
    """A safe, operator-facing error; never contains provider response bodies."""
