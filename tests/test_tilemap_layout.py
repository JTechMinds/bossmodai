"""The default office layout is well-formed and its workspaces are symmetric.

The South Workspace used to stop one row short: its bottom desks pressed
against the outer wall with no aisle below, unlike the Main Workspace. These
assertions lock the grid's shape and that mirror-image aisle.
"""

from __future__ import annotations

from core.world.tilemap import (
    DEFAULT_DESKS,
    DEFAULT_MAP,
    DEFAULT_ROOMS,
    MAP_HEIGHT,
    MAP_WIDTH,
    TileType,
)

_ROOMS = {room["id"]: room for room in DEFAULT_ROOMS}


def _interior_height(room_id: str) -> int:
    _, y1, _, y2 = _ROOMS[room_id]["bounds"]
    return y2 - y1 + 1


def test_map_grid_matches_its_declared_size() -> None:
    assert len(DEFAULT_MAP) == MAP_HEIGHT
    for y, row in enumerate(DEFAULT_MAP):
        assert len(row) == MAP_WIDTH, f"row {y} has {len(row)} tiles"


def test_workspaces_have_the_same_interior_height() -> None:
    assert _interior_height("workspace_south") == _interior_height("workspace_main")


def test_every_desk_has_an_aisle_on_its_far_side() -> None:
    """Beyond each desk, away from its chair, is an in-room row of floor."""
    for desk in DEFAULT_DESKS:
        desk_x, desk_y = desk["desk_xy"]
        _, chair_y = desk["chair_xy"]
        aisle_y = desk_y + (desk_y - chair_y)
        x1, y1, x2, y2 = _ROOMS[desk["room"]]["bounds"]
        assert y1 <= aisle_y <= y2, f"{desk['id']}: aisle row {aisle_y} is outside its room"
        aisle = DEFAULT_MAP[aisle_y][x1 : x2 + 1]
        assert all(tile == TileType.FLOOR for tile in aisle), (
            f"{desk['id']}: row {aisle_y} is not an aisle"
        )
        assert DEFAULT_MAP[aisle_y][desk_x] == TileType.FLOOR


def test_every_room_has_a_short_label_no_longer_than_its_name() -> None:
    """The office summary labels rooms with `short_name`, so every room needs one.

    The UI refuses a plan with a room missing it; it must also never be longer
    than the name it abbreviates, or it is not the compact label it exists for.
    """
    for room in DEFAULT_ROOMS:
        short_name = room.get("short_name")
        assert isinstance(short_name, str) and short_name.strip(), room["id"]
        assert len(short_name) <= len(room["name"]), room["id"]
