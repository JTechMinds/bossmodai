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
    "category, usage_syntax, help_text, "
    "enabled, priority, created_at, updated_at"
)

_UPDATE_VALID_COLUMNS = {
    "tier", "pattern", "match_mode", "agent_id",
    "description", "category", "usage_syntax", "help_text",
    "enabled", "priority", "updated_at",
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
    category: str = "general",
    usage_syntax: str | None = None,
    help_text: str | None = None,
    enabled: bool = True,
    priority: int = 0,
) -> CliPolicyRule:
    """Insert a new CLI policy rule."""
    return insert_returning(
        f"""
        INSERT INTO cli_policy_rules (
            tier, pattern, match_mode, agent_id,
            description, category, usage_syntax, help_text,
            enabled, priority
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING {_ALL_COLUMNS}
        """,
        [
            tier, pattern, match_mode, agent_id,
            description, category, usage_syntax, help_text,
            enabled, priority,
        ],
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
    execute(
        "UPDATE cli_approval_requests SET matched_rule_id = NULL WHERE matched_rule_id = $1",
        [rule_id],
    )
    execute("DELETE FROM cli_policy_rules WHERE id = $1", [rule_id])
    return True


# ---------------------------------------------------------------------------
# Seed defaults
# ---------------------------------------------------------------------------

# HA-SEC-P0-03 / overlapping HA-SEC-P1-04 seed lockdown.
#
# Choice: ``never_allowed`` (not ``approval_required``) for interpreters,
# ``xargs``, and POSIX shells.
#
# Approval still runs the approved argv, but the path jail only sees
# filesystem tokens — it cannot inspect ``python -c`` / ``node -e`` /
# ``bash -c`` payloads. ``xargs`` is an exec multiplexer (stdin becomes
# another command's argv). Those families therefore stay hard-blocked.
#
# Existing databases: :func:`reconcile_hardened_seed_rules` runs from
# ``init_db`` and upserts these rows. Operators can also use Settings →
# CLI Policy → Seed defaults, which wipes rules and re-inserts ``_SEED_RULES``.

_HARDENED_NEVER_ALLOWED: list[tuple[str, str, str, str | None, str, str | None, str | None]] = [
    ("never_allowed", "sh", "prefix", "POSIX shell (arbitrary command execution).", "system",
     "sh [options] [script]",
     "Invoke the POSIX shell. Blocked because -c and scripts execute arbitrary host commands that the path jail cannot inspect."),
    ("never_allowed", "bash", "prefix", "Bash shell (arbitrary command execution).", "system",
     "bash [options] [script]",
     "Invoke bash. Blocked because -c and scripts execute arbitrary host commands that the path jail cannot inspect."),
    ("never_allowed", "zsh", "prefix", "Zsh shell (arbitrary command execution).", "system",
     "zsh [options] [script]",
     "Invoke zsh. Blocked because -c and scripts execute arbitrary host commands that the path jail cannot inspect."),
    ("never_allowed", "dash", "prefix", "Dash shell (arbitrary command execution).", "system",
     "dash [options] [script]",
     "Invoke dash. Blocked because -c and scripts execute arbitrary host commands that the path jail cannot inspect."),
    ("never_allowed", "xargs", "prefix", "Build and execute commands from stdin.", "system",
     "xargs [options] <command>",
     "Build and execute commands from stdin. Blocked because it is an exec multiplexer that can invoke never-allowed binaries."),
    ("never_allowed", "python", "prefix", "Run the Python interpreter.", "development",
     "python [options] [script]",
     "Run the Python interpreter. Blocked because -c and imported modules can read or write any host path, bypassing the path jail."),
    ("never_allowed", "python3", "prefix", "Run the Python 3 interpreter.", "development",
     "python3 [options] [script]",
     "Run the Python 3 interpreter. Blocked because -c and imported modules can read or write any host path, bypassing the path jail."),
    ("never_allowed", "node", "prefix", "Run the Node.js runtime.", "development",
     "node [options] [script]",
     "Run the Node.js runtime. Blocked because -e and required modules can read or write any host path, bypassing the path jail."),
]

HARDENED_NEVER_ALLOWED_PATTERNS: frozenset[str] = frozenset(
    pattern for _tier, pattern, *_rest in _HARDENED_NEVER_ALLOWED
)

INTERPRETER_AND_XARGS_PATTERNS: frozenset[str] = frozenset(
    {"python", "python3", "node", "xargs"}
)

POSIX_SHELL_PATTERNS: frozenset[str] = frozenset({"sh", "bash", "zsh", "dash"})

_SEED_RULES: list[tuple[str, str, str, str | None, str, str | None, str | None]] = [
    # (tier, pattern, match_mode, description, category, usage_syntax, help_text)

    # ── never_allowed — prefix ──
    ("never_allowed", "sudo", "prefix", "Run command as superuser.", "system",
     "sudo <command>",
     "Run a command with superuser privileges. Blocked because agents must not escalate permissions."),
    ("never_allowed", "su", "prefix", "Switch user context.", "system",
     "su [user]",
     "Switch the current user context. Blocked because agents must not change identity."),
    ("never_allowed", "shutdown", "prefix", "Shut down the system.", "system",
     "shutdown [options]",
     "Power off or halt the system. Blocked because agents must not affect host uptime."),
    ("never_allowed", "reboot", "prefix", "Reboot the system.", "system",
     "reboot [options]",
     "Restart the system. Blocked because agents must not affect host uptime."),
    ("never_allowed", "mkfs", "prefix", "Create a filesystem on a device.", "system",
     "mkfs [options] <device>",
     "Create a filesystem on a device, destroying all existing data. Blocked because of irreversible data loss."),
    ("never_allowed", "dd", "prefix", "Copy raw bytes between devices.", "system",
     "dd if=<source> of=<dest> [options]",
     "Low-level byte copying between devices or files. Blocked because a wrong target can destroy disks instantly."),
    ("never_allowed", "fdisk", "prefix", "Edit disk partition tables.", "system",
     "fdisk <device>",
     "Modify disk partition tables. Blocked because of irreversible data loss."),
    ("never_allowed", "mount", "prefix", "Mount a filesystem.", "system",
     "mount <device> <mountpoint>",
     "Attach a filesystem to the directory tree. Blocked because agents must not modify mounts."),
    ("never_allowed", "umount", "prefix", "Unmount a filesystem.", "system",
     "umount <mountpoint>",
     "Detach a mounted filesystem. Blocked because agents must not modify mounts."),
    ("never_allowed", "systemctl", "prefix", "Control systemd services.", "system",
     "systemctl <action> <unit>",
     "Control systemd services and system state. Blocked because agents must not manage system services."),
    *_HARDENED_NEVER_ALLOWED,

    # ── never_allowed — glob ──
    ("never_allowed", "rm -rf /", "glob", "Recursive root delete.", "filesystem",
     None,
     "Recursively delete everything from the root filesystem. Blocked because this is catastrophic and irreversible."),
    ("never_allowed", "rm -rf ~*", "glob", "Recursive home delete.", "filesystem",
     None,
     "Recursively delete home directories. Blocked because of total user data loss."),
    ("never_allowed", "chmod 777 *", "glob", "Set world-writable permissions.", "filesystem",
     None,
     "Set all files world-readable/writable/executable. Blocked because it creates severe security vulnerabilities."),

    # ── never_allowed — exact ──
    ("never_allowed", ":(){ :|:& };:", "exact", "Fork bomb.", "system",
     None,
     "Recursively spawn processes until the system runs out of resources. Blocked because it crashes the host."),

    # ── always_allowed — prefix ──
    ("always_allowed", "echo", "prefix", "Print text to stdout.", "general",
     "echo <text>",
     "Print text to stdout.\nExample: echo hello world"),
    ("always_allowed", "head", "prefix", "Show first lines of a file.", "general",
     "head [options] <file>",
     "Display the first lines of a file (default 10).\nExample: head -n 20 app.log"),
    ("always_allowed", "tail", "prefix", "Show last lines of a file.", "general",
     "tail [options] <file>",
     "Display the last lines of a file (default 10).\nExample: tail -f app.log"),
    ("always_allowed", "grep", "prefix", "Search for text patterns.", "general",
     "grep [options] <pattern> [file...]",
     "Search for text patterns in files.\nExample: grep -r 'TODO' src/"),
    ("always_allowed", "find", "prefix", "Search for files.", "filesystem",
     "find <path> [expression]",
     "Search for files by name, type, size, or other attributes.\nExample: find . -name '*.py' -type f"),
    ("always_allowed", "wc", "prefix", "Count lines, words, and bytes.", "general",
     "wc [options] <file>",
     "Count lines, words, and bytes in a file.\nExample: wc -l README.md"),
    ("always_allowed", "sort", "prefix", "Sort lines of text.", "general",
     "sort [options] <file>",
     "Sort lines of text alphabetically or numerically.\nExample: sort -n scores.txt"),
    ("always_allowed", "uniq", "prefix", "Remove duplicate adjacent lines.", "general",
     "uniq [options] <file>",
     "Remove adjacent duplicate lines. Typically used after sort.\nExample: sort data.txt | uniq -c"),
    ("always_allowed", "date", "prefix", "Display current date and time.", "general",
     "date [+format]",
     "Display or set the system date and time.\nExample: date '+%Y-%m-%d'"),
    ("always_allowed", "env", "prefix", "Print environment variables.", "general",
     "env [options]",
     "Print the current environment variables.\nExample: env"),
    ("always_allowed", "which", "prefix", "Locate a command executable.", "general",
     "which <command>",
     "Show the full path of a command executable.\nExample: which python3"),
    ("always_allowed", "whoami", "prefix", "Print the current username.", "general",
     "whoami",
     "Print the effective username of the current user.\nExample: whoami"),
    ("always_allowed", "basename", "prefix", "Strip directory from a path.", "general",
     "basename <path> [suffix]",
     "Strip the directory portion from a file path.\nExample: basename /tmp/report.csv .csv"),
    ("always_allowed", "dirname", "prefix", "Strip filename from a path.", "general",
     "dirname <path>",
     "Return the directory portion of a file path.\nExample: dirname /tmp/report.csv"),
    ("always_allowed", "diff", "prefix", "Compare two files line by line.", "general",
     "diff [options] <file1> <file2>",
     "Compare two files line by line.\nExample: diff old.conf new.conf"),
    ("always_allowed", "tr", "prefix", "Translate or delete characters.", "general",
     "tr [options] <set1> [set2]",
     "Translate or delete characters from stdin.\nExample: echo HELLO | tr A-Z a-z"),
    ("always_allowed", "tee", "prefix", "Write stdin to stdout and a file.", "general",
     "tee [options] <file>",
     "Read stdin and write to both stdout and a file.\nExample: ls | tee listing.txt"),
    ("always_allowed", "seq", "prefix", "Print a sequence of numbers.", "general",
     "seq [first [incr]] <last>",
     "Print a sequence of numbers.\nExample: seq 1 5"),
    ("always_allowed", "true", "prefix", "Return success exit code.", "general",
     "true",
     "Return a zero (success) exit code. Used in shell scripting.\nExample: true && echo ok"),
    ("always_allowed", "false", "prefix", "Return failure exit code.", "general",
     "false",
     "Return a non-zero (failure) exit code. Used in shell scripting.\nExample: false || echo failed"),
    ("always_allowed", "test", "prefix", "Evaluate a conditional expression.", "general",
     "test <expression>",
     "Evaluate a conditional expression and return 0 (true) or 1 (false).\nExample: test -f config.yaml && echo exists"),
    ("always_allowed", "expr", "prefix", "Evaluate arithmetic or string expressions.", "general",
     "expr <expression>",
     "Evaluate arithmetic or string expressions.\nExample: expr 2 + 3"),
    ("always_allowed", "npm run", "prefix", "Run an NPM script.", "development",
     "npm run <script>",
     "Execute a script defined in package.json.\nExample: npm run build"),
    ("always_allowed", "pip list", "prefix", "List installed Python packages.", "packages",
     "pip list [options]",
     "List installed Python packages.\nExample: pip list --outdated"),
    ("always_allowed", "pip show", "prefix", "Show Python package details.", "packages",
     "pip show <package>",
     "Show metadata about an installed Python package.\nExample: pip show requests"),
    ("always_allowed", "ls", "prefix", "List files and directories.", "filesystem",
     "ls [options] [path]",
     "List files and directories.\nExample: ls -la /tmp"),
    ("always_allowed", "cat", "prefix", "Print file contents to stdout.", "filesystem",
     "cat [options] <file...>",
     "Print file contents to stdout.\nExample: cat config.yaml"),
    ("always_allowed", "uname", "prefix", "Print kernel and OS identity.", "system",
     "uname [options]",
     "Print the kernel name and related system identity. Pathless diagnostic.\nExample: uname -a"),

    # ── approval_required — prefix ──
    ("approval_required", "rm", "prefix", "Remove files or directories.", "filesystem",
     "rm [options] <file...>",
     "Delete files or directories. Approval needed because deletions are hard to undo.\nExample: rm -r build/"),
    ("approval_required", "mv", "prefix", "Move or rename files.", "filesystem",
     "mv <source> <dest>",
     "Move or rename files and directories. Approval needed because it can overwrite existing files.\nExample: mv old.txt new.txt"),
    ("approval_required", "cp", "prefix", "Copy files or directories.", "filesystem",
     "cp [options] <source> <dest>",
     "Copy files or directories. Approval needed because it can overwrite existing files.\nExample: cp -r src/ backup/"),
    ("approval_required", "chmod", "prefix", "Change file permissions.", "filesystem",
     "chmod <mode> <file...>",
     "Change file permissions. Approval needed because incorrect permissions create security vulnerabilities.\nExample: chmod 644 config.yaml"),
    ("approval_required", "chown", "prefix", "Change file ownership.", "filesystem",
     "chown <owner>[:<group>] <file...>",
     "Change file ownership. Approval needed because incorrect ownership can break access.\nExample: chown www-data:www-data /var/www"),
    ("approval_required", "curl", "prefix", "Transfer data via HTTP.", "network",
     "curl [options] <url>",
     "Transfer data from or to a server. Approval needed because it initiates network requests.\nExample: curl -s https://api.example.com/status"),
    ("approval_required", "wget", "prefix", "Download files from the web.", "network",
     "wget [options] <url>",
     "Download files from the web. Approval needed because it initiates network requests.\nExample: wget https://example.com/data.csv"),
    ("approval_required", "git push", "prefix", "Push commits to a remote.", "git",
     "git push [remote] [branch]",
     "Push local commits to a remote repository. Approval needed because it modifies the remote.\nExample: git push origin main"),
    ("approval_required", "git reset", "prefix", "Reset HEAD to a prior state.", "git",
     "git reset [options] [commit]",
     "Reset the current HEAD to a specified state. Approval needed because it can discard commits.\nExample: git reset --soft HEAD~1"),
    ("approval_required", "git checkout", "prefix", "Switch branches or restore files.", "git",
     "git checkout [options] <branch|file>",
     "Switch branches or restore files. Approval needed because it can discard uncommitted changes.\nExample: git checkout -b feature-branch"),
    ("approval_required", "pip install", "prefix", "Install a Python package.", "packages",
     "pip install <package>",
     "Install a Python package from PyPI. Approval needed because it modifies the environment.\nExample: pip install requests==2.31.0"),
    ("approval_required", "pip uninstall", "prefix", "Remove a Python package.", "packages",
     "pip uninstall <package>",
     "Remove an installed Python package. Approval needed because it modifies the environment.\nExample: pip uninstall requests"),
    ("approval_required", "npm install", "prefix", "Install a Node.js package.", "packages",
     "npm install <package>",
     "Install a Node.js package. Approval needed because it modifies node_modules.\nExample: npm install express"),
    ("approval_required", "npm uninstall", "prefix", "Remove a Node.js package.", "packages",
     "npm uninstall <package>",
     "Remove a Node.js package. Approval needed because it modifies the dependency tree.\nExample: npm uninstall express"),
    ("approval_required", "docker", "prefix", "Manage Docker containers and images.", "system",
     "docker <subcommand> [options]",
     "Manage containers, images, and networks. Approval needed because Docker operations affect system resources.\nExample: docker ps"),
    ("approval_required", "kill", "prefix", "Send a signal to a process.", "system",
     "kill [signal] <pid>",
     "Send a signal to a process (default SIGTERM). Approval needed because it can terminate critical processes.\nExample: kill 1234"),
    ("approval_required", "pkill", "prefix", "Kill processes by name pattern.", "system",
     "pkill [options] <pattern>",
     "Send a signal to processes matching a name pattern. Approval needed because it can terminate critical processes.\nExample: pkill -f 'node server'"),
]


def seed_default_rules() -> None:
    """Populate default CLI policy rules if the table is empty. Idempotent."""
    row = query_one("SELECT COUNT(*) AS cnt FROM cli_policy_rules")
    if row and int(row["cnt"]) > 0:
        return

    for tier, pattern, match_mode, description, category, usage_syntax, help_text in _SEED_RULES:
        execute(
            """
            INSERT INTO cli_policy_rules (
                tier, pattern, match_mode, description,
                category, usage_syntax, help_text
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            [tier, pattern, match_mode, description, category, usage_syntax, help_text],
        )


def reconcile_hardened_seed_rules() -> int:
    """Apply HA-SEC-P0-03 lockdown to existing databases.

    Updates every row (global or agent-specific) whose pattern is in
    :data:`_HARDENED_NEVER_ALLOWED` so it cannot remain ``always_allowed``
    or ``approval_required``. Inserts any missing global rows.

    Called from :func:`db.connection.init_db` after :func:`seed_default_rules`.
    Settings → CLI Policy → Seed defaults remains the full wipe/reseed path.

    Returns the number of inserted or updated rows.
    """
    changes = 0
    for tier, pattern, match_mode, description, category, usage_syntax, help_text in _HARDENED_NEVER_ALLOWED:
        existing = fetch_all(
            f"""
            SELECT {_ALL_COLUMNS}
            FROM cli_policy_rules
            WHERE pattern = $1 AND match_mode = $2
            """,
            [pattern, match_mode],
            CliPolicyRule,
        )
        if not existing:
            execute(
                """
                INSERT INTO cli_policy_rules (
                    tier, pattern, match_mode, description,
                    category, usage_syntax, help_text
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                [tier, pattern, match_mode, description, category, usage_syntax, help_text],
            )
            changes += 1
            continue
        for rule in existing:
            if (
                rule.tier == tier
                and rule.description == description
                and rule.category == category
                and rule.usage_syntax == usage_syntax
                and rule.help_text == help_text
                and rule.enabled
            ):
                continue
            updated = update_rule(
                rule.id,
                tier=tier,
                description=description,
                category=category,
                usage_syntax=usage_syntax,
                help_text=help_text,
                enabled=True,
            )
            if updated is not None:
                changes += 1
    changes += _ensure_safe_diagnostic_seed_rules()
    return changes


_SAFE_DIAGNOSTIC_ALWAYS_ALLOWED = (
    ("always_allowed", "uname", "prefix", "Print kernel and OS identity.", "system",
     "uname [options]",
     "Print the kernel name and related system identity. Pathless diagnostic.\nExample: uname -a"),
)


def _ensure_safe_diagnostic_seed_rules() -> int:
    """Insert missing pathless diagnostic always-allowed seed rules.

    Does not overwrite an operator-customized existing ``uname`` row.
    """
    changes = 0
    for tier, pattern, match_mode, description, category, usage_syntax, help_text in _SAFE_DIAGNOSTIC_ALWAYS_ALLOWED:
        existing = query_one(
            "SELECT id FROM cli_policy_rules WHERE pattern = $1 AND match_mode = $2 AND agent_id IS NULL",
            [pattern, match_mode],
        )
        if existing is not None:
            continue
        execute(
            """
            INSERT INTO cli_policy_rules (
                tier, pattern, match_mode, description,
                category, usage_syntax, help_text
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            [tier, pattern, match_mode, description, category, usage_syntax, help_text],
        )
        changes += 1
    return changes
