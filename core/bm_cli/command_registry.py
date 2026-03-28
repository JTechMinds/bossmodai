"""BossMod AI — Read-only metadata registry for built-in virtual commands.

Provides the canonical command metadata consumed by the help system and the
Virtual Commands UI tab.  All entries are frozen dataclasses; nothing here
mutates runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VirtualCommandMeta:
    """Immutable descriptor for a single built-in virtual command."""

    name: str
    category: str
    description: str
    usage_syntax: str
    help_text: str
    discovery_hint: str | None = None
    aliases: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------

VIRTUAL_CATEGORIES: dict[str, str] = {
    "files": "Inspect, create, and edit files",
    "git": "Version control",
    "agent": "Agent state and work history",
    "world": "Environment and physical context",
    "help": "Command discovery and reference",
}

# ---------------------------------------------------------------------------
# Command registry — every built-in virtual command
# ---------------------------------------------------------------------------

VIRTUAL_COMMAND_REGISTRY: dict[str, VirtualCommandMeta] = {
    # ── files ─────────────────────────────────────────────────────────────
    "pwd": VirtualCommandMeta(
        name="pwd",
        category="files",
        description="Print current working directory.",
        usage_syntax="pwd",
        help_text=(
            "Prints the absolute path of your current working directory.\n"
            "\n"
            "Examples:\n"
            "  pwd              — show where you are\n"
            "  pwd && ls        — show location then list contents"
        ),
    ),
    "cd": VirtualCommandMeta(
        name="cd",
        category="files",
        description="Change working directory.",
        usage_syntax="cd <path>",
        help_text=(
            "Change the current working directory. Paths can be absolute\n"
            "(/me/projects) or relative (../other). The new cwd persists\n"
            "for the rest of the turn.\n"
            "\n"
            "Examples:\n"
            "  cd /me/projects  — jump to projects root\n"
            "  cd ..            — move up one level\n"
            "  cd src/utils     — descend into a relative path"
        ),
    ),
    "ls": VirtualCommandMeta(
        name="ls",
        category="files",
        description="List directory contents.",
        usage_syntax="ls [path]",
        help_text=(
            "List files and directories at the given path, or the current\n"
            "directory when no path is supplied.\n"
            "\n"
            "Examples:\n"
            "  ls               — list cwd contents\n"
            "  ls /me/projects  — list a specific directory\n"
            "  ls src            — list a relative path"
        ),
    ),
    "cat": VirtualCommandMeta(
        name="cat",
        category="files",
        description="Read file contents.",
        usage_syntax="cat <path>",
        help_text=(
            "Print the contents of a file. Paths are resolved relative to\n"
            "the current working directory.\n"
            "\n"
            "Examples:\n"
            "  cat README.md          — read a file in cwd\n"
            "  cat /me/notes/todo.md  — read via absolute path"
        ),
        discovery_hint="full-file read; use for short files or final verification",
    ),
    "outline": VirtualCommandMeta(
        name="outline",
        category="files",
        description="List markdown headings with line numbers.",
        usage_syntax="outline <path>",
        help_text=(
            "Inspect the heading structure of a markdown document before a\n"
            "targeted edit. Returns markdown headings with 1-based line\n"
            "numbers so you can pick the right section precisely.\n"
            "\n"
            "Use this before replace-section or rewrite-section when you are\n"
            "not sure which heading to target.\n"
            "\n"
            "Examples:\n"
            "  outline proposal.md    — inspect document sections\n"
            "  outline /me/brief.md   — inspect by absolute path"
        ),
        discovery_hint="markdown heading map with line numbers; use before section edits",
        aliases=("ol",),
    ),
    "read-range": VirtualCommandMeta(
        name="read-range",
        category="files",
        description="Read a bounded line range with numbering.",
        usage_syntax="read-range <path> <start:end>",
        help_text=(
            "Read an exact line range from a file using 1-based inclusive\n"
            "line numbers. The runtime enforces a bounded maximum range.\n"
            "\n"
            "Use this for focused inspection before precise edits, especially\n"
            "for code, configs, or long documents.\n"
            "\n"
            "Examples:\n"
            "  read-range app.py 10:40            — read lines 10 through 40\n"
            "  read-range /me/brief.md 1:60       — read a bounded document slice"
        ),
        discovery_hint="bounded exact read with numbered lines; use for local context before precise edits",
        aliases=("rr", "lines"),
    ),
    "mkdir": VirtualCommandMeta(
        name="mkdir",
        category="files",
        description="Create a directory.",
        usage_syntax="mkdir <path>",
        help_text=(
            "Create a new directory. Parent directories are created\n"
            "automatically if they do not exist (like mkdir -p).\n"
            "\n"
            "Examples:\n"
            "  mkdir drafts             — create in cwd\n"
            "  mkdir /me/projects/new   — create via absolute path"
        ),
    ),

    "write": VirtualCommandMeta(
        name="write",
        category="files",
        description="Create or overwrite a file.",
        usage_syntax="write <path>",
        help_text=(
            "Create or overwrite a file at the given path. Pass the file\n"
            "content in the body field for short exact writes. If no body is\n"
            "provided, the runtime-managed authoring path is activated for\n"
            "generated document writing.\n"
            "\n"
            "Use this when you need one generated file.\n"
            "Use batch-write when you need multiple generated files.\n"
            "Use replace-section or rewrite-section when you are editing one\n"
            "part of an existing markdown document.\n"
            "Do not put a long paper or report body directly into JSON.\n"
            "\n"
            "Examples:\n"
            '  write notes.md   — with body: "# My Notes"\n'
            "  write config.json — with body containing JSON\n"
            "  write report.md   — no body to let the runtime author the file"
        ),
        discovery_hint="body = exact file text; no body = runtime-managed full-file authoring",
    ),
    "append": VirtualCommandMeta(
        name="append",
        category="files",
        description="Append text to a file.",
        usage_syntax="append <path>",
        help_text=(
            "Append text to the end of an existing file. The text to append\n"
            "is passed in the body field. Creates the file if it does not\n"
            "exist.\n"
            "\n"
            "Use append only for small additions such as notes, log entries,\n"
            "or short follow-up sections. Do not use it to dump a long-form\n"
            "generated document into CLI JSON.\n"
            "\n"
            "Examples:\n"
            '  append log.txt   — with body: "new entry"\n'
            '  append notes.md  — with body: "\\n## Section 2"'
        ),
        discovery_hint="body = small tail addition only; not for long-form generation",
    ),
    "batch-write": VirtualCommandMeta(
        name="batch-write",
        category="files",
        description="Author multiple files in one runtime-managed batch.",
        usage_syntax="batch-write",
        help_text=(
            "Create multiple generated files in one runtime-managed batch.\n"
            "Do not pass any path arguments in the command itself.\n"
            "Put a short manifest in the body instead of the full file\n"
            "contents. Each entry must include a path and a goal.\n"
            "\n"
            "The runtime will generate each file from its goal and save it.\n"
            "Do not include the actual file contents in the manifest.\n"
            "\n"
            "Use this when you need multiple generated files. Use\n"
            "write <path> with no body when you need one substantial file.\n"
            "\n"
            "Preferred manifest format:\n"
            "  /me/summary.md :: One-page executive summary\n"
            "  /me/appendix.md :: Detailed appendix with references\n"
            "\n"
            "JSON format is also accepted:\n"
            '  {"files":[{"path":"/me/summary.md","goal":"One-page executive summary"}]}\n'
            "\n"
            "Examples:\n"
            "  batch-write      — with body listing each file and goal"
        ),
        discovery_hint='body = short manifest of "path :: goal" entries; runtime authors each file',
        aliases=("bwrite",),
    ),
    "replace-section": VirtualCommandMeta(
        name="replace-section",
        category="files",
        description="Replace one markdown section with exact text.",
        usage_syntax='replace-section <path> "<heading>"',
        help_text=(
            "Replace only one markdown section body with the literal body text\n"
            "you put in the body field. The quoted heading selector may be\n"
            "either a bare title like \"Recommendation\" or a level-specific\n"
            "selector like \"## Recommendation\".\n"
            "\n"
            "Use this when you already know the exact replacement text.\n"
            "Use rewrite-section when you want the runtime to author the new\n"
            "section body from a short goal.\n"
            "\n"
            "Examples:\n"
            '  replace-section report.md "## Recommendation"   — with body containing the exact new section text\n'
            '  replace-section /me/brief.md "Summary"          — target a unique heading by title'
        ),
        discovery_hint='body = literal replacement section text; target by quoted markdown heading',
        aliases=("repsect", "rsect"),
    ),
    "rewrite-section": VirtualCommandMeta(
        name="rewrite-section",
        category="files",
        description="Rewrite one markdown section from a short goal.",
        usage_syntax='rewrite-section <path> "<heading>"',
        help_text=(
            "Rewrite only one markdown section through the runtime-managed\n"
            "authoring path. Put a short rewrite goal in the body instead of\n"
            "the final section text.\n"
            "\n"
            "The runtime reads the current document, targets the selected\n"
            "section, generates a new section body, and saves only that edit.\n"
            "Do not put the full rewritten section body into CLI JSON.\n"
            "\n"
            "Use outline first if you need to inspect the available headings.\n"
            "\n"
            "Examples:\n"
            '  rewrite-section report.md "## Recommendation"   — with body: "Make this shorter and more executive-friendly"\n'
            '  rewrite-section /me/brief.md "Risks"            — with body: "Add compliance risks and concrete mitigations"'
        ),
        discovery_hint='body = short rewrite goal; runtime rewrites only the targeted section',
        aliases=("rewsect", "rwsect"),
    ),

    # ── git ───────────────────────────────────────────────────────────────
    "git": VirtualCommandMeta(
        name="git",
        category="git",
        description="Version control operations.",
        usage_syntax="git <subcommand> [args]",
        help_text=(
            "Run read-only git operations inside the workspace.\n"
            "\n"
            "Supported subcommands:\n"
            "  status            — working tree status\n"
            "  log [N]           — recent commits (default 10)\n"
            "  diff [path]       — unstaged changes\n"
            "  show <ref>        — show a commit or object\n"
            "  restore <path>    — discard unstaged changes to a file\n"
            "\n"
            "Examples:\n"
            "  git status        — check for uncommitted changes\n"
            "  git log 5         — last 5 commits\n"
            "  git diff main.py  — diff a specific file"
        ),
    ),

    # ── agent ─────────────────────────────────────────────────────────────
    "status": VirtualCommandMeta(
        name="status",
        category="agent",
        description="Full status snapshot (runtime + tasks + artifacts).",
        usage_syntax="status",
        help_text=(
            "Return a comprehensive snapshot of your current state including\n"
            "runtime info, open tasks, recent completed tasks, and recent\n"
            "work artifacts.\n"
            "\n"
            "Examples:\n"
            "  status            — full snapshot for orientation"
        ),
    ),
    "runtime": VirtualCommandMeta(
        name="runtime",
        category="agent",
        description="Runtime state (position, status, energy).",
        usage_syntax="runtime",
        help_text=(
            "Return only the live runtime state block: current status,\n"
            "location, active activity, and bound task.\n"
            "\n"
            "Examples:\n"
            "  runtime           — quick check of runtime vitals"
        ),
    ),
    "current-task": VirtualCommandMeta(
        name="current-task",
        category="agent",
        description="Currently bound task details.",
        usage_syntax="current-task",
        help_text=(
            "Show details of the task you are currently working on,\n"
            "including its title, status, description, and any completion\n"
            "notes.\n"
            "\n"
            "Examples:\n"
            "  current-task      — see what you are working on right now"
        ),
    ),
    "tasks": VirtualCommandMeta(
        name="tasks",
        category="agent",
        description="Open and recent completed tasks.",
        usage_syntax="tasks",
        help_text=(
            "List your open (pending/accepted) tasks and recently completed\n"
            "tasks. Use this to review your backlog and recent history.\n"
            "\n"
            "Examples:\n"
            "  tasks             — review task backlog and recent completions"
        ),
    ),
    "recent-work": VirtualCommandMeta(
        name="recent-work",
        category="agent",
        description="Recent completed tasks and artifacts.",
        usage_syntax="recent-work",
        help_text=(
            "Show recently completed tasks and recent work artifacts. Useful\n"
            "for recalling what you have produced recently.\n"
            "\n"
            "Examples:\n"
            "  recent-work       — review your latest deliverables"
        ),
    ),

    # ── world ─────────────────────────────────────────────────────────────
    "activity": VirtualCommandMeta(
        name="activity",
        category="world",
        description="Current activity details.",
        usage_syntax="activity",
        help_text=(
            "Show details of your current runtime activity: kind, status,\n"
            "title, destination, and any attached metadata.\n"
            "\n"
            "Examples:\n"
            "  activity          — check what activity is in progress"
        ),
    ),
    "location": VirtualCommandMeta(
        name="location",
        category="world",
        description="Physical location in the world.",
        usage_syntax="location",
        help_text=(
            "Show your physical location on the world map including room\n"
            "name, coordinates, and runtime status.\n"
            "\n"
            "Examples:\n"
            "  location          — see where you are in the world"
        ),
    ),

    # ── help ──────────────────────────────────────────────────────────────
    "help": VirtualCommandMeta(
        name="help",
        category="help",
        description="Command reference entry point.",
        usage_syntax="help",
        help_text=(
            "Display the top-level command reference. Lists all categories\n"
            "with their commands. Start here when you need to discover\n"
            "available commands.\n"
            "\n"
            "Examples:\n"
            "  help              — show all command categories"
        ),
    ),
    "categories": VirtualCommandMeta(
        name="categories",
        category="help",
        description="Browse commands grouped by category.",
        usage_syntax="categories",
        help_text=(
            "List all command categories with a short description and\n"
            "a preview of the commands in each. Use fsearch to drill\n"
            "into a specific category.\n"
            "\n"
            "Examples:\n"
            "  categories        — browse all command categories"
        ),
    ),
    "fsearch": VirtualCommandMeta(
        name="fsearch",
        category="help",
        description="Search commands by category or keyword.",
        usage_syntax="fsearch <category|keyword>",
        help_text=(
            "Search for commands by category name or keyword. Matches\n"
            "against category names, command names, and descriptions\n"
            "(case-insensitive).\n"
            "\n"
            "Examples:\n"
            "  fsearch files      — list all file-work commands\n"
            "  fsearch git        — find git-related commands\n"
            "  fsearch section    — keyword search across all commands"
        ),
    ),
    "learn": VirtualCommandMeta(
        name="learn",
        category="help",
        description="Detailed usage for a specific command.",
        usage_syntax="learn <command>",
        help_text=(
            "Show detailed usage information for a specific command,\n"
            "including syntax, description, and examples. Works for both\n"
            "virtual and shell commands.\n"
            "\n"
            "Examples:\n"
            "  learn write       — full help for the write command\n"
            "  learn git         — full help for the git command\n"
            "  learn fsearch     — full help for fsearch"
        ),
    ),
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_virtual_command(name: str) -> VirtualCommandMeta | None:
    """Look up a virtual command by name. Returns None if not found."""
    canonical = resolve_virtual_command_name(name)
    if canonical is None:
        return None
    return VIRTUAL_COMMAND_REGISTRY.get(canonical)


def resolve_virtual_command_name(name: str) -> str | None:
    """Resolve a canonical command name from either the name or an alias."""
    normalized = name.strip().lower()
    if not normalized:
        return None
    if normalized in VIRTUAL_COMMAND_REGISTRY:
        return normalized
    return _VIRTUAL_COMMAND_ALIASES.get(normalized)


def search_virtual_commands(query: str) -> dict[str, list[VirtualCommandMeta]]:
    """Search commands by category name, command name, or description.

    Matching is case-insensitive.  Results are grouped by category, with
    categories ordered according to :data:`VIRTUAL_CATEGORIES`.
    """
    q = query.lower()
    matches: dict[str, list[VirtualCommandMeta]] = {}

    for cmd in VIRTUAL_COMMAND_REGISTRY.values():
        hit = (
            q in cmd.category.lower()
            or q in cmd.name.lower()
            or q in cmd.description.lower()
            or q in cmd.usage_syntax.lower()
            or (cmd.discovery_hint is not None and q in cmd.discovery_hint.lower())
            or any(q in alias.lower() for alias in cmd.aliases)
        )
        if hit:
            matches.setdefault(cmd.category, []).append(cmd)

    # Maintain canonical category order from VIRTUAL_CATEGORIES
    ordered: dict[str, list[VirtualCommandMeta]] = {}
    for cat in VIRTUAL_CATEGORIES:
        if cat in matches:
            ordered[cat] = matches[cat]
    return ordered


def list_virtual_categories() -> list[tuple[str, str, list[str]]]:
    """Return (category, description, [command_names]) ordered by category.

    The order follows :data:`VIRTUAL_CATEGORIES` key insertion order.
    """
    cat_commands: dict[str, list[str]] = {}
    for cmd in VIRTUAL_COMMAND_REGISTRY.values():
        cat_commands.setdefault(cmd.category, []).append(cmd.name)

    return [
        (cat, desc, cat_commands.get(cat, []))
        for cat, desc in VIRTUAL_CATEGORIES.items()
    ]


_VIRTUAL_COMMAND_ALIASES: dict[str, str] = {
    alias.lower(): meta.name
    for meta in VIRTUAL_COMMAND_REGISTRY.values()
    for alias in meta.aliases
}
