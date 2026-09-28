"""BossMod AI — import an extension package and create its instance.

An extension folder is imported as a package under a private module name, so
its modules can use relative imports and two extensions never clash on
module names. Each extension is created once per process.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from types import ModuleType

from core.extensions.contract import Extension, ExtensionContext, SupportsLiveView
from core.extensions.paths import extension_data_dir
from core.extensions.registry import ExtensionEntry

logger = logging.getLogger(__name__)

_PACKAGE_PREFIX = "bossmod_extension_"


class ExtensionLoadError(Exception):
    """An extension could not be imported or created."""


_loaded: dict[str, Extension] = {}
# Extensions that loaded but broke the contract their manifest declares. They
# are invalid for the rest of the process; the static discovery cannot see
# this without importing, which D10 forbids.
_contract_failures: dict[str, str] = {}
_lock = threading.Lock()


def package_name(ext_id: str) -> str:
    """Return the private module name an extension is imported under."""
    return _PACKAGE_PREFIX + ext_id.replace("-", "_")


def import_package(entry: ExtensionEntry) -> ModuleType:
    """Import (once) and return the extension's package module.

    Args:
        entry: A valid discovered extension.

    Returns:
        The package module; submodules are importable as
        ``<package_name>.<module>``.

    Raises:
        ExtensionLoadError: The entry is invalid, or importing it raised.
    """
    if not entry.valid:
        raise ExtensionLoadError(f"extension {entry.id} is invalid: {entry.invalid_reason}")
    name = package_name(entry.id)
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        name,
        entry.root / "__init__.py",
        submodule_search_locations=[str(entry.root)],
    )
    if spec is None or spec.loader is None:
        raise ExtensionLoadError(f"extension {entry.id}: cannot build an import spec for {entry.root}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        # Any import-time failure of third-party code: drop the half-built
        # module and re-raise with the extension named.
        del sys.modules[name]
        raise ExtensionLoadError(f"extension {entry.id} failed to import: {exc}") from exc
    return module


def load_extension(entry: ExtensionEntry) -> Extension:
    """Return the process's instance of an extension, creating it on first use.

    Args:
        entry: A valid discovered extension.

    Returns:
        The object ``create(ctx)`` returned.

    Raises:
        ExtensionLoadError: The entry is invalid, the package failed to
            import, it has no ``create``, ``create`` raised, or the instance
            lacks a method its manifest declares (``live_view``) — that last
            one also marks the extension invalid (see :func:`contract_failure`).
    """
    with _lock:
        existing = _loaded.get(entry.id)
        if existing is not None:
            return existing
        failure = _contract_failures.get(entry.id)
        if failure is not None:
            raise ExtensionLoadError(failure)
        module = import_package(entry)
        create = getattr(module, "create", None)
        if not callable(create):
            raise ExtensionLoadError(f"extension {entry.id} has no create(ctx) function")
        manifest = entry.manifest
        if manifest is None:
            raise ExtensionLoadError(f"extension {entry.id} has no manifest")
        ctx = ExtensionContext(manifest=manifest, data_dir=extension_data_dir(entry.id))
        try:
            instance = create(ctx)
        except Exception as exc:
            raise ExtensionLoadError(f"extension {entry.id} failed to start: {exc}") from exc
        if manifest.live_view and not isinstance(instance, SupportsLiveView):
            reason = "manifest declares live_view but the extension has no live_view() method"
            _contract_failures[entry.id] = reason
            raise ExtensionLoadError(reason)
        _loaded[entry.id] = instance
        logger.info("Loaded extension %s", entry.id)
        return instance


def contract_failure(ext_id: str) -> str | None:
    """Return why a loaded extension broke its declared contract, else ``None``."""
    with _lock:
        return _contract_failures.get(ext_id)


def loaded_extension(ext_id: str) -> Extension | None:
    """Return the instance if this process already created it, else ``None``."""
    with _lock:
        return _loaded.get(ext_id)


def shutdown_loaded_extensions() -> None:
    """Call ``shutdown()`` on every extension this process created.

    Each extension is shut down even when an earlier one raised; the failures
    are logged with their tracebacks, since process shutdown must finish.
    """
    with _lock:
        instances = list(_loaded.items())
    for ext_id, instance in instances:
        try:
            instance.shutdown()
        except Exception:
            logger.exception("Extension %s failed to shut down", ext_id)
