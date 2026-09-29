"""BossMod AI — the working-prompt block each applicable extension adds."""

from __future__ import annotations

import logging
from typing import Any

from core.agent_loop.turn_context import _determine_mode
from core.extensions.contract import SupportsPromptState
from core.extensions.loader import ExtensionLoadError, load_extension
from core.extensions.registry import Discovery, ExtensionEntry, enabled_ids, get_discovery
from core.extensions.setup_runner import entry_setup_status
from core.llm.routing import select_model
from core.models import Agent
from db.extension_agent_configs import get_extension_agent_config
from db.model_capabilities import supports_images

logger = logging.getLogger(__name__)


def render_extension_blocks(
    agent: Agent,
    trigger: dict[str, Any],
    discovery: Discovery | None = None,
) -> str | None:
    """Return the prompt text of every extension that applies to this turn.

    An extension applies when it is valid, enabled, set up (or needs no
    setup), when it requires an image model the model this turn is routed
    to (the same mode/model the agent loop picks for ``trigger``) is flagged
    image-capable, and — when its manifest declares ``agent_config`` — this
    agent has a stored config (an agent without a mailbox is not told about
    ``mail``).

    Each block is the extension's static prompt text, then — when the
    extension implements ``prompt_state`` — its state line last. The state
    changes between turns, so keeping it after the static text keeps
    everything before it cache-stable. Asking loads the extension (it is
    enabled, so D10 allows the import); an extension that fails to load is
    logged at error level and contributes its static text only, the same
    failure its command reports as ``EXTENSION_LOAD_FAILED``.

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
        ValueError: An agent's stored extension config is corrupt.
        Exception: Whatever an extension's ``prompt_state`` raises (e.g.
            Browser Vision's browser thread not answering), unchanged.
    """
    found = discovery if discovery is not None else get_discovery()
    enabled = enabled_ids()
    model = select_model(agent, _determine_mode(trigger))
    blocks = [
        _block(entry, agent)
        for entry in found.valid_entries()
        if entry.id in enabled and _applies(entry, model, agent)
    ]
    blocks = [block for block in blocks if block]
    return "\n\n".join(blocks) if blocks else None


def _applies(entry: ExtensionEntry, model: str | None, agent: Agent) -> bool:
    if entry_setup_status(entry).state not in {"ready", "not_required"}:
        return False
    if entry.manifest.requires.image_model and not (model is not None and supports_images(model)):
        return False
    if entry.manifest.agent_config is not None:
        return get_extension_agent_config(entry.id, agent.id) is not None
    return True


def _block(entry: ExtensionEntry, agent: Agent) -> str:
    parts = [_prompt_text(entry), _prompt_state(entry, agent)]
    return "\n\n".join(part for part in parts if part)


def _prompt_state(entry: ExtensionEntry, agent: Agent) -> str | None:
    try:
        instance = load_extension(entry)
    except ExtensionLoadError as exc:
        logger.error("Extension %s: no state line in the prompt, it failed to load (%s)", entry.id, exc)
        return None
    if not isinstance(instance, SupportsPromptState):
        return None
    return instance.prompt_state(agent)


def _prompt_text(entry: ExtensionEntry) -> str:
    if entry.manifest.prompt is None:
        return ""
    return (entry.root / entry.manifest.prompt).read_text(encoding="utf-8").strip()
