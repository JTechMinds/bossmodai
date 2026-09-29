"""BossMod AI — extension commands in the BossMod CLI.

Each valid extension adds one virtual command (its ``command.name``). The
handler is registered at import from the static discovery, so the command is
always known to the parser and policy (virtual commands are auto-allowed);
whether it RUNS is decided per call from the live enabled set.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Callable

from core.extensions.loader import ExtensionLoadError, load_extension, loaded_extension
from core.extensions.registry import Discovery, ExtensionEntry, enabled_ids, get_discovery, is_enabled

if TYPE_CHECKING:
    from core.bm_cli.command_registry import VirtualCommandMeta
    from core.bm_cli.types import BossModCliResult, CliExecutionContext, ParsedCliCommand

# core.bm_cli is imported inside the functions: the CLI runtime imports this
# module while it is itself being imported, so a top-level import of any
# core.bm_cli module from here would be circular.

# Same shape as core.bm_cli.runtime.CliHandler; defined here because the
# runtime imports this module.
CliHandler = Callable[["CliExecutionContext", "ParsedCliCommand", "str | None"], "BossModCliResult"]

EXTENSIONS_CATEGORY = "extensions"


def extension_handlers(discovery: Discovery | None = None) -> dict[str, CliHandler]:
    """Return one CLI handler per valid extension, keyed by command name.

    Args:
        discovery: Defaults to this process's discovery.

    Returns:
        ``{command name: handler}``.
    """
    found = discovery if discovery is not None else get_discovery()
    return {entry.manifest.command.name: _handler_for(entry) for entry in found.valid_entries()}


def extension_command_meta(discovery: Discovery | None = None) -> dict[str, VirtualCommandMeta]:
    """Return help metadata for every valid extension's command, enabled or not."""
    found = discovery if discovery is not None else get_discovery()
    return {entry.manifest.command.name: _meta_for(entry) for entry in found.valid_entries()}


def enabled_extension_command_meta(discovery: Discovery | None = None) -> dict[str, VirtualCommandMeta]:
    """Return help metadata for the commands of enabled extensions only.

    Raises:
        core.extensions.registry.ExtensionSettingError: The enabled setting
            is unreadable.
    """
    found = discovery if discovery is not None else get_discovery()
    enabled = enabled_ids()
    return {
        entry.manifest.command.name: _meta_for(entry)
        for entry in found.valid_entries()
        if entry.id in enabled
    }


def _meta_for(entry: ExtensionEntry) -> VirtualCommandMeta:
    from core.bm_cli.command_registry import VirtualCommandMeta

    command = entry.manifest.command
    return VirtualCommandMeta(
        name=command.name,
        category=EXTENSIONS_CATEGORY,
        description=command.summary,
        usage_syntax=command.usage,
        help_text=command.help,
    )


def _stamped(result: BossModCliResult, ext_id: str) -> BossModCliResult:
    """Return ``result`` with ``data["extension_id"] = ext_id`` merged in.

    Core reads the stamp to learn which extension produced a result (see
    ``turn_helpers.announce_extension_result``) without parsing command names.
    """
    return replace(result, data={**(result.data or {}), "extension_id": ext_id})


def _handler_for(entry: ExtensionEntry) -> CliHandler:
    name = entry.manifest.name

    def run(ctx: CliExecutionContext, parsed: ParsedCliCommand, body: str | None) -> BossModCliResult:
        from core.bm_cli.results import error_result

        if not is_enabled(entry.id):
            # A disabled extension releases what it held (e.g. browsers) the
            # first time it is called after the operator turned it off.
            instance = loaded_extension(entry.id)
            if instance is not None:
                instance.shutdown()
            return error_result(
                parsed.raw,
                f"EXTENSION_DISABLED: {name} is off (Add → Extensions)",
                cwd=ctx.cwd,
            )
        try:
            instance = load_extension(entry)
        except ExtensionLoadError as exc:
            return error_result(parsed.raw, f"EXTENSION_LOAD_FAILED: {exc}", cwd=ctx.cwd)
        return instance.handle(ctx, parsed, body)

    def handler(ctx: CliExecutionContext, parsed: ParsedCliCommand, body: str | None) -> BossModCliResult:
        # Every result this extension's command returns, success or error, is
        # stamped, so the disable path's EXTENSION_DISABLED is announced too.
        return _stamped(run(ctx, parsed, body), entry.id)

    return handler
