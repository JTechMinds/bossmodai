"""Refuse to run tests against the operator's real data.

A test run once resolved the per-agent ``/me`` root to the checkout's real
``artifacts/agents`` and deleted a real agent's folder. Every root a test can
write through is checked here against the REAL install (this file's
checkout) and its sibling ``bossmod-data``. The check reads module constants
and environment values only; it creates nothing.

Used twice: by conftest.py before any test runs, and by
test_real_data_isolation.py so the rule is also a named, visible test.
"""

from __future__ import annotations

from pathlib import Path

REAL_INSTALL = Path(__file__).resolve().parents[1]
REAL_DATA = REAL_INSTALL.parent / "bossmod-data"


def _inside(path: Path, root: Path) -> bool:
    resolved = Path(path).resolve()
    real = root.resolve()
    return resolved == real or real in resolved.parents


def guarded_roots() -> dict[str, Path]:
    """Every root a test run can write through, by name. Nothing is created."""
    from core.bm_cli import filesystem
    from core.bm_cli.install_layout import default_company_root
    from db.company_layout import legacy_flat_projects_root
    from db.connection import database_path
    from db.secret_store import data_key_path

    company = default_company_root()
    return {
        "artifacts root": filesystem._ARTIFACTS_ROOT,
        "agents root": filesystem._AGENTS_ROOT,
        "reset backups": filesystem._ARTIFACTS_ROOT / "db_backups",
        "company root": company,
        "legacy flat projects root": legacy_flat_projects_root(company.resolve()),
        "database": database_path(),
        "migration backups": database_path().parent / "db_backups",
        "data key": data_key_path(),
    }


def real_data_violations() -> list[str]:
    """Each root that resolves inside the real checkout or the real data dir."""
    found: list[str] = []
    for name, path in guarded_roots().items():
        for label, real in (("the checkout", REAL_INSTALL), ("bossmod-data", REAL_DATA)):
            if _inside(path, real):
                found.append(f"{name} {path} is inside {label} ({real})")
    return found


def assert_no_real_data() -> None:
    """Raise when any test root points at real data.

    Raises:
        RuntimeError: Naming every offending root.
    """
    found = real_data_violations()
    if found:
        raise RuntimeError(
            "Refusing to run tests against real data:\n  " + "\n  ".join(found)
        )
