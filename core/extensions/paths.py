"""BossMod AI — where extensions live on disk."""

from __future__ import annotations

from pathlib import Path

# core.bm_cli is imported inside the functions: importing it runs the CLI
# runtime, which registers extension commands through this package, so a
# top-level import here would be circular.

# A dot folder at the company top level: not a floor, so no agent's /projects
# reaches it, and the company file browser hides top-level dot entries.
_DATA_DIRNAME = ".extensions"


def extensions_root() -> Path:
    """Return the folder extensions are discovered in (``<install>/extensions``)."""
    from core.bm_cli.install_layout import app_install_root

    return app_install_root() / "extensions"


def extension_data_dir(ext_id: str) -> Path:
    """Return one extension's private data dir (not created here).

    Args:
        ext_id: A validated extension id.

    Returns:
        ``<company root>/.extensions/<ext_id>``.
    """
    from core.bm_cli.floor_roots import company_root

    return company_root() / _DATA_DIRNAME / ext_id
