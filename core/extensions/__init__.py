"""BossMod AI — extension host.

An extension is a folder under ``<install>/extensions/`` holding a
``manifest.json`` and a Python package (``__init__.py`` exposing
``create(ctx)``). Folders are discovered once per process at start; a module
is imported only when its extension is enabled or being set up, so a broken
or disabled extension cannot break the app.

Both processes import this package: the app lists extensions and runs setup,
the runtime worker routes CLI commands and renders prompt blocks.
"""
