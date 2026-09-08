/**
 * BossMod AI — office canvas sprites.
 *
 * Pure drawing, and nothing else. Every function takes a 2D context, the data
 * to draw, and the options it needs; none reads module state, fetches, or
 * touches the document. That is the seam this file exists to hold: canvas.js
 * mixed "owns a canvas" with "knows how a desk looks", so neither half could
 * be reasoned about alone.
 *
 * Colour never appears here as a literal. `opts.palette` is resolved from
 * tokens.css by office-canvas.js, so the canvas and the CSS around it cannot
 * drift apart.
 */
const BossModCanvasSprites = (() => {

    /**
     * Tile type enum. Must match TileType in core/world/tilemap.py — these are
     * the integers the tilemap payload actually carries.
     */
    const TILE = Object.freeze({
        VOID: 0, FLOOR: 1, WALL: 2, DESK: 3, MEETING: 4,
        BREAK: 5, TRANSIT: 6, DOOR: 7, CHAIR: 8,
    });

    /** Palette key per tile type. VOID is skipped rather than painted. */
    const TILE_PAINT = Object.freeze({
        [TILE.FLOOR]: 'floor', [TILE.WALL]: 'wall', [TILE.DESK]: 'desk',
        [TILE.MEETING]: 'meeting', [TILE.BREAK]: 'breakRoom', [TILE.TRANSIT]: 'transit',
        [TILE.DOOR]: 'door', [TILE.CHAIR]: 'chair',
    });

    /** Thought-bubble geometry and type. Pixels at scale 1. */
    const BUBBLE = Object.freeze({
        MAX_WIDTH: 200, PAD_X: 8, PAD_Y: 6, RADIUS: 8,
        POINTER_W: 8, POINTER_H: 6, GAP: 4,
        FONT: '11px system-ui, sans-serif', LINE_HEIGHT: 14, MAX_LINES: 3,
        FADE_MS: 500,
    });

    /** Agent circle radius as a fraction of one tile. */
    const AGENT_RADIUS_RATIO = 0.35;

    /**
     * Paint the tilemap.
     *
     * @param {CanvasRenderingContext2D} ctx2d
     * @param {{width: number, height: number, tiles: number[][]}} map
     * @param {{tileSize: number, palette: object}} opts
     * @returns {void}
     * @throws {TypeError} When `map.tiles` is not a grid — a malformed payload
     *   must fail here, not paint a blank floor that reads as "empty office".
     */
    function drawTiles(ctx2d, map, opts) {
        const { tileSize, palette } = opts;
        if (!Array.isArray(map.tiles)) throw new TypeError('[sprites] map.tiles is not a grid');
        for (let y = 0; y < map.height; y += 1) {
            for (let x = 0; x < map.width; x += 1) {
                const paint = TILE_PAINT[map.tiles[y][x]];
                if (!paint) continue;  // VOID and anything the map adds later.
                ctx2d.fillStyle = palette[paint];
                ctx2d.fillRect(x * tileSize, y * tileSize, tileSize, tileSize);
                ctx2d.strokeStyle = palette.grid;
                ctx2d.lineWidth = 0.5;
                ctx2d.strokeRect(x * tileSize, y * tileSize, tileSize, tileSize);
            }
        }
    }

    /**
     * Paint each room's name on a pill at its centre.
     *
     * @param {CanvasRenderingContext2D} ctx2d
     * @param {Array<{name: string, bounds: number[]}>} rooms  Empty is valid.
     * @param {{tileSize: number, palette: object}} opts
     * @returns {void}
     */
    function drawRoomLabels(ctx2d, rooms, opts) {
        const { tileSize, palette } = opts;
        ctx2d.font = 'bold 11px system-ui, sans-serif';
        ctx2d.textAlign = 'center';
        ctx2d.textBaseline = 'middle';
        for (const room of rooms || []) {
            const [x1, y1, x2, y2] = room.bounds;
            const centerX = ((x1 + x2) / 2) * tileSize + tileSize / 2;
            const centerY = ((y1 + y2) / 2) * tileSize + tileSize / 2;
            const pillW = ctx2d.measureText(room.name).width + 16;
            ctx2d.fillStyle = palette.pill;
            ctx2d.beginPath();
            ctx2d.roundRect(centerX - pillW / 2, centerY - 9, pillW, 18, 4);
            ctx2d.fill();
            ctx2d.fillStyle = palette.roomInk;
            ctx2d.fillText(room.name, centerX, centerY);
        }
    }

    /**
     * Paint a small monitor on every desk tile.
     *
     * @param {CanvasRenderingContext2D} ctx2d
     * @param {Array<{desk_xy: number[]}>} desks  Empty is valid.
     * @param {{tileSize: number, palette: object}} opts
     * @returns {void}
     */
    function drawDesks(ctx2d, desks, opts) {
        const { tileSize, palette } = opts;
        ctx2d.fillStyle = palette.deskInk;
        for (const desk of desks || []) {
            const cx = desk.desk_xy[0] * tileSize + tileSize / 2;
            const cy = desk.desk_xy[1] * tileSize + tileSize / 2;
            ctx2d.fillRect(cx - 5, cy - 4, 10, 7);
            ctx2d.fillRect(cx - 2, cy + 3, 4, 2);
        }
    }

    /**
     * Paint every agent: shadow, body, hover ring, status dot, name.
     *
     * @param {CanvasRenderingContext2D} ctx2d
     * @param {Array<object>} agents  Tile coordinates, possibly fractional
     *   mid-animation.
     * @param {{tileSize: number, palette: object, hoveredId: string|null,
     *   statusColor: (status: string, kind: string|null) => string}} opts
     *   `statusColor` is injected rather than imported so this module keeps no
     *   dependency of its own — it is BossModAgentStatus.getStatusColor in the app.
     * @returns {void}
     */
    function drawAgents(ctx2d, agents, opts) {
        const { tileSize, palette, hoveredId, statusColor } = opts;
        const radius = tileSize * AGENT_RADIUS_RATIO;
        for (const agent of agents || []) {
            const cx = agent.x * tileSize + tileSize / 2;
            const cy = agent.y * tileSize + tileSize / 2;
            const body = agent.color || palette.agentDefault;

            ctx2d.beginPath();
            ctx2d.arc(cx + 1, cy + 1, radius, 0, Math.PI * 2);
            ctx2d.fillStyle = palette.agentShadow;
            ctx2d.fill();

            // body
            ctx2d.beginPath();
            ctx2d.arc(cx, cy, radius, 0, Math.PI * 2);
            ctx2d.fillStyle = body;
            ctx2d.fill();
            // The ring is what separates an arbitrary operator-chosen colour
            // from the desk and chair it is drawn on top of. Measured without
            // it, EVERY colour in every candidate palette scores 1.1-1.5:1
            // against the chair, so this is structural rather than a palette
            // problem. `pill` is --panel: 5.02:1 on the chair and 3.19:1 on the
            // desk. On the pale floor tiles the ring itself is near-invisible
            // (1.10-1.24:1) and it is the body that carries the separation —
            // 5.08:1 or better for every seed colour. Same stroke the status
            // dot has always used.
            ctx2d.strokeStyle = palette.pill;
            ctx2d.lineWidth = 2;
            ctx2d.stroke();

            if (hoveredId && hoveredId === agent.id) {
                ctx2d.strokeStyle = body;
                ctx2d.lineWidth = 2;
                ctx2d.beginPath();
                ctx2d.arc(cx, cy, radius + 3, 0, Math.PI * 2);
                ctx2d.stroke();
            }

            // status dot
            ctx2d.beginPath();
            ctx2d.arc(cx + radius * 0.6, cy + radius * 0.6, 3, 0, Math.PI * 2);
            ctx2d.fillStyle = statusColor(agent.status, agent.currentActivityKind);
            ctx2d.fill();
            ctx2d.strokeStyle = palette.pill;
            ctx2d.lineWidth = 1;
            ctx2d.stroke();

            ctx2d.font = '10px system-ui, sans-serif';
            ctx2d.textAlign = 'center';
            ctx2d.textBaseline = 'alphabetic';
            ctx2d.fillStyle = palette.nameInk;
            ctx2d.fillText(agent.name, cx, cy + radius + 12);
        }
    }

    /**
     * Word-wrap to fit `maxWidth`, truncating with an ellipsis past MAX_LINES.
     *
     * @param {CanvasRenderingContext2D} ctx2d  Measurement needs the context's
     *   current font, so the caller's font is set here before measuring.
     * @param {string} text
     * @param {number} maxWidth
     * @returns {string[]} At most BUBBLE.MAX_LINES lines.
     */
    function wrapText(ctx2d, text, maxWidth) {
        ctx2d.font = BUBBLE.FONT;
        const lines = [];
        let line = '';
        for (const word of String(text).split(' ')) {
            const test = line ? `${line} ${word}` : word;
            if (ctx2d.measureText(test).width > maxWidth && line) {
                lines.push(line);
                line = word;
            } else {
                line = test;
            }
        }
        if (line) lines.push(line);
        if (lines.length > BUBBLE.MAX_LINES) {
            lines.length = BUBBLE.MAX_LINES;
            lines[BUBBLE.MAX_LINES - 1] = lines[BUBBLE.MAX_LINES - 1].replace(/\s*\S*$/, '…');
        }
        return lines;
    }

    /**
     * Paint one speech bubble above a point.
     *
     * @param {CanvasRenderingContext2D} ctx2d
     * @param {{x: number, y: number, text: string, opacity: number}} bubble
     *   Tile coordinates. Expiry and fade are the caller's decision — this
     *   draws exactly what it is given.
     * @param {{tileSize: number, palette: object}} opts
     * @returns {void}
     */
    function drawThoughtBubble(ctx2d, bubble, opts) {
        const { tileSize, palette } = opts;
        const cx = bubble.x * tileSize + tileSize / 2;
        const cy = bubble.y * tileSize + tileSize / 2;
        const lines = wrapText(ctx2d, bubble.text, BUBBLE.MAX_WIDTH - BUBBLE.PAD_X * 2);

        ctx2d.font = BUBBLE.FONT;
        const textW = lines.reduce((widest, line) => Math.max(widest, ctx2d.measureText(line).width), 0);
        const boxW = textW + BUBBLE.PAD_X * 2;
        const boxH = lines.length * BUBBLE.LINE_HEIGHT + BUBBLE.PAD_Y * 2;
        const boxX = cx - boxW / 2;
        const boxY = cy - tileSize * AGENT_RADIUS_RATIO - BUBBLE.GAP - BUBBLE.POINTER_H - boxH;
        const footY = boxY + boxH;

        ctx2d.save();
        ctx2d.globalAlpha = bubble.opacity;
        ctx2d.shadowColor = palette.agentShadow;
        ctx2d.shadowBlur = 4;
        ctx2d.shadowOffsetY = 2;
        ctx2d.fillStyle = palette.bubbleBg;
        ctx2d.beginPath();
        ctx2d.roundRect(boxX, boxY, boxW, boxH, BUBBLE.RADIUS);
        ctx2d.fill();
        ctx2d.shadowColor = 'transparent';
        ctx2d.strokeStyle = palette.bubbleLine;
        ctx2d.lineWidth = 1;
        ctx2d.stroke();

        ctx2d.fillStyle = palette.bubbleBg;
        ctx2d.beginPath();
        ctx2d.moveTo(cx - BUBBLE.POINTER_W / 2, footY);
        ctx2d.lineTo(cx, footY + BUBBLE.POINTER_H);
        ctx2d.lineTo(cx + BUBBLE.POINTER_W / 2, footY);
        ctx2d.closePath();
        ctx2d.fill();

        ctx2d.strokeStyle = palette.bubbleLine;
        ctx2d.beginPath();
        ctx2d.moveTo(cx - BUBBLE.POINTER_W / 2, footY - 0.5);
        ctx2d.lineTo(cx, footY + BUBBLE.POINTER_H);
        ctx2d.lineTo(cx + BUBBLE.POINTER_W / 2, footY - 0.5);
        ctx2d.stroke();

        ctx2d.fillStyle = palette.bubbleInk;
        ctx2d.textAlign = 'left';
        ctx2d.textBaseline = 'top';
        lines.forEach((line, index) => {
            ctx2d.fillText(line, boxX + BUBBLE.PAD_X,
                boxY + BUBBLE.PAD_Y + index * BUBBLE.LINE_HEIGHT);
        });
        ctx2d.restore();
    }

    /**
     * Paint a list of bubbles.
     *
     * @param {CanvasRenderingContext2D} ctx2d
     * @param {Array<object>} bubbles  See drawThoughtBubble.
     * @param {{tileSize: number, palette: object}} opts
     * @returns {void}
     */
    function drawThoughtBubbles(ctx2d, bubbles, opts) {
        for (const bubble of bubbles || []) drawThoughtBubble(ctx2d, bubble, opts);
    }

    return {
        TILE, BUBBLE, AGENT_RADIUS_RATIO,
        drawTiles, drawRoomLabels, drawDesks, drawAgents, drawThoughtBubbles, wrapText,
    };
})();
