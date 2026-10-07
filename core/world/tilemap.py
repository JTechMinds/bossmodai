"""BossMod AI — Office tilemap definition.

Defines tile types, room metadata, and the default 28x21 office layout.
The Canvas renderer reads this data (served via API) to draw the office.
The world simulation uses it for pathfinding and location rules.
"""

from collections.abc import Collection
from enum import IntEnum


class TileType(IntEnum):
    """Tile types for the office grid. Values map to render colors in canvas.js."""
    VOID = 0        # Outside the office (not walkable)
    FLOOR = 1       # General walkable floor
    WALL = 2        # Walls (not walkable)
    DESK = 3        # Agent desk (assigned seating)
    MEETING = 4     # Meeting room floor
    BREAK = 5       # Break room floor
    TRANSIT = 6     # Hallways / corridors
    DOOR = 7        # Doorways (walkable transition between rooms)
    CHAIR = 8       # Chair at a desk


class RoomType(str):
    """Room types that govern what actions are allowed."""
    WORKSPACE = "workspace"
    MEETING = "meeting"
    BREAK = "break"
    HALLWAY = "hallway"


# ─── Room definitions (id, display_name, short_name, type, bounds) ───
# short_name is the compact label for narrow UI (the office summary); agents never see it.
# Bounds are (x1, y1, x2, y2) inclusive, top-left origin.

DEFAULT_ROOMS = [
    {
        "id": "workspace_main",
        "name": "Main Workspace",
        "short_name": "Main WS",
        "room_type": RoomType.WORKSPACE,
        "bounds": (1, 1, 12, 8),
    },
    {
        "id": "meeting_room",
        "name": "Meeting Room",
        "short_name": "Meeting",
        "room_type": RoomType.MEETING,
        "bounds": (16, 1, 23, 8),
    },
    {
        "id": "break_room",
        "name": "Break Room",
        "short_name": "Break",
        "room_type": RoomType.BREAK,
        "bounds": (16, 12, 23, 19),
    },
    {
        "id": "hallway_main",
        "name": "Hallway",
        "short_name": "Hallway",
        "room_type": RoomType.HALLWAY,
        "bounds": (13, 1, 15, 19),
    },
    {
        "id": "workspace_south",
        "name": "South Workspace",
        "short_name": "South WS",
        "room_type": RoomType.WORKSPACE,
        "bounds": (1, 12, 12, 19),
    },
]

# ─── Desk positions (tile coordinates where agents sit) ───
# Each desk has a chair tile next to it where the agent stands. Agents store
# the chair (``Agent.desk_x`` / ``desk_y``), never the desk id, so ids may be
# renumbered as long as every chair keeps its coordinates. Each workspace is a
# 2x3 grid: desk row, chair row, aisle, chair row, desk row. Every floor draws
# its own copy of this map, so occupancy is per floor (core/world/seating.py).

DEFAULT_DESKS = [
    {"id": "desk_1",  "label": "Desk 1 — Main NW",   "desk_xy": (3, 3),   "chair_xy": (3, 4),   "room": "workspace_main"},
    {"id": "desk_2",  "label": "Desk 2 — Main N",    "desk_xy": (7, 3),   "chair_xy": (7, 4),   "room": "workspace_main"},
    {"id": "desk_3",  "label": "Desk 3 — Main NE",   "desk_xy": (11, 3),  "chair_xy": (11, 4),  "room": "workspace_main"},
    {"id": "desk_4",  "label": "Desk 4 — Main SW",   "desk_xy": (3, 7),   "chair_xy": (3, 6),   "room": "workspace_main"},
    {"id": "desk_5",  "label": "Desk 5 — Main S",    "desk_xy": (7, 7),   "chair_xy": (7, 6),   "room": "workspace_main"},
    {"id": "desk_6",  "label": "Desk 6 — Main SE",   "desk_xy": (11, 7),  "chair_xy": (11, 6),  "room": "workspace_main"},
    {"id": "desk_7",  "label": "Desk 7 — South NW",  "desk_xy": (3, 14),  "chair_xy": (3, 15),  "room": "workspace_south"},
    {"id": "desk_8",  "label": "Desk 8 — South N",   "desk_xy": (7, 14),  "chair_xy": (7, 15),  "room": "workspace_south"},
    {"id": "desk_9",  "label": "Desk 9 — South NE",  "desk_xy": (11, 14), "chair_xy": (11, 15), "room": "workspace_south"},
    {"id": "desk_10", "label": "Desk 10 — South SW", "desk_xy": (3, 18),  "chair_xy": (3, 17),  "room": "workspace_south"},
    {"id": "desk_11", "label": "Desk 11 — South S",  "desk_xy": (7, 18),  "chair_xy": (7, 17),  "room": "workspace_south"},
    {"id": "desk_12", "label": "Desk 12 — South SE", "desk_xy": (11, 18), "chair_xy": (11, 17), "room": "workspace_south"},
]

# Map grid dimensions
MAP_WIDTH = 28
MAP_HEIGHT = 21

