"""BossMod AI — Office tilemap definition.

Defines tile types, room metadata, and the default 30x20 office layout.
The Canvas renderer reads this data (served via API) to draw the office.
The world simulation uses it for pathfinding and location rules.
"""

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


# ─── Room definitions (id, display_name, type, bounds) ───
# Bounds are (x1, y1, x2, y2) inclusive, top-left origin.

DEFAULT_ROOMS = [
    {
        "id": "workspace_main",
        "name": "Main Workspace",
        "room_type": RoomType.WORKSPACE,
        "bounds": (1, 1, 12, 8),
    },
    {
        "id": "meeting_room",
        "name": "Meeting Room",
        "room_type": RoomType.MEETING,
        "bounds": (16, 1, 23, 8),
    },
    {
        "id": "break_room",
        "name": "Break Room",
        "room_type": RoomType.BREAK,
        "bounds": (16, 12, 23, 18),
    },
    {
        "id": "hallway_main",
        "name": "Hallway",
        "room_type": RoomType.HALLWAY,
        "bounds": (13, 1, 15, 18),
    },
    {
        "id": "workspace_south",
        "name": "South Workspace",
        "room_type": RoomType.WORKSPACE,
        "bounds": (1, 12, 12, 18),
    },
]

# ─── Desk positions (tile coordinates where agents sit) ───
# Each desk has a chair tile next to it where the agent stands.

DEFAULT_DESKS = [
    {"id": "desk_1", "desk_xy": (3, 3),  "chair_xy": (3, 4),  "room": "workspace_main"},
    {"id": "desk_2", "desk_xy": (7, 3),  "chair_xy": (7, 4),  "room": "workspace_main"},
    {"id": "desk_3", "desk_xy": (11, 3), "chair_xy": (11, 4), "room": "workspace_main"},
    {"id": "desk_4", "desk_xy": (3, 7),  "chair_xy": (3, 6),  "room": "workspace_main"},
    {"id": "desk_5", "desk_xy": (7, 7),  "chair_xy": (7, 6),  "room": "workspace_main"},
    {"id": "desk_6", "desk_xy": (3, 14), "chair_xy": (3, 15), "room": "workspace_south"},
    {"id": "desk_7", "desk_xy": (7, 14), "chair_xy": (7, 15), "room": "workspace_south"},
    {"id": "desk_8", "desk_xy": (11, 14),"chair_xy": (11, 15),"room": "workspace_south"},
]

# Map grid dimensions
MAP_WIDTH = 28
MAP_HEIGHT = 20

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
    [_W,_F,_F,_C,_F,_F,_F,_C,_F,_F,_F,_F,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 6
    [_W,_F,_F,_D,_F,_F,_F,_D,_F,_F,_F,_F,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 7
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_M,_M,_M,_M,_M,_M,_M,_W,_V,_V,_V],  # 8
    [_W,_W,_W,_W,_W,_W,_O,_W,_W,_W,_W,_W,_W,_T,_T,_T,_W,_W,_W,_W,_O,_W,_W,_W,_W,_V,_V,_V],  # 9
    [_V,_V,_V,_V,_V,_V,_T,_V,_V,_V,_V,_V,_V,_T,_T,_T,_V,_V,_V,_V,_T,_V,_V,_V,_V,_V,_V,_V],  # 10
    [_W,_W,_W,_W,_W,_W,_O,_W,_W,_W,_W,_W,_W,_T,_T,_T,_W,_W,_W,_W,_O,_W,_W,_W,_W,_V,_V,_V],  # 11
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 12
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 13
    [_W,_F,_F,_D,_F,_F,_F,_D,_F,_F,_F,_D,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 14
    [_W,_F,_F,_C,_F,_F,_F,_C,_F,_F,_F,_C,_F,_T,_T,_T,_O,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 15
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 16
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 17
    [_W,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_F,_T,_T,_T,_W,_B,_B,_B,_B,_B,_B,_B,_W,_V,_V,_V],  # 18
    [_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_W,_V,_V,_V],  # 19
]
# fmt: on


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
