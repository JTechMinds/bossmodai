/**
 * BossMod AI — the floor plan as the office summary draws it.
 *
 * The pure half of context/mini-office.js: it turns the floor plan's `rooms`
 * into rooms the summary can colour and place, and says where each one sits
 * on a CSS grid. No DOM, no request, no store — mini-office.js owns those,
 * and it owns reporting a plan this refuses.
 *
 * The summary places rooms by their tile `bounds`, colours them by their
 * `room_type` and labels them with their `short_name`, the facts
 * core/world/tilemap.py's DEFAULT_ROOMS carries besides a name. The short
 * label is data rather than a text rule here, so a renamed room cannot
 * silently keep a stale abbreviation. That shape is a UI contract now.
 * Nothing else in the map payload is read: tiles, desks and the map's own
 * dimensions are the canvas renderer's, and this is not one.
 */
const BossModFloorPlan = (() => {
    /**
     * The `room_type`s the summary can colour — core/world/tilemap.py's
     * RoomType. Each is a `data-tone` context.css paints with the token the
     * canvas uses for that room's tiles; anything else is a plan it cannot draw.
     */
    const ROOM_TYPES = Object.freeze(['workspace', 'meeting', 'break', 'hallway']);

    function drawable(room) {
        const bounds = room && room.bounds;
        return String((room && room.name) || '').trim() !== ''
            && typeof room.short_name === 'string' && room.short_name.trim() !== ''
            && ROOM_TYPES.includes(room.room_type)
            && Array.isArray(bounds) && bounds.length === 4 && bounds.every(Number.isInteger)
            && bounds[0] <= bounds[2] && bounds[1] <= bounds[3];
    }

    /**
     * Read the map's rooms into a plan the summary can draw.
     *
     * Every room needs a name, a non-empty `short_name` (the summary's
     * label; the full name stays its accessible name), a `room_type` in
     * ROOM_TYPES and integer `bounds` `[x1, y1, x2, y2]` with x1 <= x2 and
     * y1 <= y2. One room short of that and any colour, label or position
     * given to it would be a guess, so the whole plan is refused.
     *
     * The tracks are the rooms' own bounding box, not the map's width and
     * height, so the void and the outer wall around the floor waste no space.
     *
     * @param {object[]} rooms  `mapData.rooms`, in map order.
     * @returns {{rooms: Array<{name: string, shortName: string, roomType: string,
     *   bounds: number[]}>, minX: number, minY: number, cols: number, rows: number}}
     * @throws {Error} When there are no rooms (no box to lay a floor out on),
     *   or naming the first room it cannot draw.
     */
    function read(rooms) {
        if (!Array.isArray(rooms) || rooms.length === 0) {
            throw new Error('[floor-plan] the floor plan has no rooms');
        }
        const bad = rooms.find((room) => !drawable(room));
        if (bad !== undefined) {
            throw new Error(`[floor-plan] a room it cannot draw: ${JSON.stringify(bad)}`);
        }
        const placed = rooms.map((room) => ({
            name: String(room.name).trim(),
            shortName: room.short_name.trim(),
            roomType: room.room_type,
            bounds: room.bounds,
        }));
        const edge = (index, pick) => pick(...placed.map((room) => room.bounds[index]));
        const minX = edge(0, Math.min);
        const minY = edge(1, Math.min);
        return {
            rooms: placed,
            minX,
            minY,
            cols: edge(2, Math.max) - minX + 1,
            rows: edge(3, Math.max) - minY + 1,
        };
    }

    /**
     * Where a room sits on the plan's grid: its tile bounds, shifted so the
     * bounding box starts at track 1. A room taller than it is wide turns its
     * text — the Hallway today — read from the shape, so no pixel threshold
     * decides it.
     *
     * @param {{minX: number, minY: number}} plan  From read().
     * @param {number[]} bounds  Inclusive tiles `[x1, y1, x2, y2]`.
     * @returns {{column: string, row: string, vertical: boolean}} CSS
     *   `grid-column` / `grid-row` values, and the orientation.
     */
    function place(plan, [x1, y1, x2, y2]) {
        return {
            column: `${x1 - plan.minX + 1} / ${x2 - plan.minX + 2}`,
            row: `${y1 - plan.minY + 1} / ${y2 - plan.minY + 2}`,
            vertical: y2 - y1 > x2 - x1,
        };
    }

    return { read, place };
})();
