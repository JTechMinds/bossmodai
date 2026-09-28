"""BossMod AI — the working-prompt block each applicable extension adds."""

from __future__ import annotations

from typing import Any

from core.agent_loop.turn_context import _determine_mode
from core.extensions.registry import Discovery, ExtensionEntry, enabled_ids, get_discovery
from core.extensions.setup_runner import entry_setup_status
from core.llm.routing import select_model
from core.models import Agent
from db.model_capabilities import supports_images


def render_extension_blocks(
    agent: Agent,
    trigger: dict[str, Any],
    discovery: Discovery | None = None,
) -> str | None:
    """Return the prompt text of every extension that applies to this turn.

    An extension applies when it is valid, enabled, set up (or needs no
    setup), and — when it requires an image model — the model this turn is
    routed to (the same mode/model the agent loop picks for ``trigger``) is
    flagged image-capable.

    Args:
        agent: The agent the prompt is for.
        trigger: This turn's trigger; it decides the routing mode.
        discovery: Defaults to this process's discovery.

    Returns:
        The blocks joined by a blank line, or ``None`` when none apply.

    Raises:
        OSError: A valid extension's prompt file cannot be read.
        core.extensions.registry.ExtensionSettingError: The enabled setting
            is unreadable.
    """
    found = discovery if discovery is not None else get_discovery()
    enabled = enabled_ids()
    model = select_model(agent, _determine_mode(trigger))
    blocks = [
        _prompt_text(entry)
        for entry in found.valid_entries()
        if entry.id in enabled and _applies(entry, model)
    ]
    blocks = [block for block in blocks if block]
    return "\n\n".join(blocks) if blocks else None


def _applies(entry: ExtensionEntry, model: str | None) -> bool:
    if entry_setup_status(entry).state not in {"ready", "not_required"}:
        return False
    if entry.manifest.requires.image_model:
        return model is not None and supports_images(model)
    return True


def _prompt_text(entry: ExtensionEntry) -> str:
    if entry.manifest.prompt is None:
        return ""
    return (entry.root / entry.manifest.prompt).read_text(encoding="utf-8").strip()
