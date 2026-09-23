"""Header floor switcher is the control. The left rail stays clear of floor chrome."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HTML = (ROOT / "ui" / "templates" / "index.html").read_text(encoding="utf-8")


def _read(*parts: str) -> str:
    return (JS.joinpath(*parts)).read_text(encoding="utf-8")


def test_header_switcher_is_one_trigger_over_every_floor() -> None:
    header = _read("shell", "header.js")
    switcher = _read("shell", "floor-switcher.js")
    assert "BossModFloorSwitcher.mount" in header
    assert "class: 'floor-switcher'" in switcher
    assert "class: 'floor-switcher-trigger'" in switcher
    # No browse modes: one floor at a time.
    assert "data-floor-scope" not in switcher
    assert "All floors" not in switcher
    assert "Other floor" not in switcher
    # The panel hangs off the positioned host, never <body> — the RCA of the
    # header collapse.
    assert "container: document.body" not in switcher
    assert "container: element" in switcher
    assert "setAttribute('data-menu', 'floor')" in switcher
    assert "New floor" in switcher
    assert "floor-row-more" in switcher
    assert "Edit floor ${floor.name}" in switcher
    # I/O is floor-api.js's; the switcher never fetches directly.
    assert "BossModFloorApi.createFloorApi" in switcher
    assert "apiFetch(" not in switcher
    assert "data-floor-scope" not in header


def test_switcher_panel_host_is_positioned() -> None:
    shell_css = (ROOT / "ui" / "static" / "css" / "shell.css").read_text(encoding="utf-8")
    host = shell_css.split(".floor-switcher {", 1)[1].split("}", 1)[0]
    assert "position: relative;" in host
    overlays = (ROOT / "ui" / "static" / "css" / "overlays.css").read_text(encoding="utf-8")
    assert '.menu[data-menu="floor"]' in overlays


def test_no_browse_state_survives() -> None:
    for path in JS.rglob("*.js"):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        # Split literals, so this test is not itself a hit for the repo-wide
        # grep that proves the browse state is gone.
        for name in ("floor" "Scope", "browse" "FloorId", "office" "FloorId", "office" "People"):
            assert name not in text, f"{path.relative_to(JS)} still names {name}"


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


def test_people_menu_opens_the_vacation_view() -> None:
    menu = _read("shell", "people-view-menu.js")
    assert "On vacation" in menu
    assert "BossModVacationDialog.open" in menu
    dialog = _read("shell", "vacation-dialog.js")
    assert "No one is on vacation." in dialog
    assert "Bring back to" in dialog
    assert "Retry" in dialog


def test_floor_scope_script_loads_before_the_header() -> None:
    scope = HTML.index("js/shell/floor-scope.js")
    api = HTML.index("js/shell/floor-api.js")
    edit = HTML.index("js/shell/floor-edit.js")
    vacation = HTML.index("js/shell/vacation-dialog.js")
    switcher = HTML.index("js/shell/floor-switcher.js")
    header = HTML.index("js/shell/header.js")
    people_menu = HTML.index("js/shell/people-view-menu.js")
    assert scope < api < edit < switcher < header
    assert api < vacation < people_menu
    floor = HTML.index("js/context/agent-floor.js")
    edit = HTML.index("js/context/agent-edit.js")
    assert floor < edit