# V = VOID, F = FLOOR, W = WALL, D = DESK, M = MEETING, B = BREAK,
# T = TRANSIT, O = DOOR, C = CHAIR
_V, _F, _W, _D, _M, _B, _T, _O, _C = range(9)

# fmt: off
DEFAULT_MAP = [
    #  0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27
    [_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_V,_V,_V],  # 0
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 1
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 2
    [_W,_F,_F,_D,_F,_F,_F,_D,_F,_F,_F,_D,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 3
    [_W,_F,_F,_C,_F,_F,_F,_C,_F,_F,_F,_C,_F,_T,_T,_T,_O,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 4
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 5
    [_W,_F,_F,_C,_F,_F,_F,_C,_F,_F,_F,_C,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 6
    [_W,_F,_F,_D,_F,_F,_F,_D,_F,_F,_F,_D,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 7
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 8
    [_W,_W,_W,_W,_W,_W,_O,_W,_W,_W,_W,_W,_W,_T,_T,_T,_W,_W,_W,_W,_O,_W,_W,_W,_W,_V,_V,_V],  # 9
    [_V,_V,_V,_V,_V,_V,_T,_V,_V,_V,_V,_V,_V,_T,_T,_T,_V,_V,_V,_V,_T,_V,_V,_V,_V,_V,_V,_V],  # 10
    [_W,_W,_W,_W,_W,_W,_O,_W,_W,_W,_W,_W,_W,_T,_T,_T,_W,_W,_W,_W,_O,_W,_W,_W,_W,_V,_V,_V],  # 11
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 12
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 13
    [_W,_F,_F,_D,_F,_F,_F,_D,_F,_F,_F,_D,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 14
    [_W,_F,_F,_C,_F,_F,_F,_C,_F,_F,_F,_C,_F,_T,_T,_T,_O,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 15
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 16
    [_W,_F,_F,_C,_F,_F,_F,_C,_F,_F,_F,_C,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 17
    [_W,_F,_F,_D,_F,_F,_F,_D,_F,_F,_F,_D,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 18
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 19
    [_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_V,_V,_V],  # 20
]
# fmt: on


_CHAIRS: tuple[tuple[int, int], ...] = tuple(desk["chair_xy"] for desk in DEFAULT_DESKS)


def is_chair(xy: tuple[int, int]) -> bool:
    """Return True when ``xy`` is the chair tile of one of ``DEFAULT_DESKS``."""
    return tuple(xy) in _CHAIRS


def first_free_chair(occupied: Collection[tuple[int, int]]) -> tuple[int, int] | None:
    """Return the first chair, in ``DEFAULT_DESKS`` order, not in ``occupied``.

    Pure: the caller decides whose chairs count (core/world/seating.py scopes
    them to one floor).

    Args:
        occupied: Chair tiles already claimed.

    Returns:
        The chair tile, or None when every chair is claimed.
    """
    for chair in _CHAIRS:
        if chair not in occupied:
            return chair
    return None


def free_tile_in_room(
    room_id: str,
    occupied: Collection[tuple[int, int]],
) -> tuple[int, int] | None:
    """Return the free walkable tile closest to a room's centre.

    Chair tiles are never offered: walking to a room must not take someone's
    desk. Candidates are ordered by Manhattan distance from the room's integer
    centre, ties broken by ``(y, x)``, so the choice is deterministic.

    Args:
        room_id: A ``DEFAULT_ROOMS`` id.
        occupied: Tiles already taken (bodies standing there, or the
            destinations of walks in progress).

    Returns:
        The tile, or None when every candidate in the room is occupied.

    Raises:
        KeyError: ``room_id`` is not a room on the map.
    """
    room = next((r for r in DEFAULT_ROOMS if r["id"] == room_id), None)
    if room is None:
        raise KeyError(room_id)
    x1, y1, x2, y2 = room["bounds"]
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    candidates = [
        (x, y)
        for y in range(y1, y2 + 1)
        for x in range(x1, x2 + 1)
        if is_walkable(x, y) and (x, y) not in _CHAIRS
    ]
    candidates.sort(key=lambda xy: (abs(xy[0] - cx) + abs(xy[1] - cy), xy[1], xy[0]))
    for tile in candidates:
        if tile not in occupied:
            return tile
    return None


def get_map_data() -> dict:
    """Return the full map data as a JSON-serializable dict for the frontend."""
    return {
        "width": MAP_WIDTH,
        "height": MAP_HEIGHT,
        "tiles": DEFAULT_MAP,
        "rooms": DEFAULT_ROOMS,
        "desks": DEFAULT_DESKS,
    }


def is_walkable(x: int, y: int) -> bool:
    """Check if a tile is walkable."""
    if x < 0 or x >= MAP_WIDTH or y < 0 or y >= MAP_HEIGHT:
        return False
    tile = DEFAULT_MAP[y][x]
    return tile not in (TileType.VOID, TileType.WALL, TileType.DESK)


def get_room_at(x: int, y: int) -> dict | None:
    """Return the room definition containing tile (x, y), or None."""
    for room in DEFAULT_ROOMS:
        bx1, by1, bx2, by2 = room["bounds"]
        if bx1 <= x <= bx2 and by1 <= y <= by2:
            return room
    return None
