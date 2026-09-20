"""Nest git Settings keys and operator-facing card copy.

Named credentials live in ``nest_git_credentials`` (labels + match).
Secrets stay on ``nest_git_pat`` / ``nest_git_ssh_key`` (Default) or
``nest_git_pat_<id>`` / ``nest_git_ssh_<id>``.
"""

from __future__ import annotations

NEST_GIT_CATEGORY = "nest_git"
NEST_GIT_HOST_ENABLED_KEY = "nest_git_host_enabled"
NEST_GIT_PAT_KEY = "nest_git_pat"
NEST_GIT_SSH_KEY = "nest_git_ssh_key"
NEST_GIT_CREDENTIALS_KEY = "nest_git_credentials"
NEST_GIT_DEFAULT_CREDENTIAL_ID = "default"
NEST_GIT_DEFAULT_CREDENTIAL_LABEL = "Default"

NEST_GIT_KIND = "nest_git"
NEST_GIT_GRANT_ROOT = NEST_GIT_HOST_ENABLED_KEY

NEST_GIT_TITLE = "needs permission to push to GitHub"
NEST_GIT_BODY = (
    "Your computer’s GitHub login isn’t shared with agents. "
    "Paste a GitHub access token (a special password from GitHub → Settings → Developer settings), "
    "or an SSH key if you use those. Saved once under Settings → Nest git. "
    "Approving a command once doesn’t skip this."
)
NEST_GIT_ENABLE_LABEL = "Use this computer’s Git login"
NEST_GIT_ADD_LABEL = "Add a GitHub access token or SSH key"
NEST_GIT_ENABLE_HINT = (
    "Saved once under Settings → Nest git. Approving a command once doesn’t skip this."
)
NEST_GIT_ENABLED_NOTE = "GitHub permission saved under Settings → Nest git."
NEST_GIT_CARD_COPY = "needs permission to push to GitHub"
NEST_GIT_TOKEN_LABEL = "GitHub access token"
NEST_GIT_SSH_LABEL = "SSH key (optional)"
NEST_GIT_SAVE_LABEL = "Save"
NEST_GIT_OPEN_SETTINGS_LABEL = "Open Nest git settings"
NEST_GIT_EMPTY_CREDS = (
    "Paste a GitHub access token or an SSH key. An empty field doesn’t save."
)
NEST_GIT_LABEL_LABEL = "Name"
NEST_GIT_MATCH_LABEL = "Remote match"
NEST_GIT_MATCH_HINT = "github.com/Org/* or github.com/Org/repo"
NEST_GIT_DEFAULT_TOGGLE_LABEL = "Use for remotes that don’t match another credential"
NEST_GIT_PICK_PREFIX = "Use"

NEST_GIT_HOWTO = (
    "Configure a git credential helper the Shell can see "
    "(`git config --get credential.helper`) or start ssh-agent and `ssh-add` a key "
    "so SSH_AUTH_SOCK is visible, then Enable host git. "
    "Or add a PAT/SSH in Settings → Nest git. "
    "Browser or desktop GitHub login is not the agent's."
)
NEST_GIT_NO_CREDS_WHY = "Nest git has no credentials"
NEST_GIT_NO_MATCH_WHY = "No Nest git credential matches this remote"
NEST_GIT_TOKEN_REJECTED_WHY = "GitHub rejected this token"
NEST_GIT_TOKEN_NO_REPO_WHY = "this token may not have access to this repo"
NEST_GIT_TOKEN_NO_REPO_HINT = "grant it under the token’s repository access."
NEST_GIT_AMBIGUOUS_CREDS_WHY = (
    "GitHub rejected this token, or it may not have access to this repo"
)
# Kept as the fail-closed short why when 401 vs 403 cannot be told apart.
NEST_GIT_BAD_CREDS_WHY = NEST_GIT_AMBIGUOUS_CREDS_WHY
NEST_GIT_TOKEN_REJECTED_HOWTO = (
    "Paste a GitHub access token (a special password from GitHub → Settings → Developer settings), "
    "or an SSH key if you use those, on the Nest git card or under Settings → Nest git. "
    "Your computer’s GitHub login isn’t shared with agents."
)
NEST_GIT_TOKEN_NO_REPO_OWNER = (
    "A fine-grained token belongs to one Resource owner — you, or one organization, not both. "
    "Personal and organization repos cannot share one fine-grained token. "
    "For an organization repo, Resource owner = the org that owns the repo → select that repo → "
    "Contents Read and write. "
    "Need one key for everything? Use a classic repo token. "
    "If the organization uses SAML, open Configure SSO on the token."
)
NEST_GIT_TOKEN_NO_REPO_HOWTO = (
    f"{NEST_GIT_TOKEN_NO_REPO_WHY} — {NEST_GIT_TOKEN_NO_REPO_HINT} "
    f"{NEST_GIT_TOKEN_NO_REPO_OWNER}"
)
NEST_GIT_BAD_CREDS_HOWTO = (
    f"{NEST_GIT_TOKEN_REJECTED_WHY}. {NEST_GIT_TOKEN_REJECTED_HOWTO} "
    f"Or {NEST_GIT_TOKEN_NO_REPO_HOWTO}"
)
NEST_GIT_NO_MATCH_HOWTO = (
    "Add a credential for this remote, or pick which saved one to use. "
    f"Match like {NEST_GIT_MATCH_HINT}. "
    f"{NEST_GIT_TOKEN_NO_REPO_OWNER}"
)
NEST_GIT_PROBE_FAIL_WHY = "Host git is not visible to Shell"
NEST_GIT_BLOCK_KIND = "blocked_nest_git"

# Bot attribution when a stored PAT is the auth path.
NEST_GIT_BOT_NAME = "bossmod-bot"
NEST_GIT_BOT_EMAIL = "nest-git@users.noreply.github.com"


def nest_git_card_title(agent_name: str | None = None) -> str:
    """Return the in-thread card title, preferring the agent display name."""
    name = (agent_name or "").strip()
    if name:
        return f"{name} needs permission to push to GitHub"
    return NEST_GIT_TITLE
