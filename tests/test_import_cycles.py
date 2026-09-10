"""Cold-import guards for modules that used to sit inside an import cycle.

``core.agent_pack.schema`` once imported ``suggest_finish_line`` from
``core.agent_loop.role_contracts``, which reaches ``core.agent_loop.deliverables``
-> ``db`` -> ``core.bm_cli`` -> ``core.tasking.board`` -> back into
``role_contracts`` while it is still initialising. That made
``import core.agent_pack`` succeed only when ``db`` happened to be imported
first, and forced a function-local import of ``describe_pack`` in
``core.models.agent_template``. ``core.agent_loop.specialty`` now holds the pure
half, and ``core.agent_pack`` no longer touches the cycle.

Every check here runs in a **fresh interpreter**. Importing these modules inside
the pytest process proves nothing: ``conftest`` and sibling test modules have
already pulled ``db`` into ``sys.modules``, and ``db`` being imported first is
exactly what used to paper the cycle over.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Entry points that must work on their own. Each one reaches core.agent_pack,
# directly or through core.models.agent_template's module-level describe_pack.
COLD_IMPORT_MODULES = [
    "core.agent_pack",
    "core.agent_pack.service",
    "core.models.agent_template",
]

# core.agent_loop.specialty only breaks the cycle for as long as it stays a
# leaf. Importing it must not drag in the database, the CLI, tasking, or the
# rest of the agent loop.
_LEAF_PROBE = """
import json
import sys

import core.agent_loop.specialty  # noqa: F401

forbidden = sorted(
    name
    for name in sys.modules
    if name == "db"
    or name.startswith(("db.", "core.tasking", "core.bm_cli"))
    or (name.startswith("core.agent_loop.") and name != "core.agent_loop.specialty")
)
print(json.dumps(forbidden))
"""


def _run_cold(source: str) -> subprocess.CompletedProcess[str]:
    """Run ``source`` in a fresh interpreter rooted at the repo, capturing output.

    The child inherits this process's environment (so ``BOSSMOD_DB_PATH`` from
    ``conftest`` still points at the throwaway test database) but shares none of
    its already-imported modules, which is the whole point of the check.
    """
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("module", COLD_IMPORT_MODULES)
def test_module_imports_in_a_fresh_interpreter(module: str) -> None:
    """``import <module>`` must succeed with nothing else imported first."""
    result = _run_cold(f"import {module}")
    assert result.returncode == 0, (
        f"`import {module}` failed in a fresh interpreter "
        f"(exit {result.returncode}):\n{result.stderr}"
    )


def test_specialty_module_stays_a_leaf() -> None:
    """The cycle-breaking module must not import db, tasking, the CLI, or the loop."""
    result = _run_cold(_LEAF_PROBE)
    assert result.returncode == 0, (
        f"importing core.agent_loop.specialty failed:\n{result.stderr}"
    )
    forbidden = result.stdout.strip().splitlines()[-1]
    assert forbidden == "[]", (
        "core.agent_loop.specialty must stay a leaf; it pulled in: "
        f"{forbidden}. Anything needing db/tasking/bm_cli/the agent loop "
        "belongs in core.agent_loop.role_contracts instead."
    )
