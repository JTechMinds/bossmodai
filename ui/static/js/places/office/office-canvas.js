/**
 * BossMod AI — the office canvas.
 *
 * Owns one <canvas>: its size, its palette, its pointer input, and the render
 * pass. What moves lives in canvas-motion.js; what things look like lives in
 * canvas-sprites.js. This is the only one of the three that touches the
 * document, and the split is what keeps the other two reasonable about.
 *
 * Resize contract (test_ui_ws_canvas_p2.py): it listens for `panel-resize` and
 * never binds `window.resize`. A hidden container reports 0x0, so a
 * ResizeObserver catches the moment the place becomes visible, and the place
 * fires one `panel-resize` on mount.
 */
const BossModOfficeCanvas = (() => {
    const SPRITES = BossModCanvasSprites;

    const TILE_SIZE = 28;
    const MAX_SCALE = 1.5;
    /** Below this the container is hidden or collapsed; sizing would yield 0. */
    const MIN_CONTAINER_PX = 8;
    const HIT_RADIUS_TILES = 0.6;

    /**
     * Palette key -> custom property in tokens.css. A canvas cannot use CSS,
     * so the values are read once at construction; tokens.css stays the single
     * source of truth for colour rather than a second palette living here.
     */
    const PALETTE_TOKENS = Object.freeze({
        floor: '--office-floor', wall: '--office-wall', desk: '--office-desk',
        meeting: '--blue', breakRoom: '--teal', transit: '--office-transit',
        door: '--amber', chair: '--office-chair', grid: '--office-grid',
        pill: '--panel', roomInk: '--muted', deskInk: '--office-desk-ink',
        agentDefault: '--accent', agentShadow: '--office-shadow', nameInk: '--ink',
        bubbleBg: '--panel', bubbleLine: '--line', bubbleInk: '--ink',
    });

    /**
     * Resolve the sprite palette from the document's custom properties.
     *
     * @returns {object} Palette keyed as canvas-sprites.js expects.
     * @throws {Error} When a token resolves empty. A missing token would paint
     *   transparent shapes — a map that looks broken for no visible reason.
     */
    function readPalette() {
        const computed = window.getComputedStyle(document.documentElement);
        const palette = {};
        for (const [key, token] of Object.entries(PALETTE_TOKENS)) {
            const value = computed.getPropertyValue(token).trim();
            if (!value) throw new Error(`[office-canvas] tokens.css defines no ${token}`);
            palette[key] = value;
        }
        return palette;
    }

    /**
     * Build a canvas controller.
     *
     * @param {object} deps
     * @param {HTMLCanvasElement} deps.canvas
     * @param {HTMLElement} deps.container  The element the canvas sizes itself
     *   to. Injected rather than looked up by id: canvas.js reached for
     *   `document.getElementById('canvas-container')`, tying the renderer to
     *   one page layout.
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {(agentId: string) => void} deps.onAgentClick
     * @returns {{init: () => Promise<void>, updateAgents: (agents: object[]) => void,
     *   handleActivity: (event: object) => void, showThought: (id: string, text: string) => void,
     *   resize: () => void, destroy: () => void}}
     * @throws {Error} When any dependency is missing.
     */
    function createOfficeCanvas(deps) {
        const { canvas, container, api, onAgentClick } = deps || {};
        if (!canvas) throw new Error('[office-canvas] deps.canvas is required');
        if (!container) throw new Error('[office-canvas] deps.container is required');
        if (typeof api !== 'function') throw new Error('[office-canvas] deps.api is required');
        if (typeof onAgentClick !== 'function') throw new Error('[office-canvas] deps.onAgentClick is required');

        const ctx2d = canvas.getContext('2d');
        const palette = readPalette();
        const disposers = [];

        let mapData = null;
        let agents = [];
        let hoveredId = null;
        let scale = 1;
        let destroyed = false;

        const motion = BossModCanvasMotion.createMotion({
            repaint: () => render(),
            getAgents: () => agents,
        });

        function sizeCanvas() {
            if (!mapData) return;
            const boxW = container.clientWidth;
            const boxH = container.clientHeight;
            if (boxW < MIN_CONTAINER_PX || boxH < MIN_CONTAINER_PX) return;
            const pixelW = mapData.width * TILE_SIZE;
            const pixelH = mapData.height * TILE_SIZE;
            scale = Math.min(boxW / pixelW, boxH / pixelH, MAX_SCALE);
            canvas.width = Math.floor(pixelW * scale);
            canvas.height = Math.floor(pixelH * scale);
            ctx2d.imageSmoothingEnabled = false;
        }

        function render() {
            if (!mapData || destroyed) return;
            const opts = { tileSize: TILE_SIZE, palette };
            ctx2d.clearRect(0, 0, canvas.width, canvas.height);
            ctx2d.save();
            ctx2d.scale(scale, scale);
            SPRITES.drawTiles(ctx2d, mapData, opts);
            SPRITES.drawRoomLabels(ctx2d, mapData.rooms, opts);
            SPRITES.drawDesks(ctx2d, mapData.desks, opts);
            SPRITES.drawAgents(ctx2d, agents, {
                ...opts, hoveredId, statusColor: BossModUtils.getStatusColor,
            });
            SPRITES.drawThoughtBubbles(ctx2d, motion.bubbles(), opts);
            ctx2d.restore();
        }

        function agentAt(clientX, clientY) {
            const rect = canvas.getBoundingClientRect();
            const tileX = (clientX - rect.left) / (TILE_SIZE * scale);
            const tileY = (clientY - rect.top) / (TILE_SIZE * scale);
            return agents.find((agent) => {
                const dx = tileX - (agent.x + 0.5);
                const dy = tileY - (agent.y + 0.5);
                return Math.sqrt(dx * dx + dy * dy) < HIT_RADIUS_TILES;
            }) || null;
        }

        function bind(target, type, handler) {
            target.addEventListener(type, handler);
            disposers.push(() => target.removeEventListener(type, handler));
        }

        bind(canvas, 'click', (event) => {
            const agent = agentAt(event.clientX, event.clientY);
            if (agent) onAgentClick(agent.id);
        });
        bind(canvas, 'mousemove', (event) => {
            const agent = agentAt(event.clientX, event.clientY);
            const next = agent ? agent.id : null;
            if (next === hoveredId) return;
            hoveredId = next;
            canvas.style.cursor = agent ? 'pointer' : 'default';
            render();
        });
        bind(canvas, 'mouseleave', () => {
            hoveredId = null;
            canvas.style.cursor = 'default';
            render();
        });
        // The single resize path, written out rather than routed through
        // bind() so the contract is legible at the call site: this module
        // listens for 'panel-resize' and never for 'resize'.
        const onPanelResize = () => { sizeCanvas(); render(); };
        window.addEventListener('panel-resize', onPanelResize);
        disposers.push(() => window.removeEventListener('panel-resize', onPanelResize));

        const observer = new ResizeObserver(() => { sizeCanvas(); render(); });
        observer.observe(container);
        disposers.push(() => observer.disconnect());

        /**
         * Apply the operator's configured bubble lifetime.
         *
         * @returns {Promise<void>} Never rejects: the duration is a preference,
         *   and failing to read it must not stop the map rendering. The failure
         *   is logged, never swallowed, and the documented default holds.
         */
        async function loadThoughtDuration() {
            try {
                const res = await api('/api/settings?category=simulation', { cache: 'no-store' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const rows = await res.json();
                const row = rows.find((item) => item.key === 'thought_bubble_duration_ms');
                const ms = row ? parseInt(row.value, 10) : 0;
                if (ms > 0) motion.setThoughtDuration(ms);
            } catch (err) {
                console.error('[office-canvas] thought duration unreadable; keeping the default', err);
            }
        }

        return {
            /**
             * Fetch the tilemap and paint the first frame.
             *
             * @returns {Promise<void>}
             * @throws {Error} When /api/map fails. The place turns this into its
             *   error state; canvas.js logged and returned, leaving a blank
             *   rectangle that read as an empty office.
             */
            async init() {
                const res = await api('/api/map', { cache: 'no-store' });
                if (!res.ok) throw new Error(`[office-canvas] /api/map failed: HTTP ${res.status}`);
                mapData = await res.json();
                sizeCanvas();
                render();
                await loadThoughtDuration();
            },

            /**
             * Re-point the agent list, preserving any walk in progress.
             *
             * @param {object[]} nextAgents  Normalised roster rows.
             * @returns {void}
             */
            updateAgents(nextAgents) {
                const byId = new Map(agents.map((agent) => [agent.id, agent]));
                agents = (nextAgents || []).map((incoming) => {
                    const existing = byId.get(incoming.id);
                    if (!existing) return { ...incoming };
                    const merged = { ...existing, ...incoming };
                    // A walking agent keeps its interpolated position: the
                    // snapshot's coordinates are where the walk started, not
                    // where it is, and snapping back would undo it each tick.
                    if (motion.isWalking(incoming.id)) {
                        if (incoming.status === 'in_transit') {
                            merged.x = existing.x;
                            merged.y = existing.y;
                            return merged;
                        }
                        motion.dropWalk(incoming.id);
                    }
                    return merged;
                });
                motion.sync();
                render();
            },

            /**
             * Start a walk from an `agent_moved` activity broadcast.
             *
             * @param {object} event  Ignored unless it is a move carrying a path.
             * @returns {void}
             */
            handleActivity(event) {
                if (!event || event.event !== 'agent_moved') return;
                if (!Array.isArray(event.path) || event.path.length < 2 || !event.agent_id) return;
                motion.startWalk(event.agent_id, event.path, event.tiles_per_second);
                render();
            },

            /**
             * Float a thought above an agent for the configured lifetime.
             *
             * @param {string} agentId
             * @param {string} text  Empty text is ignored: an empty bubble is
             *   noise, not information.
             * @returns {void}
             */
            showThought(agentId, text) {
                if (!text) return;
                motion.showThought(agentId, text);
                render();
            },

            /**
             * Re-measure and repaint. The place calls this on mount, because a
             * canvas built inside a hidden container has zero size.
             * @returns {void}
             */
            resize() {
                sizeCanvas();
                render();
            },

            /**
             * Drop every listener, frame, and timer this controller created.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                motion.destroy();
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createOfficeCanvas };
})();
