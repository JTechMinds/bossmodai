"""Header floor switcher is the control. The left rail stays clear of floor chrome."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HTML = (ROOT / "ui" / "templates" / "index.html").read_text(encoding="utf-8")


def _read(*parts: str) -> str:
    return (JS.joinpath(*parts)).read_text(encoding="utf-8")


def test_header_switcher_offers_this_other_and_all() -> None:
    header = _read("shell", "header.js")
    switcher = _read("shell", "floor-switcher.js")
    assert "BossModFloorSwitcher.mount" in header
    assert "class: 'floor-switcher'" in switcher
    for scope in ("this", "other", "all"):
        assert f"'data-floor-scope': '{scope}'" in switcher
    assert "All floors" in switcher
    assert "'aria-label': 'Floor'" in switcher
    bell = header.split("header-bell", 1)[1][:500]
    assert "data-floor-scope" not in bell
    assert "data-floor-scope" not in header


def test_left_rail_has_no_floor_chrome() -> None:
    for relative in (
        ("shell", "roster.js"),
        ("shell", "roster-people.js"),
        ("shell", "people-view-menu.js"),
    ):
        source = _read(*relative)
        assert "floor-switcher" not in source
        assert "data-floor-scope" not in source
    roster = _read("shell", "roster.js")
    assert "roster-search" in roster
    assert roster.index("roster-search") < roster.index("BossModRosterPeople.createPeople")


def test_places_nav_does_not_grow_a_floor_tab() -> None:
    places = _read("shell", "places.js")
    assert "['chat', 'office', 'tasks', 'files', 'metrics', 'log']" in places
    assert "floor" not in places


def test_office_echoes_the_floor_and_is_not_the_switcher() -> None:
    office = _read("places", "office", "office-place.js")
    assert "office-floor-echo" in office
    assert "data-floor-scope" not in office
    assert "floor-switcher" not in office


def test_hire_uses_the_concrete_floor_and_move_is_not_a_role_patch() -> None:
    save = _read("context", "agent-form-save.js")
    assert "BossModFloorScope.hireFloorId()" in save
    floor = _read("context", "agent-floor.js")
    assert "apiMoveHomeFloor" in floor
    assert "confirm_open_work" in floor
    api = _read("context", "agent-api.js")
    assert "/home-floor" in api
    assert "apiMoveHomeFloor" in api
    menu = _read("shell", "people-view-menu.js")
    assert "home-floor" not in menu


def test_floor_scope_script_loads_before_the_header() -> None:
    scope = HTML.index("js/shell/floor-scope.js")
    switcher = HTML.index("js/shell/floor-switcher.js")
    header = HTML.index("js/shell/header.js")
    assert scope < switcher < header
    floor = HTML.index("js/context/agent-floor.js")
    edit = HTML.index("js/context/agent-edit.js")
    assert floor < edit
