"""BossMod AI — Help and discovery virtual BossMod CLI commands."""

from __future__ import annotations

import logging

import db
from core import config
from core.bm_cli.command_registry import (
    get_virtual_command,
    list_virtual_categories,
    search_virtual_commands,
)
from core.bm_cli.results import error_result, success_result
from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand
from core.models.cli_policy import CliPolicyRule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _shell_enabled() -> bool:
    """Return True when the operator has enabled shell execution."""
    try:
        return config.get("cli_shell_enabled") == "true"
    except Exception:
        return False


def _default_policy() -> str:
    """Return the configured default shell policy (deny when unset)."""
    try:
        return config.get("cli_default_policy") or "deny"
    except Exception:
        return "deny"


def _get_shell_rules() -> list[CliPolicyRule]:
    """Fetch all enabled shell policy rules from the DB."""
    try:
        return db.list_cli_policy_rules(enabled_only=True)
    except Exception:
        logger.debug("Failed to load shell policy rules", exc_info=True)
        return []


def _get_shell_categories() -> list[tuple[str, int, list[str]]]:
    """Return (category, count, [pattern_previews]) grouped from DB rules."""
    rules = _get_shell_rules()
    groups: dict[str, list[str]] = {}
    for rule in rules:
        groups.setdefault(rule.category, []).append(rule.pattern)

    result: list[tuple[str, int, list[str]]] = []
    for cat, patterns in sorted(groups.items()):
        preview = patterns[:3]
        result.append((cat, len(patterns), preview))
    return result


def _search_shell_commands(query: str) -> dict[str, list[CliPolicyRule]]:
    """Search shell policy rules by matching query against category, pattern, description."""
    q = query.lower()
    rules = _get_shell_rules()
    matches: dict[str, list[CliPolicyRule]] = {}
    for rule in rules:
        hit = (
            q in rule.category.lower()
            or q in rule.pattern.lower()
            or (rule.description and q in rule.description.lower())
        )
        if hit:
            matches.setdefault(rule.category, []).append(rule)
    return matches


def _find_shell_command(cmd_name: str) -> CliPolicyRule | None:
    """Find the best matching shell policy rule for a command name.

    Prefers an exact match on the first token of the pattern, then falls back
    to a prefix match.
    """
    rules = _get_shell_rules()
    name_lower = cmd_name.lower()

    # Pass 1: exact match on first token
    for rule in rules:
        first_token = rule.pattern.split()[0].lower() if rule.pattern.strip() else ""
        if first_token == name_lower:
            return rule

    # Pass 2: prefix match
    for rule in rules:
        first_token = rule.pattern.split()[0].lower() if rule.pattern.strip() else ""
        if first_token.startswith(name_lower):
            return rule

    return None


def _tier_tag(tier: str) -> str:
    """Return a human-friendly tag for a policy tier."""
    tags: dict[str, str] = {
        "approval_required": "(approval required)",
        "always_allowed": "(allowed)",
        "never_allowed": "(blocked)",
    }
    return tags.get(tier, f"({tier})")


def _render_virtual_discovery_line(cmd) -> str:
    """Render one compact command line with syntax and AI-facing usage hint."""
    hint = cmd.discovery_hint or cmd.description
    return f"  {cmd.usage_syntax} — {hint}"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_help(
    ctx: CliExecutionContext,
    parsed: ParsedCliCommand,
    content: str | None = None,
) -> BossModCliResult:
    """Entry-point help: show discovery commands and shell status."""
    shell_on = _shell_enabled()

    lines = [
        "Run commands by typing their name. Browse by category or search to find what you need.",
        "",
        "  categories        — browse command categories",
        "  fsearch <query>   — search commands with syntax and usage hints",
        "  learn <command>   — detailed usage for a specific command",
    ]

    if shell_on:
        lines.append("")
        lines.append("Additional commands are available. Some may require operator approval.")
    else:
        lines.append("")
        lines.append("Only built-in commands are currently available.")

    return success_result(
        command=parsed.raw,
        detail="BossMod CLI help reference",
        kind="help",
        data={"shell_enabled": shell_on},
        sections=[("HELP", lines)],
        cwd=ctx.cwd,
    )


def handle_commands(
    ctx: CliExecutionContext,
    parsed: ParsedCliCommand,
    content: str | None = None,
) -> BossModCliResult:
    """List command categories (virtual always, shell when enabled)."""
    sections: list[tuple[str, list[str]]] = []

    # Virtual categories
    virtual_cats = list_virtual_categories()
    v_lines: list[str] = []
    for cat, desc, cmd_names in virtual_cats:
        preview = ", ".join(cmd_names)
        v_lines.append(f"  {cat} — {desc} ({preview})")
    sections.insert(0, ("AVAILABLE CATEGORIES", v_lines))

    # Shell categories (only when enabled)
    shell_on = _shell_enabled()
    if shell_on:
        shell_cats = _get_shell_categories()
        if shell_cats:
            s_lines: list[str] = []
            for cat, count, previews in shell_cats:
                preview = ", ".join(previews[:3])
                s_lines.append(f"  {cat} — {count} rule(s) ({preview})")
            sections.append(("ADDITIONAL CATEGORIES (policy-controlled)", s_lines))
        else:
            sections.append(("ADDITIONAL CATEGORIES (policy-controlled)", ["No policy rules configured."]))

    footer_lines = ['Type "fsearch <category|keyword>" to review the commands inside a category.']
    sections.append(("HINT", footer_lines))

    return success_result(
        command=parsed.raw,
        detail="Listed command categories",
        kind="commands",
        data={"shell_enabled": shell_on},
        sections=sections,
        cwd=ctx.cwd,
    )


