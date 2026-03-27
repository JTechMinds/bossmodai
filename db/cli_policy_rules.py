"""BossMod AI — CLI policy rule CRUD.

Provides typed, parameterized access to the ``cli_policy_rules`` table.
The primary query for the policy engine is :func:`get_rules_by_tier`, which
returns agent-specific rules first, then global (agent_id IS NULL) rules,
both ordered by priority descending.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.models.cli_policy import CliPolicyRule
from db.crud import (
    build_update_returning,
    execute,
    fetch_all,
    fetch_one,
    insert_returning,
    query_one,
)

_ALL_COLUMNS = (
    "id, tier, pattern, match_mode, agent_id, description, "
    "enabled, priority, created_at, updated_at"
)

_UPDATE_VALID_COLUMNS = {
    "tier", "pattern", "match_mode", "agent_id",
    "description", "enabled", "priority", "updated_at",
}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def create_rule(
    *,
    tier: str,
    pattern: str,
    match_mode: str = "prefix",
    agent_id: str | None = None,
    description: str | None = None,
    enabled: bool = True,
    priority: int = 0,
) -> CliPolicyRule:
    """Insert a new CLI policy rule."""
    return insert_returning(
        f"""
        INSERT INTO cli_policy_rules (
            tier, pattern, match_mode, agent_id,
            description, enabled, priority
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_ALL_COLUMNS}
        """,
        [tier, pattern, match_mode, agent_id, description, enabled, priority],
        CliPolicyRule,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_rules(
    *,
    tier: str | None = None,
    agent_id: str | None = None,
    enabled_only: bool = False,
) -> list[CliPolicyRule]:
    """Return rules with optional filters, ordered by tier, priority DESC, created_at."""
    conditions: list[str] = []
    params: list[object] = []

    if tier is not None:
        params.append(tier)
        conditions.append(f"tier = ${len(params)}")
    if agent_id is not None:
        params.append(agent_id)
        conditions.append(f"agent_id = ${len(params)}")
    if enabled_only:
        conditions.append("enabled = TRUE")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return fetch_all(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM cli_policy_rules
        {where}
        ORDER BY tier, priority DESC, created_at
        """,
        params,
        CliPolicyRule,
    )


def get_rule(rule_id: str) -> CliPolicyRule | None:
    """Fetch a single rule by id."""
    return fetch_one(
        f"SELECT {_ALL_COLUMNS} FROM cli_policy_rules WHERE id = $1",
        [rule_id],
        CliPolicyRule,
    )


def get_rules_by_tier(
    tier: str,
    *,
    agent_id: str | None = None,
) -> list[CliPolicyRule]:
    """Return enabled rules for a tier, agent-specific first then global.

    This is the primary query used by the policy engine. Agent-specific rules
    (matching *agent_id*) are returned before global rules (``agent_id IS NULL``),
    with each group ordered by priority descending.
    """
    conditions = ["tier = $1", "enabled = TRUE"]
    params: list[object] = [tier]

    if agent_id is not None:
        params.append(agent_id)
        idx = len(params)
        conditions.append(f"(agent_id = ${idx} OR agent_id IS NULL)")
    else:
        conditions.append("agent_id IS NULL")

    return fetch_all(
        f"""
        SELECT {_ALL_COLUMNS}
        FROM cli_policy_rules
        WHERE {' AND '.join(conditions)}
        ORDER BY
            CASE WHEN agent_id IS NOT NULL THEN 0 ELSE 1 END,
            priority DESC
        """,
        params,
        CliPolicyRule,
    )


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

def update_rule(rule_id: str, **fields: object) -> CliPolicyRule | None:
    """Update a rule's mutable fields. Automatically bumps ``updated_at``."""
    fields["updated_at"] = datetime.now(timezone.utc)
    return build_update_returning(
        "cli_policy_rules",
        "id",
        rule_id,
        fields,
        _UPDATE_VALID_COLUMNS,
        _ALL_COLUMNS,
        CliPolicyRule,
    )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_rule(rule_id: str) -> bool:
    """Delete a rule by id. Returns ``True`` if a row was removed."""
    row = query_one(
        "SELECT id FROM cli_policy_rules WHERE id = $1",
        [rule_id],
    )
    if row is None:
        return False
    execute("DELETE FROM cli_policy_rules WHERE id = $1", [rule_id])
    return True


# ---------------------------------------------------------------------------
# Seed defaults
# ---------------------------------------------------------------------------

