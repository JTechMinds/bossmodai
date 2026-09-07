/**
 * BossMod AI — the office canvas's motion layer.
 *
 * Everything on the floor that changes between server updates: agents walking
 * a path, and thought bubbles living out their lifetime. It owns the state,
 * the interpolation, and the clock that drives them — a layer that needed its
 * consumer to remember to pump it would be a worse API, because forgetting is
 * silent.
 *
 * It never draws and never reads the document. office-canvas.js gives it a
 * repaint function and a way to reach the agent list; everything else is here.
 */
const BossModCanvasMotion = (() => {

    /** Fallback pace when the mover reports none. */
    const DEFAULT_TILES_PER_SECOND = 4;
    /** Fade window, in ms, at the end of a bubble's life. */
    const FADE_MS = 500;
    /** Longest a bubble-expiry repaint may sleep, in ms. */
    const EXPIRY_TICK_MS = 100;

    /**
     * Build a motion layer.
     *
     * @param {object} deps
     * @param {() => void} deps.repaint  Called whenever the layer has moved.
     * @param {() => object[]} deps.getAgents  The live agent list. Read through
     *   a getter, not captured, because the canvas replaces the array on every
     *   world update and a captured reference would animate a dead list.
     * @param {number} [deps.thoughtDurationMs=4000]
     * @returns {object} See each method's docs.
     * @throws {Error} When repaint or getAgents is missing.
     */
    function createMotion(deps) {
        const { repaint, getAgents } = deps || {};
        if (typeof repaint !== 'function') throw new Error('[canvas-motion] deps.repaint is required');
        if (typeof getAgents !== 'function') throw new Error('[canvas-motion] deps.getAgents is required');

        const walks = new Map();
        const thoughts = new Map();
        let thoughtDurationMs = (deps && deps.thoughtDurationMs) || 4000;
        let frameId = null;
        let expiryTimer = null;
        let stopped = false;

        function agentById(agentId) {
            return getAgents().find((item) => item && item.id === agentId) || null;
        }

        /**
         * Advance every walk to `now`, writing positions onto the agents.
         * Finished walks are dropped here, so `walks.size` is the loop's own
         * stop condition and there is no completion event to miss.
         *
         * @returns {boolean} Whether anything actually moved.
         */
        function advance(now) {
            let moved = false;
            for (const [agentId, walk] of [...walks.entries()]) {
                const agent = agentById(agentId);
                if (!agent) {
                    walks.delete(agentId);
                    continue;
                }
                const progress = (now - walk.startedAt) / walk.tileDurationMs;
                const index = Math.floor(progress);
                let point;
                if (index >= walk.points.length - 1) {
                    point = walk.points[walk.points.length - 1];
                    walks.delete(agentId);
                } else {
                    const from = walk.points[index];
                    const to = walk.points[index + 1];
                    const t = Math.max(0, Math.min(progress - index, 1));
                    point = {
                        x: from.x + ((to.x - from.x) * t),
                        y: from.y + ((to.y - from.y) * t),
                    };
                }
                if (agent.x !== point.x || agent.y !== point.y) {
                    agent.x = point.x;
                    agent.y = point.y;
                    moved = true;
                }
            }
            return moved;
        }

        function frame(now) {
            frameId = null;
            if (advance(now)) repaint();
            pump();
        }

        /** Keep a frame or an expiry timer pending for as long as either is due. */
        function pump() {
            if (stopped) return;
            if (walks.size > 0) {
                if (frameId === null) frameId = requestAnimationFrame(frame);
                return;  // The frame loop already repaints; no expiry timer needed.
            }
            if (expiryTimer !== null || thoughts.size === 0) return;
            const now = Date.now();
            let soonest = Infinity;
            for (const thought of thoughts.values()) {
                soonest = Math.min(soonest, (thought.at + thoughtDurationMs) - now);
            }
            expiryTimer = setTimeout(() => {
                expiryTimer = null;
                repaint();
                pump();
            }, Math.max(0, Math.min(soonest, EXPIRY_TICK_MS)));
        }

        return {
            /**
             * Begin a walk along a tile path, snapping the agent to its start.
             *
             * @param {string} agentId
             * @param {Array<number[]>} path  `[[x, y], …]`, at least two points.
             * @param {number} tilesPerSecond  Non-positive falls back to the default.
             * @returns {void}
             * @throws {Error} On a path of fewer than two points — there is no
             *   walk to animate, and ignoring it would hide a bad broadcast.
             */
            startWalk(agentId, path, tilesPerSecond) {
                if (!Array.isArray(path) || path.length < 2) {
                    throw new Error('[canvas-motion] a walk needs at least two points');
                }
                const speed = Number(tilesPerSecond) > 0
                    ? Number(tilesPerSecond)
                    : DEFAULT_TILES_PER_SECOND;
                const points = path.map(([x, y]) => ({ x, y }));
                walks.set(agentId, {
                    points, startedAt: performance.now(), tileDurationMs: 1000 / speed,
                });
                const agent = agentById(agentId);
                if (agent) {
                    agent.x = points[0].x;
                    agent.y = points[0].y;
                    agent.status = 'in_transit';
                }
                pump();
            },

            /**
             * @param {string} agentId
             * @returns {boolean} Whether this agent is mid-walk.
             */
            isWalking(agentId) {
                return walks.has(agentId);
            },

            /**
             * Abandon an agent's walk, if any.
             * @param {string} agentId
             * @returns {void}
             */
            dropWalk(agentId) {
                walks.delete(agentId);
            },

            /**
             * Float a thought above an agent, replacing any earlier one.
             *
             * @param {string} agentId
             * @param {string} text
             * @returns {void}
             */
            showThought(agentId, text) {
                thoughts.set(agentId, { text, at: Date.now() });
                pump();
            },

            /**
             * @param {number} ms  Non-positive is rejected rather than accepted
             *   as "never show a bubble", which is not a setting anyone means.
             * @returns {void}
             * @throws {RangeError} On a non-positive duration.
             */
            setThoughtDuration(ms) {
                if (!(ms > 0)) throw new RangeError(`[canvas-motion] bad thought duration: ${ms}`);
                thoughtDurationMs = ms;
            },

            /**
             * Drawable bubbles for this instant, positioned on their agent.
             * Expired ones are dropped here — the only place they are removed.
             *
             * @returns {Array<{x: number, y: number, text: string, opacity: number}>}
             */
            bubbles() {
                const now = Date.now();
                const out = [];
                for (const [agentId, thought] of [...thoughts.entries()]) {
                    const remaining = (thought.at + thoughtDurationMs) - now;
                    if (remaining <= 0) {
                        thoughts.delete(agentId);
                        continue;
                    }
                    const agent = agentById(agentId);
                    if (!agent) continue;
                    out.push({
                        x: agent.x,
                        y: agent.y,
                        text: thought.text,
                        opacity: remaining < FADE_MS ? remaining / FADE_MS : 1,
                    });
                }
                return out;
            },

            /**
             * Restart the clock after the agent list was replaced.
             * @returns {void}
             */
            sync() {
                pump();
            },

            /**
             * Stop the clock and forget every walk and thought.
             * @returns {void}
             */
            destroy() {
                stopped = true;
                if (frameId !== null) cancelAnimationFrame(frameId);
                if (expiryTimer !== null) clearTimeout(expiryTimer);
                frameId = null;
                expiryTimer = null;
                walks.clear();
                thoughts.clear();
            },
        };
    }

    return { createMotion, DEFAULT_TILES_PER_SECOND, FADE_MS };
})();
