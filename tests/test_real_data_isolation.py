"""No test root may resolve into the real checkout or the real ``bossmod-data``.

conftest.py already refuses to start on a violation; this names the rule as a
test and checks that the check itself can see a real path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.bm_cli import filesystem
from tests._real_data_guard import (
    REAL_DATA,
    REAL_INSTALL,
    assert_no_real_data,
    guarded_roots,
    real_data_violations,
)


def test_the_guard_measures_against_the_real_install() -> None:
    # This file sits in <checkout>/tests: the real install, not a stand-in.
    assert REAL_INSTALL == Path(__file__).resolve().parents[1]
    assert (REAL_INSTALL / "core" / "bm_cli" / "filesystem.py").is_file()
    assert REAL_DATA == REAL_INSTALL.parent / "bossmod-data"


def test_every_test_root_is_outside_real_data() -> None:
    roots = guarded_roots()
    assert set(roots) >= {
        "artifacts root", "agents root", "reset backups", "company root",
        "legacy flat projects root", "database", "migration backups", "data key",
    }
    assert real_data_violations() == []
    assert_no_real_data()


def test_the_guard_catches_a_real_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pure path swaps; nothing is created or touched at the real location.
    monkeypatch.setattr(filesystem, "_AGENTS_ROOT", REAL_INSTALL / "artifacts" / "agents")
    monkeypatch.setenv("BOSSMOD_COMPANY_ROOT", str(REAL_DATA / "company"))
    found = real_data_violations()
    assert any(item.startswith("agents root") and "the checkout" in item for item in found)
    assert any(item.startswith("company root") and "bossmod-data" in item for item in found)
    assert any(item.startswith("legacy flat projects root") for item in found)
    with pytest.raises(RuntimeError, match="Refusing to run tests against real data"):
        assert_no_real_data()