def handle_fsearch(
    ctx: CliExecutionContext,
    parsed: ParsedCliCommand,
    content: str | None = None,
) -> BossModCliResult:
    """Search commands by category or keyword."""
    if not parsed.args:
        return error_result(
            parsed.raw,
            'Missing argument. Usage: fsearch <category|keyword>',
            cwd=ctx.cwd,
        )

    query = " ".join(parsed.args)
    sections: list[tuple[str, list[str]]] = []
    found = False

    # Virtual matches
    v_matches = search_virtual_commands(query)
    for cat, cmds in v_matches.items():
        lines: list[str] = []
        for cmd in cmds:
            lines.append(_render_virtual_discovery_line(cmd))
        sections.append((f" {cat.upper()}", lines))
        found = True

    # Shell matches (when enabled)
    shell_on = _shell_enabled()
    if shell_on:
        s_matches = _search_shell_commands(query)
        for cat, rules in s_matches.items():
            lines = []
            for rule in rules:
                desc = rule.description or rule.pattern
                tag = _tier_tag(rule.tier)
                lines.append(f"  {rule.pattern} — {desc} {tag}")
            sections.append((cat.upper(), lines))
            found = True

    if not found:
        return success_result(
            command=parsed.raw,
            detail=f'No commands match "{query}"',
            kind="fsearch",
            data={"query": query, "results": []},
            sections=[
                ("SEARCH RESULTS", [f'No commands match "{query}". Type "commands" to see available categories.']),
            ],
            cwd=ctx.cwd,
        )

    footer_lines = ['Type "learn <command>" only when the one-line usage hint is not enough.']
    sections.append(("HINT", footer_lines))

    return success_result(
        command=parsed.raw,
        detail=f'Search results for "{query}"',
        kind="fsearch",
        data={"query": query},
        sections=sections,
        cwd=ctx.cwd,
    )


def handle_learn(
    ctx: CliExecutionContext,
    parsed: ParsedCliCommand,
    content: str | None = None,
) -> BossModCliResult:
    """Detailed usage for a specific command."""
    if not parsed.args:
        return error_result(
            parsed.raw,
            'Missing argument. Usage: learn <command>',
            cwd=ctx.cwd,
        )

    cmd_name = parsed.args[0].lower()

    # Check virtual commands first
    vcmd = get_virtual_command(cmd_name)
    if vcmd is not None:
        lines = [
            f"  Command:   {vcmd.name}",
            f"  Category:  {vcmd.category}",
            f"  Usage:     {vcmd.usage_syntax}",
        ]
        if vcmd.discovery_hint:
            lines.extend(["", f"Quick use: {vcmd.discovery_hint}"])
        lines.extend(["", vcmd.help_text])
        return success_result(
            command=parsed.raw,
            detail=f'Usage info for "{cmd_name}"',
            kind="learn",
            data={"command": cmd_name},
            sections=[(f"COMMAND: {vcmd.name}", lines)],
            cwd=ctx.cwd,
        )

    # Check shell commands (when enabled)
    shell_on = _shell_enabled()
    if shell_on:
        shell_cmd = _find_shell_command(cmd_name)
        if shell_cmd is not None:
            lines = [
                f"  Command:   {shell_cmd.pattern}",
                f"  Category:  {shell_cmd.category}",
                f"  Policy:    {shell_cmd.tier} {_tier_tag(shell_cmd.tier)}",
                f"  Usage:     {shell_cmd.usage_syntax or shell_cmd.pattern}",
            ]
            if shell_cmd.help_text:
                lines.append("")
                lines.append(shell_cmd.help_text)
            return success_result(
                command=parsed.raw,
                detail=f'Usage info for "{cmd_name}"',
                kind="learn",
                data={"command": cmd_name},
                sections=[(f"COMMAND: {shell_cmd.pattern}", lines)],
                cwd=ctx.cwd,
            )

    # Not found
    default = _default_policy()
    lines = [
        f'"{cmd_name}" is not a recognized command.',
        f"Default shell policy: {default}.",
        "",
        'Type "fsearch <keyword>" to search available commands.',
    ]
    return success_result(
        command=parsed.raw,
        detail=f'Command "{cmd_name}" not found',
        kind="learn",
        data={"command": cmd_name, "type": "not_found"},
        sections=[("NOT FOUND", lines)],
        cwd=ctx.cwd,
    )
