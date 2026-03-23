/**
 * BossMod AI — Canvas office map renderer.
 *
 * Fetches the tilemap from /api/map and renders the 2D office.
 * Draws colored tiles per type, room labels, and agent circles.
 * Click detection on agents triggers the agent panel overlay.
 *
 * Architecture: tile types map to color constants. When pixel art
 * arrives, swap the renderTile function to draw from a spritesheet.
 */

const OfficeCanvas = (() => {
    // ─── Constants ───

    const TILE_SIZE = 28; // px per tile (adjustable)

    // Tile type enum (must match TileType in tilemap.py)
    const TILE = {
        VOID:    0,
        FLOOR:   1,
        WALL:    2,
        DESK:    3,
        MEETING: 4,
        BREAK:   5,
        TRANSIT: 6,
        DOOR:    7,
        CHAIR:   8,
    };

    // Tile colors (light theme palette from design)
    const TILE_COLORS = {
        [TILE.VOID]:    '#f8fafc',  // bg color (invisible)
        [TILE.FLOOR]:   '#e5e7eb',  // gray-200
        [TILE.WALL]:    '#9ca3af',  // gray-400
        [TILE.DESK]:    '#d97706',  // amber-600
        [TILE.MEETING]: '#dbeafe',  // blue-100
        [TILE.BREAK]:   '#dcfce7',  // green-100
        [TILE.TRANSIT]: '#f3f4f6',  // gray-100
        [TILE.DOOR]:    '#fef3c7',  // amber-100
        [TILE.CHAIR]:   '#b45309',  // amber-700
    };

    // ─── Thought bubble constants ───

    const BUBBLE_MAX_WIDTH = 200;
    const BUBBLE_PAD_X = 8;
    const BUBBLE_PAD_Y = 6;
    const BUBBLE_RADIUS = 8;
    const BUBBLE_POINTER_W = 8;
    const BUBBLE_POINTER_H = 6;
    const BUBBLE_GAP = 4;        // gap between pointer and agent circle
    const BUBBLE_FONT = '11px system-ui, sans-serif';
    const BUBBLE_LINE_HEIGHT = 14;
    const BUBBLE_MAX_LINES = 3;
    const BUBBLE_FADE_MS = 500;  // fade-out in last 500ms

    // ─── State ───

    let canvas = null;
    let ctx = null;
    let mapData = null;
    let agents = [];     // {id, name, x, y, color, status}
    const agentAnimations = new Map();
    const agentThoughts = new Map();  // agent_id -> { text, timestamp }
    let thoughtDurationMs = 4000;
    let hoveredAgent = null;
    let scale = 1;
    let animationFrameId = null;

    // ─── Initialization ───

    async function init() {
        canvas = document.getElementById('office-canvas');
        if (!canvas) return;
        ctx = canvas.getContext('2d');

        // Fetch map data from API
        try {
            const res = await fetch('/api/map');
            mapData = await res.json();
        } catch (err) {
            console.error('[OfficeCanvas] Failed to load map:', err);
            return;
        }

        sizeCanvas();

        // Load agents from API
        try {
            const worldRes = await fetch('/api/world');
            const world = await worldRes.json();
            agents = world.map(BossModUtils.normalizeAgent);
        } catch (err) {
            console.error('[OfficeCanvas] Failed to load agents:', err);
        }

        // Load thought bubble duration from settings
        loadThoughtDuration();

        render();

        // Event listeners
        canvas.addEventListener('click', handleClick);
        canvas.addEventListener('mousemove', handleMouseMove);
        canvas.addEventListener('mouseleave', () => {
            hoveredAgent = null;
            canvas.style.cursor = 'default';
            render();
        });

        // Resize when panels change or window resizes
        window.addEventListener('panel-resize', () => {
            sizeCanvas();
            render();
        });
        window.addEventListener('resize', () => {
            sizeCanvas();
            render();
        });

        console.log('[OfficeCanvas] Initialized', mapData.width, 'x', mapData.height);
    }

    // ─── Canvas sizing ───

    function sizeCanvas() {
        if (!mapData || !canvas) return;

        const container = document.getElementById('canvas-container');
        const containerW = container.clientWidth;
        const containerH = container.clientHeight;

        const mapPixelW = mapData.width * TILE_SIZE;
        const mapPixelH = mapData.height * TILE_SIZE;

        // Scale to fit container while maintaining aspect ratio
        scale = Math.min(
            containerW / mapPixelW,
            containerH / mapPixelH,
            1.5  // max zoom
        );

        canvas.width = Math.floor(mapPixelW * scale);
        canvas.height = Math.floor(mapPixelH * scale);

        // Crisp pixel rendering
        ctx.imageSmoothingEnabled = false;
    }

    // ─── Rendering ───

    function render() {
        if (!mapData || !ctx) return;

        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.save();
        ctx.scale(scale, scale);

        renderTiles();
        renderRoomLabels();
        renderDesks();
        renderAgents();
        renderThoughtBubbles();

        ctx.restore();
    }

    function renderTiles() {
        const { width, height, tiles } = mapData;

        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {
                const tileType = tiles[y][x];
                if (tileType === TILE.VOID) continue;

                ctx.fillStyle = TILE_COLORS[tileType] || TILE_COLORS[TILE.FLOOR];
                ctx.fillRect(x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE);

                // Subtle grid lines
                ctx.strokeStyle = 'rgba(0, 0, 0, 0.04)';
                ctx.lineWidth = 0.5;
                ctx.strokeRect(x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE);
            }
        }
    }

    function renderRoomLabels() {
        if (!mapData.rooms) return;

        ctx.font = 'bold 11px system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        for (const room of mapData.rooms) {
            const [x1, y1, x2, y2] = room.bounds;
            const centerX = ((x1 + x2) / 2) * TILE_SIZE + TILE_SIZE / 2;
            const centerY = ((y1 + y2) / 2) * TILE_SIZE + TILE_SIZE / 2;

            // Background pill
            const text = room.name;
            const metrics = ctx.measureText(text);
            const padX = 8;
            const padY = 4;

            ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
            ctx.beginPath();
            const pillW = metrics.width + padX * 2;
            const pillH = 18;
            ctx.roundRect(
                centerX - pillW / 2,
                centerY - pillH / 2,
                pillW,
                pillH,
                4
            );
            ctx.fill();

            // Label text
            ctx.fillStyle = '#475569'; // slate-600
            ctx.fillText(text, centerX, centerY);
        }
    }

    function renderDesks() {
        if (!mapData.desks) return;

        // Draw desk indicators (small monitor shape on desk tiles)
        for (const desk of mapData.desks) {
            const [dx, dy] = desk.desk_xy;
            const cx = dx * TILE_SIZE + TILE_SIZE / 2;
            const cy = dy * TILE_SIZE + TILE_SIZE / 2;

            // Monitor shape
            ctx.fillStyle = '#78716c'; // stone-500
            ctx.fillRect(cx - 5, cy - 4, 10, 7);
            ctx.fillRect(cx - 2, cy + 3, 4, 2);
        }
    }

    function renderAgents() {
        for (const agent of agents) {
            const cx = agent.x * TILE_SIZE + TILE_SIZE / 2;
            const cy = agent.y * TILE_SIZE + TILE_SIZE / 2;
            const radius = TILE_SIZE * 0.35;
            const isHovered = hoveredAgent && hoveredAgent.id === agent.id;

            // Shadow
            ctx.beginPath();
            ctx.arc(cx + 1, cy + 1, radius, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(0, 0, 0, 0.15)';
            ctx.fill();

            // Agent circle
            ctx.beginPath();
            ctx.arc(cx, cy, radius, 0, Math.PI * 2);
            ctx.fillStyle = agent.color || '#3b82f6';
            ctx.fill();

            // Hover ring
            if (isHovered) {
                ctx.strokeStyle = agent.color || '#3b82f6';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(cx, cy, radius + 3, 0, Math.PI * 2);
                ctx.stroke();
            }

            // Status indicator dot (bottom-right)
            const statusColor = getStatusColor(agent.status, agent.currentActivityKind);
            ctx.beginPath();
            ctx.arc(cx + radius * 0.6, cy + radius * 0.6, 3, 0, Math.PI * 2);
            ctx.fillStyle = statusColor;
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1;
            ctx.stroke();

            // Name label below
            ctx.font = '10px system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillStyle = '#1e293b';
            ctx.fillText(
                agent.name,
                cx,
                cy + radius + 12
            );
        }
    }

    function getStatusColor(status, currentActivityKind) {
        return BossModUtils.getStatusColor(status, currentActivityKind);
    }

    // ─── Thought bubbles ───

    async function loadThoughtDuration() {
        try {
            const res = await fetch('/api/settings?category=simulation');
            if (!res.ok) return;
            const settings = await res.json();
            const setting = settings.find(s => s.key === 'thought_bubble_duration_ms');
            if (setting) thoughtDurationMs = parseInt(setting.value, 10) || 4000;
        } catch (err) {
            console.error('[OfficeCanvas] Failed to load thought duration:', err);
        }
    }

    function showThought(agentId, text) {
        if (!text) return;
        agentThoughts.set(agentId, { text, timestamp: Date.now() });
        ensureThoughtTimer();
        render();
    }

    function ensureThoughtTimer() {
        if (agentThoughts.size === 0) return;
        // If the animation loop is already running (path animations), bubbles
        // get redrawn each frame automatically. Otherwise, schedule a render
        // for when the oldest active thought expires.
        if (animationFrameId) return;
        // Use a simple timeout to re-render once thoughts might expire
        const now = Date.now();
        let soonest = Infinity;
        for (const thought of agentThoughts.values()) {
            const remaining = (thought.timestamp + thoughtDurationMs) - now;
            if (remaining < soonest) soonest = remaining;
        }
        if (soonest < Infinity && soonest > 0) {
            setTimeout(() => { render(); ensureThoughtTimer(); }, Math.min(soonest, 100));
        }
    }

    /** Word-wrap text to fit within maxWidth, return array of lines. */
    function wrapText(text, maxWidth) {
        ctx.font = BUBBLE_FONT;
        const words = text.split(' ');
        const lines = [];
        let line = '';

        for (const word of words) {
            const test = line ? `${line} ${word}` : word;
            if (ctx.measureText(test).width > maxWidth && line) {
                lines.push(line);
                line = word;
            } else {
                line = test;
            }
        }
        if (line) lines.push(line);

        // Truncate to max lines
        if (lines.length > BUBBLE_MAX_LINES) {
            lines.length = BUBBLE_MAX_LINES;
            lines[BUBBLE_MAX_LINES - 1] = lines[BUBBLE_MAX_LINES - 1].replace(/\s*\S*$/, '\u2026');
        }

        return lines;
    }

    function renderThoughtBubbles() {
        const now = Date.now();
        let hasActive = false;

        for (const [agentId, thought] of agentThoughts.entries()) {
            const elapsed = now - thought.timestamp;
            if (elapsed >= thoughtDurationMs) {
                agentThoughts.delete(agentId);
                continue;
            }

            const agent = agents.find(a => a.id === agentId);
            if (!agent) continue;

            hasActive = true;

            // Compute opacity (fade out in last BUBBLE_FADE_MS)
            const remaining = thoughtDurationMs - elapsed;
            const opacity = remaining < BUBBLE_FADE_MS ? remaining / BUBBLE_FADE_MS : 1;

            const cx = agent.x * TILE_SIZE + TILE_SIZE / 2;
            const cy = agent.y * TILE_SIZE + TILE_SIZE / 2;
            const agentRadius = TILE_SIZE * 0.35;

            // Word-wrap the text
            const textMaxW = BUBBLE_MAX_WIDTH - BUBBLE_PAD_X * 2;
            const lines = wrapText(thought.text, textMaxW);

            // Calculate bubble dimensions
            ctx.font = BUBBLE_FONT;
            let textW = 0;
            for (const line of lines) {
                const w = ctx.measureText(line).width;
                if (w > textW) textW = w;
            }
            const bubbleW = textW + BUBBLE_PAD_X * 2;
            const bubbleH = lines.length * BUBBLE_LINE_HEIGHT + BUBBLE_PAD_Y * 2;

            // Position: centered above agent
            const bubbleX = cx - bubbleW / 2;
            const bubbleY = cy - agentRadius - BUBBLE_GAP - BUBBLE_POINTER_H - bubbleH;

            ctx.save();
            ctx.globalAlpha = opacity;

            // Drop shadow
            ctx.shadowColor = 'rgba(0, 0, 0, 0.1)';
            ctx.shadowBlur = 4;
            ctx.shadowOffsetX = 0;
            ctx.shadowOffsetY = 2;

            // Bubble body
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.roundRect(bubbleX, bubbleY, bubbleW, bubbleH, BUBBLE_RADIUS);
            ctx.fill();

            // Reset shadow for border
            ctx.shadowColor = 'transparent';

            // Border
            ctx.strokeStyle = '#e2e8f0';
            ctx.lineWidth = 1;
            ctx.stroke();

            // Pointer triangle
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.moveTo(cx - BUBBLE_POINTER_W / 2, bubbleY + bubbleH);
            ctx.lineTo(cx, bubbleY + bubbleH + BUBBLE_POINTER_H);
            ctx.lineTo(cx + BUBBLE_POINTER_W / 2, bubbleY + bubbleH);
            ctx.closePath();
            ctx.fill();

            // Pointer border (left and right edges only)
            ctx.strokeStyle = '#e2e8f0';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(cx - BUBBLE_POINTER_W / 2, bubbleY + bubbleH - 0.5);
            ctx.lineTo(cx, bubbleY + bubbleH + BUBBLE_POINTER_H);
            ctx.lineTo(cx + BUBBLE_POINTER_W / 2, bubbleY + bubbleH - 0.5);
            ctx.stroke();

            // Text
            ctx.font = BUBBLE_FONT;
            ctx.fillStyle = '#1e293b';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';
            for (let i = 0; i < lines.length; i++) {
                ctx.fillText(
                    lines[i],
                    bubbleX + BUBBLE_PAD_X,
                    bubbleY + BUBBLE_PAD_Y + i * BUBBLE_LINE_HEIGHT,
                );
            }

            ctx.restore();
        }

        if (hasActive) ensureThoughtTimer();
    }

    // ─── Hit detection ───

    function getAgentAt(canvasX, canvasY) {
        // Convert canvas coordinates to tile coordinates
        const tileX = canvasX / (TILE_SIZE * scale);
        const tileY = canvasY / (TILE_SIZE * scale);

        const hitRadius = 0.6; // tiles

        for (const agent of agents) {
            const dx = tileX - (agent.x + 0.5);
            const dy = tileY - (agent.y + 0.5);
            if (Math.sqrt(dx * dx + dy * dy) < hitRadius) {
                return agent;
            }
        }
        return null;
    }

    function handleClick(e) {
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const agent = getAgentAt(x, y);
        if (agent) {
            BossModApp.selectAgent(agent);
        }
    }

    function handleMouseMove(e) {
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const agent = getAgentAt(x, y);
        const changed = (agent?.id !== hoveredAgent?.id);

        hoveredAgent = agent;
        canvas.style.cursor = agent ? 'pointer' : 'default';

        if (changed) render();
    }

    // ─── Public API ───

    /**
     * Update agent positions and re-render.
     * Called when WebSocket delivers new world state.
     * @param {Array} newAgents - [{id, name, x, y, color, status}]
     */
    function updateAgents(newAgents) {
        const byId = new Map(agents.map(agent => [agent.id, agent]));
        agents = newAgents.map((incoming) => {
            const existing = byId.get(incoming.id);
            const animating = agentAnimations.has(incoming.id);
            if (!existing) {
                return {
                    ...incoming,
                    serverX: incoming.x,
                    serverY: incoming.y,
                };
            }

            const merged = {
                ...existing,
                ...incoming,
                serverX: incoming.x,
                serverY: incoming.y,
            };

            if (animating && incoming.status === 'in_transit') {
                merged.status = incoming.status;
                return merged;
            }

            if (animating && incoming.status !== 'in_transit') {
                agentAnimations.delete(incoming.id);
            }

            merged.x = incoming.x;
            merged.y = incoming.y;
            return merged;
        });
        ensureAnimationLoop();
        render();
    }

    function handleActivity(event) {
        if (!event || event.event !== 'agent_moved' || !Array.isArray(event.path) || !event.agent_id) {
            return;
        }
        startPathAnimation(event.agent_id, event.path, event.tiles_per_second);
    }

    function startPathAnimation(agentId, path, tilesPerSecond) {
        if (!Array.isArray(path) || path.length < 2) return;

        const speed = Number(tilesPerSecond) > 0 ? Number(tilesPerSecond) : 4;
        const now = performance.now();
        const normalizedPath = path.map(([x, y]) => ({ x, y }));

        const agent = agents.find((item) => item.id === agentId);
        if (agent) {
            agent.x = normalizedPath[0].x;
            agent.y = normalizedPath[0].y;
            agent.status = 'in_transit';
        }

        agentAnimations.set(agentId, {
            path: normalizedPath,
            startedAt: now,
            tileDurationMs: 1000 / speed,
        });

        ensureAnimationLoop();
        render();
    }

    function ensureAnimationLoop() {
        if (animationFrameId || agentAnimations.size === 0) return;
        animationFrameId = requestAnimationFrame(stepAnimations);
    }

    function stepAnimations(now) {
        animationFrameId = null;
        let needsAnotherFrame = false;
        let changed = false;

        for (const [agentId, animation] of agentAnimations.entries()) {
            const agent = agents.find((item) => item.id === agentId);
            if (!agent) {
                agentAnimations.delete(agentId);
                continue;
            }

            const segmentProgress = (now - animation.startedAt) / animation.tileDurationMs;
            const segmentIndex = Math.floor(segmentProgress);

            if (segmentIndex >= animation.path.length - 1) {
                const finalPoint = animation.path[animation.path.length - 1];
                agent.x = finalPoint.x;
                agent.y = finalPoint.y;
                agentAnimations.delete(agentId);
                changed = true;
                continue;
            }

            const start = animation.path[segmentIndex];
            const end = animation.path[segmentIndex + 1];
            const t = Math.max(0, Math.min(segmentProgress - segmentIndex, 1));
            const nextX = start.x + ((end.x - start.x) * t);
            const nextY = start.y + ((end.y - start.y) * t);

            if (agent.x !== nextX || agent.y !== nextY) {
                agent.x = nextX;
                agent.y = nextY;
                changed = true;
            }
            needsAnotherFrame = true;
        }

        if (changed) {
            render();
        }

        if (needsAnotherFrame && agentAnimations.size > 0) {
            animationFrameId = requestAnimationFrame(stepAnimations);
        }
    }

    return {
        init,
        render,
        updateAgents,
        handleActivity,
        showThought,
    };
})();

// Boot on DOM ready
document.addEventListener('DOMContentLoaded', OfficeCanvas.init);
