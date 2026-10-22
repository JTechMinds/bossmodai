"""BossMod AI — the runtime worker's calls into extensions after an operator change.

Only the runtime worker starts, stops or removes what an extension holds
(browsers, desktops). The app process files a runtime command, and the
worker's ``RuntimeController`` runs these functions in the worker's
extension lane (one job at a time, off the command loop, each call in a
thread via ``asyncio.to_thread``):

- ``extension_config_changed`` → :func:`notify_agent_config_changed`: one
  agent's settings for one extension were saved or removed;
- ``extensions_reconcile`` → :func:`apply_extension_states` (release every
  extension the operator turned off), then :func:`reconcile_agents` (drop
  what extensions hold for agents that no longer exist). The worker's boot
  job, the lane's first once the worker reports ready, runs
  :func:`reconcile_agents` too.

Every function is synchronous and never broadcasts. Each returns the ids of
the live-view extensions it called a hook on, so the async caller can tell
open live views to re-read (``extension_live(ext_id, None)``). One
extension's failure is logged with its traceback and never stops the next
extension or the worker's command loop.
"""

from __future__ import annotations

import logging

import db
from core.extensions.contract import SupportsAgentConfigChanged, SupportsAgentReconcile
from core.extensions.loader import ExtensionLoadError, load_extension, loaded_extensions, unload_extension
from core.extensions.registry import ExtensionEntry, enabled_ids, get_discovery

logger = logging.getLogger(__name__)


def notify_agent_config_changed(ext_id: str, agent_id: str) -> frozenset[str]:
    """Tell one extension that one agent's settings for it were saved or removed.

    The extension is loaded (it is enabled, so the import is allowed) and,
    when it implements ``SupportsAgentConfigChanged``, its
    ``agent_config_changed(agent_id)`` runs; it reads the new value itself.

    Failure modes:
        - An unknown, invalid or disabled ``ext_id`` is logged at WARNING and
          nothing is called: it was turned off before the command ran, and
          :func:`apply_extension_states` releases a turned-off extension.
        - An extension that cannot load is logged at ERROR with the
          traceback, and nothing is called.
        - An exception from ``agent_config_changed`` is logged at ERROR with
          the traceback, naming the extension and agent, and is not
          re-raised: the save already succeeded and the command loop must
          keep serving other commands. The operator sees the extension's
          own effect (e.g. a desktop not restarted reports its old specs on
          the agent's next status).

    Args:
        ext_id: The extension whose settings changed.
        agent_id: The agent whose settings changed.

    Returns:
        ``{ext_id}`` when the hook was called (even if it raised, since it
        may have changed state) and the extension declares a live view;
        otherwise empty.

    Raises:
        core.extensions.registry.ExtensionSettingError: The enabled setting
            is unreadable.
    """
    entry = get_discovery().get(ext_id)
    if entry is None or not entry.valid:
        logger.warning(
            "Settings change for agent %s names extension %s, which is unknown or invalid; nothing to tell",
            agent_id, ext_id,
        )
        return frozenset()
    if ext_id not in enabled_ids():
        logger.warning(
            "Settings change for agent %s names extension %s, which is off; nothing to tell",
            agent_id, ext_id,
        )
        return frozenset()
    try:
        instance = load_extension(entry)
    except ExtensionLoadError:
        logger.exception("Extension %s could not load to apply agent %s's settings change", ext_id, agent_id)
        return frozenset()
    if not isinstance(instance, SupportsAgentConfigChanged):
        return frozenset()
    try:
        instance.agent_config_changed(agent_id)
    except Exception:
        # Extension code may raise anything; see the docstring for why the
        # host logs it and carries on.
        logger.exception("Extension %s failed to apply agent %s's settings change", ext_id, agent_id)
    return _live_view_ids([entry])


def apply_extension_states() -> frozenset[str]:
    """Release every extension this process created that the operator has since turned off.

    Each one's ``shutdown()`` runs, then it is unloaded, so turning it on
    again creates a fresh instance. Extensions still enabled, and ones this
    process never created, are not touched.

    Failure modes:
        - A ``shutdown()`` that raises is logged at ERROR with the traceback
          (as ``shutdown_loaded_extensions`` does) and the extension is
          unloaded anyway: an instance whose shutdown failed is in an
          unknown state and is never reused.
        - A released id missing from this process's discovery is logged at
          ERROR (every loaded id comes from it, so that is a bug) and gets
          no live-view nudge.

    Returns:
        The ids of the released extensions that declare a live view.

    Raises:
        core.extensions.registry.ExtensionSettingError: The enabled setting
            is unreadable.
    """
    discovery = get_discovery()
    enabled = enabled_ids()
    released: list[ExtensionEntry] = []
    for ext_id, instance in loaded_extensions().items():
        if ext_id in enabled:
            continue
        try:
            instance.shutdown()
        except Exception:
            logger.exception("Extension %s failed to shut down after it was turned off", ext_id)
        unload_extension(ext_id)
        logger.info("Released extension %s: it was turned off", ext_id)
        entry = discovery.get(ext_id)
        if entry is None or entry.manifest is None:
            logger.error("Released extension %s is not in this process's discovery; no live-view nudge", ext_id)
            continue
        released.append(entry)
    return _live_view_ids(released)


def reconcile_agents() -> frozenset[str]:
    """Tell every enabled extension which agents exist, so it drops what it holds for the rest.

    Reads the agent ids once, then loads each valid, enabled extension and,
    when it implements ``SupportsAgentReconcile``, calls
    ``reconcile_agents(existing)``. Run at worker boot and after agents are
    deleted, after the rows are gone, so a deleted agent reads as missing.

    Failure modes:
        - An extension that cannot load is logged at ERROR with the
          traceback, and the next extension still runs.
        - An exception from ``reconcile_agents`` (e.g. Docker not running)
          is logged at ERROR with the traceback, and the next extension
          still runs; its resources stay until the next reconcile.

    Returns:
        The ids of the extensions whose hook was called (even if it raised)
        that declare a live view.

    Raises:
        core.extensions.registry.ExtensionSettingError: The enabled setting
            is unreadable.
    """
    existing = frozenset(agent.id for agent in db.list_agents())
    enabled = enabled_ids()
    called: list[ExtensionEntry] = []
    for entry in get_discovery().valid_entries():
        if entry.id not in enabled:
            continue
        try:
            instance = load_extension(entry)
        except ExtensionLoadError:
            logger.exception("Extension %s could not load to reconcile its agents", entry.id)
            continue
        if not isinstance(instance, SupportsAgentReconcile):
            continue
        try:
            instance.reconcile_agents(existing)
        except Exception:
            # Extension code may raise anything; one extension's failure must
            # not keep the others from releasing what deleted agents held.
            logger.exception("Extension %s failed to reconcile its agents", entry.id)
        called.append(entry)
    return _live_view_ids(called)


def _live_view_ids(entries: list[ExtensionEntry]) -> frozenset[str]:
    """The ids of the (valid) entries whose manifest declares a live view."""
    return frozenset(entry.id for entry in entries if entry.manifest.live_view is not None)