_SEED_RULES: list[tuple[str, str, str, str | None]] = [
    # (tier, pattern, match_mode, description)

    # ── never_allowed — prefix ──
    ("never_allowed", "sudo",       "prefix", "Privilege escalation"),
    ("never_allowed", "su",         "prefix", "Switch user"),
    ("never_allowed", "shutdown",   "prefix", "System shutdown"),
    ("never_allowed", "reboot",     "prefix", "System reboot"),
    ("never_allowed", "mkfs",       "prefix", "Filesystem format"),
    ("never_allowed", "dd",         "prefix", "Raw disk write"),
    ("never_allowed", "fdisk",      "prefix", "Partition editor"),
    ("never_allowed", "mount",      "prefix", "Mount filesystem"),
    ("never_allowed", "umount",     "prefix", "Unmount filesystem"),
    ("never_allowed", "systemctl",  "prefix", "Systemd control"),

    # ── never_allowed — glob ──
    ("never_allowed", "rm -rf /",   "glob", "Recursive root delete"),
    ("never_allowed", "rm -rf ~*",  "glob", "Recursive home delete"),
    ("never_allowed", "chmod 777 *","glob", "World-writable permissions"),

    # ── never_allowed — exact ──
    ("never_allowed", ":(){ :|:& };:", "exact", "Fork bomb"),

    # ── always_allowed — prefix ──
    ("always_allowed", "echo",      "prefix", "Print text"),
    ("always_allowed", "head",      "prefix", "File head"),
    ("always_allowed", "tail",      "prefix", "File tail"),
    ("always_allowed", "grep",      "prefix", "Pattern search"),
    ("always_allowed", "find",      "prefix", "File search"),
    ("always_allowed", "wc",        "prefix", "Word/line count"),
    ("always_allowed", "sort",      "prefix", "Sort lines"),
    ("always_allowed", "uniq",      "prefix", "Deduplicate lines"),
    ("always_allowed", "date",      "prefix", "Current date/time"),
    ("always_allowed", "env",       "prefix", "Environment variables"),
    ("always_allowed", "which",     "prefix", "Locate executable"),
    ("always_allowed", "whoami",    "prefix", "Current user"),
    ("always_allowed", "basename",  "prefix", "Strip directory"),
    ("always_allowed", "dirname",   "prefix", "Strip filename"),
    ("always_allowed", "diff",      "prefix", "File diff"),
    ("always_allowed", "tr",        "prefix", "Translate characters"),
    ("always_allowed", "tee",       "prefix", "Tee output"),
    ("always_allowed", "xargs",     "prefix", "Build arguments"),
    ("always_allowed", "seq",       "prefix", "Sequence generator"),
    ("always_allowed", "true",      "prefix", "No-op success"),
    ("always_allowed", "false",     "prefix", "No-op failure"),
    ("always_allowed", "test",      "prefix", "Condition test"),
    ("always_allowed", "expr",      "prefix", "Expression evaluator"),
    ("always_allowed", "python",    "prefix", "Python interpreter"),
    ("always_allowed", "python3",   "prefix", "Python 3 interpreter"),
    ("always_allowed", "node",      "prefix", "Node.js interpreter"),
    ("always_allowed", "npm run",   "prefix", "NPM script runner"),
    ("always_allowed", "pip list",  "prefix", "List pip packages"),
    ("always_allowed", "pip show",  "prefix", "Show pip package"),
    ("always_allowed", "ls",        "prefix", "List directory"),
    ("always_allowed", "cat",       "prefix", "Concatenate files"),

    # ── approval_required — prefix ──
    ("approval_required", "rm",            "prefix", "Remove files"),
    ("approval_required", "mv",            "prefix", "Move/rename files"),
    ("approval_required", "cp",            "prefix", "Copy files"),
    ("approval_required", "chmod",         "prefix", "Change permissions"),
    ("approval_required", "chown",         "prefix", "Change ownership"),
    ("approval_required", "curl",          "prefix", "HTTP client"),
    ("approval_required", "wget",          "prefix", "Download files"),
    ("approval_required", "git push",      "prefix", "Git push"),
    ("approval_required", "git reset",     "prefix", "Git reset"),
    ("approval_required", "git checkout",  "prefix", "Git checkout"),
    ("approval_required", "pip install",   "prefix", "Install pip package"),
    ("approval_required", "pip uninstall", "prefix", "Uninstall pip package"),
    ("approval_required", "npm install",   "prefix", "Install npm package"),
    ("approval_required", "npm uninstall", "prefix", "Uninstall npm package"),
    ("approval_required", "docker",        "prefix", "Docker command"),
    ("approval_required", "kill",          "prefix", "Kill process"),
    ("approval_required", "pkill",         "prefix", "Kill process by name"),
]


def seed_default_rules() -> None:
    """Populate default CLI policy rules if the table is empty. Idempotent."""
    row = query_one("SELECT COUNT(*) AS cnt FROM cli_policy_rules")
    if row and int(row["cnt"]) > 0:
        return

    for tier, pattern, match_mode, description in _SEED_RULES:
        execute(
            """
            INSERT INTO cli_policy_rules (tier, pattern, match_mode, description)
            VALUES ($1, $2, $3, $4)
            """,
            [tier, pattern, match_mode, description],
        )
