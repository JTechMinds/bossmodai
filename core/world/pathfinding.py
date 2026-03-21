"""BossMod AI — A* pathfinding on the office tilemap.

Uses the ``pathfinding`` library to find walkable routes between
tiles on the office grid. Agents use this to navigate between
desks, meeting rooms, break rooms, etc.
"""

from __future__ import annotations

import logging

from pathfinding.core.diagonal_movement import DiagonalMovement
from pathfinding.core.grid import Grid
from pathfinding.finder.a_star import AStarFinder

from core.world.tilemap import DEFAULT_MAP, MAP_HEIGHT, MAP_WIDTH, TileType

logger = logging.getLogger(__name__)

# Tiles agents can walk on
_WALKABLE_TILES = {
    TileType.FLOOR,
    TileType.MEETING,
    TileType.BREAK,
    TileType.TRANSIT,
    TileType.DOOR,
    TileType.CHAIR,
}

# Pre-build the walkability matrix (1 = walkable, 0 = blocked)
_WALK_MATRIX: list[list[int]] = [
    [1 if DEFAULT_MAP[y][x] in _WALKABLE_TILES else 0 for x in range(MAP_WIDTH)]
    for y in range(MAP_HEIGHT)
]

_finder = AStarFinder(diagonal_movement=DiagonalMovement.never)


def find_path(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
) -> list[tuple[int, int]]:
    """Find a walkable path from (start_x, start_y) to (end_x, end_y).

    Returns a list of ``(x, y)`` tuples representing the path,
    including the start and end positions. Returns an empty list
    if no path exists.

    The grid is rebuilt each call to ensure clean state (required
    by the ``pathfinding`` library after each search).
    """
    if not _in_bounds(start_x, start_y) or not _in_bounds(end_x, end_y):
        logger.warning("Path request out of bounds: (%d,%d) → (%d,%d)", start_x, start_y, end_x, end_y)
        return []

    # Grid must be recreated per search (library mutates internal state)
    grid = Grid(matrix=_WALK_MATRIX)
    start = grid.node(start_x, start_y)
    end = grid.node(end_x, end_y)

    path, _runs = _finder.find_path(start, end, grid)

    if not path:
        logger.debug("No path found: (%d,%d) → (%d,%d)", start_x, start_y, end_x, end_y)
        return []

    return [(node.x, node.y) for node in path]


def path_length(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
) -> int:
    """Return the number of steps in the shortest path, or -1 if none."""
    p = find_path(start_x, start_y, end_x, end_y)
    return len(p) - 1 if p else -1


def _in_bounds(x: int, y: int) -> bool:
    return 0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT
