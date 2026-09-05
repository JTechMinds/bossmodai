"""BossMod AI — Managed file authoring behind BossMod CLI.

Package peel of the former managed_writer.py. Public entrypoints are unchanged:
is_managed_*_request, run_managed_write, run_managed_batch_write,
run_managed_section_rewrite, ManagedWriteOutcome, ManagedWriteProgress.
"""

from core.bm_cli.managed_writer.batch import run_managed_batch_write
from core.bm_cli.managed_writer.detect import (
    is_managed_batch_write_request,
    is_managed_section_rewrite_request,
    is_managed_write_request,
)
from core.bm_cli.managed_writer.section import run_managed_section_rewrite
from core.bm_cli.managed_writer.types import ManagedWriteOutcome, ManagedWriteProgress
from core.bm_cli.managed_writer.write import run_managed_write

__all__ = [
    "ManagedWriteOutcome",
    "ManagedWriteProgress",
    "is_managed_batch_write_request",
    "is_managed_section_rewrite_request",
    "is_managed_write_request",
    "run_managed_batch_write",
    "run_managed_section_rewrite",
    "run_managed_write",
]
