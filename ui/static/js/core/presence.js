/**
 * BossMod AI — who is mid-turn, for the life of the page.
 *
 * The runtime worker's dispatcher is the one producer of presence. The app
 * process keeps a snapshot of its `agent_presence` deltas and sends it as a
 * `presence_snapshot` frame on every WebSocket connect, and as an empty one
 * when the worker exits. `attach` turns both into the store's `presence`
 * slice, which outlives every place and conversation, so leaving Chat,
 * reloading, reconnecting, or opening a conversation after its turn started
 * no longer loses the row. Surfaces only select from the slice: a thread its
 * own channel's rows, a DM, the desk and the People rail one agent's row
 * through `agentRow`.
 *
 * Pure apart from `attach`, and it names no other module, so index.html loads
 * it straight after core/bus.js, ahead of everything that reads it.
 */
const BossModPresence = (() => {

    /** The phases a kept row can hold. `idle` is the removal, never a row. */
    const ROW_PHASES = Object.freeze(['thinking', 'queued']);

    /**
     * @typedef {object} PresenceRow
     * @property {string} agentId
     * @property {string} name  The agent's display name.
     * @property {string|null} channelId  The thread the turn is for; null for
     *   a DM or desk turn.
     * @property {'thinking'|'queued'} phase
     * @property {number} ahead  Turns waiting ahead of a queued row; 0 when
     *   thinking.
     * @property {string|null} since  ISO-8601 start of a thinking turn; null
     *   when queued.
     */

    /**
     * The agent id a payload names.
     *
     * @param {object} data
     * @returns {string}
     * @throws {Error} When the payload is not an object or its `agent_id` is
     *   missing or empty.
     */
    function requireAgentId(data) {
        if (!data || typeof data !== 'object') {
            throw new Error(`[presence] a presence payload must be an object, got ${JSON.stringify(data)}`);
        }
        if (typeof data.agent_id !== 'string' || !data.agent_id) {
            throw new Error(`[presence] agent_id must be a non-empty string, got ${JSON.stringify(data.agent_id)}`);
        }
        return data.agent_id;
    }

    /**
     * One row, from a snapshot row or an `agent_presence` payload. Both carry
     * the server's snake_case keys.
     *
     * The checks are the ones core/runtime/presence.py makes before the server
     * keeps or forwards a row, so the snapshot and the deltas can never
     * disagree about what a valid row is.
     *
     * @param {object} data  `{agent_id, agent_name, phase, channel_id?, ahead?, since?}`.
     * @returns {PresenceRow}
     * @throws {Error} Naming the field: a missing or empty `agent_id` or
     *   `agent_name`, a phase other than thinking or queued, or a queued row
     *   without a whole, non-negative `ahead`.
     */
    function normalizeRow(data) {
        const agentId = requireAgentId(data);
        if (!ROW_PHASES.includes(data.phase)) {
            throw new Error(`[presence] a row's phase must be thinking or queued, got ${JSON.stringify(data.phase)}`);
        }
        if (typeof data.agent_name !== 'string' || !data.agent_name) {
            throw new Error(`[presence] agent_name must be a non-empty string, got ${JSON.stringify(data.agent_name)}`);
        }
        const queued = data.phase === 'queued';
        if (queued && !(Number.isInteger(data.ahead) && data.ahead >= 0)) {
            throw new Error(`[presence] a queued row needs a whole, non-negative ahead, got ${JSON.stringify(data.ahead)}`);
        }
        return {
            agentId,
            name: data.agent_name,
            channelId: data.channel_id || null,
            phase: data.phase,
            ahead: queued ? data.ahead : 0,
            since: typeof data.since === 'string' ? data.since : null,
        };
    }

    /** @param {PresenceRow} a @param {PresenceRow} b @returns {boolean} */
    function sameRow(a, b) {
        return a.agentId === b.agentId && a.name === b.name && a.channelId === b.channelId
            && a.phase === b.phase && a.ahead === b.ahead && a.since === b.since;
    }

    /**
     * Apply one `agent_presence` delta, keyed `(agentId, channelId)` exactly
     * as the server's PresenceRegistry keys it: thinking or queued sets the
     * row under the key, idle removes it, and an idle for a row that is not
     * there changes nothing (idle is idempotent).
     *
     * @param {PresenceRow[]} rows  The current slice; never mutated.
     * @param {object} data  One `agent_presence` payload.
     * @returns {PresenceRow[]} `rows` itself when nothing changed, since
     *   core/store.js notifies by reference; otherwise a new array.
     * @throws {Error} On a missing `agent_id`, an unknown phase, or any
     *   check `normalizeRow` makes.
     */
    function applyEvent(rows, data) {
        const agentId = requireAgentId(data);
        if (data.phase !== 'idle' && !ROW_PHASES.includes(data.phase)) {
            throw new Error(`[presence] phase must be thinking, queued or idle, got ${JSON.stringify(data.phase)}`);
        }
        const channelId = data.channel_id || null;
        const at = rows.findIndex((row) => row.agentId === agentId && row.channelId === channelId);
        if (data.phase === 'idle') {
            return at === -1 ? rows : rows.filter((_, index) => index !== at);
        }
        const next = normalizeRow(data);
        if (at === -1) return rows.concat([next]);
        if (sameRow(rows[at], next)) return rows;
        return rows.map((row, index) => (index === at ? next : row));
    }

    /**
     * What one agent is doing, wherever it is doing it: its thinking row if
     * it has one, otherwise its queued row with the fewest turns ahead,
     * otherwise null. The one owner of that rule; the DM, the desk and the
     * People rail all ask here.
     *
     * @param {PresenceRow[]} rows
     * @param {string} agentId
     * @returns {PresenceRow|null}
     */
    function agentRow(rows, agentId) {
        let queued = null;
        for (const row of rows) {
            if (row.agentId !== agentId) continue;
            if (row.phase === 'thinking') return row;
            if (!queued || row.ahead < queued.ahead) queued = row;
        }
        return queued;
    }

    /**
     * Keep `store.presence` in step with the socket: `presence_snapshot`
     * replaces the slice, `agent_presence` applies one delta. Attach before
     * the socket connects, so the connect snapshot is heard.
     *
     * A malformed payload is caught here, at the wire boundary, logged with
     * `console.error`, and leaves the slice as it was.
     *
     * @param {{store: object, bus: object}} deps
     * @returns {() => void} Disposer for both subscriptions.
     * @throws {Error} When the store or bus is missing, or the store has no
     *   `presence` array to keep.
     */
    function attach({ store, bus } = {}) {
        if (!store) throw new Error('[presence] deps.store is required');
        if (!bus) throw new Error('[presence] deps.bus is required');
        if (!Array.isArray(store.getState().presence)) {
            throw new Error('[presence] the store needs a presence slice (an array)');
        }

        const offSnapshot = bus.subscribe('presence_snapshot', (data) => {
            let rows;
            try {
                if (!Array.isArray(data)) throw new Error('[presence] a snapshot must be a list of rows');
                rows = data.map(normalizeRow);
            } catch (err) {
                console.error('[presence] dropped malformed event', data, err);
                return;
            }
            store.setState({ presence: rows });
        });

        const offDelta = bus.subscribe('agent_presence', (data) => {
            let rows;
            try {
                rows = applyEvent(store.getState().presence, data);
            } catch (err) {
                console.error('[presence] dropped malformed event', data, err);
                return;
            }
            store.setState({ presence: rows });
        });

        return () => {
            offSnapshot();
            offDelta();
        };
    }

    return { normalizeRow, applyEvent, agentRow, attach };
})();
