/**
 * BossMod AI — the roster's shape and an agent's visible state.
 *
 * The second of the three modules utils.js became. Everything here answers
 * "what does the world snapshot say about this agent, and what colour is
 * that": normalising a `world_update` row, merging it over what the roster
 * already held, and mapping a status or a running activity to its treatment.
 *
 * STATUS_CONFIG is the whole set of agent statuses `db.get_world_state()` can
 * emit — there is no `error` status, which is why the needs queue derives that
 * kind from the `diagnostic` topic instead (spec 5.1).
 */
const BossModAgentStatus = (() => {

    // ─── Roster shape ───

    const AGENT_COLOR_PALETTE = [
        '#3b82f6',
        '#f59e0b',
        '#10b981',
        '#f43f5e',
        '#8b5cf6',
        '#06b6d4',
        '#f97316',
        '#ec4899',
    ];

    /**
     * One `world_update` row as the UI holds it.
     *
     * @param {object} w  A raw row from db.get_world_state().
     * @returns {object} Every field defaulted; absent means null, never
     *   undefined, so a consumer can tell "not set" from "not sent".
     */
    function normalizeAgent(w) {
        return {
            id: w.id,
            name: w.name,
            role: w.role || null,
            description: w.description || null,
            done_fail_bar: w.done_fail_bar || null,
            x: w.x ?? 0,
            y: w.y ?? 0,
            color: w.color || '#3b82f6',
            status: w.status || 'idle',
            currentActivityKind: w.currentActivityKind || null,
            // When the running turn began (spec 12, carried items). The
            // presence row turns this into "working · 12m"; null is a turn
            // that is not running, and reads as "is thinking...".
            currentActivitySince: w.currentActivitySince || null,
            boundTaskId: w.boundTaskId || null,
            idle_since: w.idle_since || null,
            location: w.location || null,
        };
    }

    /**
     * The first palette colour no peer is using.
     *
     * @param {object[]} roster
     * @param {object} [options]
     * @param {string[]} [options.palette]   Defaults to AGENT_COLOR_PALETTE.
     * @param {string} [options.excludeId]   The agent being edited.
     * @returns {string} Wraps around the palette when every colour is taken.
     */
    function nextUnusedAgentColor(roster, options) {
        const opts = options || {};
        const palette = Array.isArray(opts.palette) && opts.palette.length
            ? opts.palette
            : AGENT_COLOR_PALETTE;
        const excludeId = opts.excludeId || null;
        const peers = (roster || []).filter(item => item && item.id !== excludeId);
        const used = new Set(
            peers
                .map(item => String(item.color || '').trim().toLowerCase())
                .filter(Boolean)
        );
        const unused = palette.find(color => !used.has(String(color).toLowerCase()));
        if (unused) return unused;
        return palette[peers.length % palette.length];
    }

    /**
     * Fold a fresh world snapshot over the roster already on screen.
     *
     * @param {object[]} roster    What the store holds now.
     * @param {object[]} incoming  Normalised rows from the snapshot.
     * @returns {object[]} A new array. A non-array `incoming` returns a copy of
     *   the roster rather than emptying it — a malformed frame must not blank
     *   the rail.
     */
    function mergeRosterFromWorld(roster, incoming) {
        if (!Array.isArray(incoming)) {
            return Array.isArray(roster) ? roster.slice() : [];
        }
        const prior = new Map((roster || []).filter(item => item && item.id).map(item => [item.id, item]));
        return incoming.filter(item => item && item.id).map(runtime => {
            const prev = prior.get(runtime.id) || {};
            return {
                ...prev,
                ...runtime,
                id: runtime.id,
                name: runtime.name || prev.name,
                role: runtime.role ?? prev.role ?? null,
                description: runtime.description ?? prev.description ?? null,
                done_fail_bar: runtime.done_fail_bar ?? prev.done_fail_bar ?? null,
                color: runtime.color || prev.color || '#3b82f6',
                status: runtime.status || prev.status || 'idle',
                currentActivityKind: runtime.currentActivityKind ?? prev.currentActivityKind ?? null,
                idle_since: runtime.idle_since ?? prev.idle_since ?? null,
                x: runtime.x ?? prev.x ?? 0,
                y: runtime.y ?? prev.y ?? 0,
                location: runtime.location || prev.location || 'Unknown',
            };
        });
    }

    // ─── Status colour mappings ───

    const STATUS_CONFIG = {
        waiting:       { hex: '#2563eb', classes: 'bg-blue-50 text-blue-700',      dot: 'bg-blue-500' },
        blocked:       { hex: '#ef4444', classes: 'bg-red-50 text-red-700',        dot: 'bg-red-500' },
        work_active:   { hex: '#f59e0b', classes: 'bg-amber-50 text-amber-700',    dot: 'bg-amber-500' },
        social_active: { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500' },
        in_transit:    { hex: '#3b82f6', classes: 'bg-blue-50 text-blue-700',       dot: 'bg-blue-500' },
        idle:          { hex: '#94a3b8', classes: 'bg-slate-50 text-slate-600',     dot: 'bg-slate-400' },
    };

    const ACTIVITY_CONFIG = {
        assignment:   { hex: '#f59e0b', classes: 'bg-amber-50 text-amber-700',    dot: 'bg-amber-500', label: 'assignment' },
        break:        { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'break' },
        conversation: { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'conversation' },
        meeting:      { hex: '#3b82f6', classes: 'bg-blue-50 text-blue-700',       dot: 'bg-blue-500', label: 'meeting' },
        movement:     { hex: '#3b82f6', classes: 'bg-blue-50 text-blue-700',       dot: 'bg-blue-500', label: 'moving' },
        social:       { hex: '#10b981', classes: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500', label: 'social' },
        work:         { hex: '#f59e0b', classes: 'bg-amber-50 text-amber-700',    dot: 'bg-amber-500', label: 'working' },
    };

    const DEFAULT_STATUS = STATUS_CONFIG.idle;

    /** What the agent is doing wins over what they are; idle is the floor. */
    function getDisplayState(status, currentActivityKind = null) {
        return ACTIVITY_CONFIG[currentActivityKind] || STATUS_CONFIG[status] || DEFAULT_STATUS;
    }

    /**
     * @param {string} status
     * @param {string|null} [currentActivityKind]
     * @returns {string} A hex colour, for the canvas.
     */
    function getStatusColor(status, currentActivityKind = null) {
        return getDisplayState(status, currentActivityKind).hex;
    }

    /**
     * @param {string} status
     * @param {string|null} [currentActivityKind]
     * @returns {string} Tailwind background/text classes, for pane markup.
     */
    function getStatusClasses(status, currentActivityKind = null) {
        return getDisplayState(status, currentActivityKind).classes;
    }

    /**
     * @param {string} status
     * @param {string|null} [currentActivityKind]
     * @returns {string} The Tailwind class for the dot.
     */
    function getStatusDot(status, currentActivityKind = null) {
        return getDisplayState(status, currentActivityKind).dot;
    }

    /**
     * @param {string} status
     * @param {string|null} [currentActivityKind]
     * @returns {string} The word the operator reads. An unrecognised activity
     *   falls through to the raw status rather than to a label that was never
     *   configured.
     */
    function getStatusLabel(status, currentActivityKind = null) {
        if (currentActivityKind && ACTIVITY_CONFIG[currentActivityKind]) {
            return ACTIVITY_CONFIG[currentActivityKind].label;
        }
        return status || 'idle';
    }

    return {
        normalizeAgent,
        AGENT_COLOR_PALETTE,
        nextUnusedAgentColor,
        mergeRosterFromWorld,
        getStatusColor,
        getStatusClasses,
        getStatusDot,
        getStatusLabel,
    };
})();
