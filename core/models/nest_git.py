"""Nest git Settings keys and operator-facing card copy.

One Settings store. Secrets stay on ``nest_git_pat`` / ``nest_git_ssh_key``.
"""

from __future__ import annotations

NEST_GIT_CATEGORY = "nest_git"
NEST_GIT_HOST_ENABLED_KEY = "nest_git_host_enabled"
NEST_GIT_PAT_KEY = "nest_git_pat"
NEST_GIT_SSH_KEY = "nest_git_ssh_key"

NEST_GIT_KIND = "nest_git"
NEST_GIT_GRANT_ROOT = NEST_GIT_HOST_ENABLED_KEY

NEST_GIT_TITLE = "Enable host git for nest?"
NEST_GIT_BODY = (
    "Remote nest git (typically push) needs credentials the Shell can see. "
    "Enable host git after a credential helper or SSH agent is visible to Shell, "
    "or add a PAT/SSH. Both write Settings → Nest git. "
    "Always-allow on a command does not skip auth. "
    "Browser or desktop GitHub login is not the agent's."
)
NEST_GIT_ENABLE_LABEL = "Enable host git for nest"
NEST_GIT_ADD_LABEL = "Add PAT/SSH"
NEST_GIT_ENABLE_HINT = "same as Settings → Nest git. Always-allow does not skip auth."
NEST_GIT_ENABLED_NOTE = "Nest git auth ready (Settings → Nest git)."
NEST_GIT_CARD_COPY = "needs nest git credentials — Enable host git or Add PAT/SSH"

NEST_GIT_HOWTO = (
    "Configure a git credential helper the Shell can see "
    "(`git config --get credential.helper`) or start ssh-agent and `ssh-add` a key "
    "so SSH_AUTH_SOCK is visible, then Enable host git. "
    "Or add a PAT/SSH in Settings → Nest git. "
    "Browser or desktop GitHub login is not the agent's."
)
NEST_GIT_NO_CREDS_WHY = "Nest git has no credentials"
NEST_GIT_PROBE_FAIL_WHY = "Host git is not visible to Shell"
NEST_GIT_BLOCK_KIND = "blocked_nest_git"

# Bot attribution when a stored PAT is the auth path.
NEST_GIT_BOT_NAME = "bossmod-bot"
NEST_GIT_BOT_EMAIL = "nest-git@users.noreply.github.com"
