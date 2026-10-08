/**
 * BossMod AI — each floor remembers its own chat.
 *
 * `state.conversationId`/`conversationKind` are the one open conversation,
 * and every door writes them. `state.conversationByFloor` is the last chat
 * on each floor. This module keeps the two in step through the store, so no
 * door has to know about floors:
 *
 * - The floor changes: open that floor's remembered chat if it is still
 *   valid, otherwise the empty state.
 * - A conversation opens: record it under its own floor, and when that is
 *   not the current floor, move the operator there. The open conversation
 *   therefore always belongs to `currentFloorId`.
 *
 * It draws nothing, and it does nothing until `settle()`: before the roster
 * and thread lists have loaded, no conversation's floor is knowable.
 */
const BossModFloorChat = (() => {
    const KINDS = Object.freeze(['agent', 'thread']);

    /**
     * The floor a conversation lives on, from the loaded lists.
     *
     * @param {object} state  Store state; reads `roster` and `threads`.
     * @param {string|null} id  Agent id or thread id.
     * @param {string|null} kind  `'agent'` or `'thread'`.
     * @returns {string|null} The floor id, or null when the row is not loaded
     *   (or the kind is not one of the two).
     */
    function conversationFloor(state, id, kind) {
        if (!id || KINDS.indexOf(kind) === -1) return null;
        const pool = kind === 'thread' ? state.threads : state.roster;
        const rows = Array.isArray(pool) ? pool : [];
        const row = rows.find((item) => item && item.id === id);
        if (!row) return null;
        return BossModFloorScope.floorOf(row) || null;
    }

    /**
     * The conversation to open on a floor: its remembered entry, only when
     * that conversation is still loaded and still on that floor. A removed
     * agent, a moved agent or thread, and a deleted floor's entry all resolve
     * to the empty state.
     *
     * @param {object} state  Store state; reads `conversationByFloor`.
     * @param {string} floorId
     * @returns {{conversationId: string|null, conversationKind: 'agent'|'thread'|null}}
     */
    function resolve(state, floorId) {
        const map = state.conversationByFloor || {};
        const entry = Object.prototype.hasOwnProperty.call(map, floorId) ? map[floorId] : null;
        if (entry && conversationFloor(state, entry.id, entry.kind) === floorId) {
            return { conversationId: entry.id, conversationKind: entry.kind };
        }
        return { conversationId: null, conversationKind: null };
    }

    /**
     * Record a floor's chat without mutating the map.
     *
     * @param {object} map  `{ [floorId]: {id, kind} }`.
     * @param {string} floorId
     * @param {string} id
     * @param {'agent'|'thread'} kind
     * @returns {object} A new map, or the SAME map when the entry is already
     *   equal, so a no-op notifies no subscriber and triggers no session save.
     */
    function remember(map, floorId, id, kind) {
        const current = map && map[floorId];
        if (current && current.id === id && current.kind === kind) return map;
        const next = Object.assign({}, map);
        next[floorId] = { id, kind };
        return next;
    }

    /**
     * Wire the floor ↔ conversation rule to a store.
     *
     * @param {{store: object}} deps  The application store.
     * @returns {{settle: () => void, destroy: () => void}} `settle()` arms the
     *   rule once the roster and thread lists have both loaded (a second call
     *   is a no-op); `destroy()` drops both subscriptions.
     * @throws {Error} When no store is given.
     */
    function attach(deps) {
        const store = deps && deps.store;
        if (!store) throw new Error('[floor-chat] attach needs a store');
        let ready = false;

        function onConversation() {
            if (!ready) return;
            const state = store.getState();
            const id = state.conversationId;
            const kind = state.conversationKind;
            // Chat shows the empty state without both, so there is nothing
            // open to record.
            if (!id || !kind) return;
            // Not in the lists yet means a creation door just opened it, and
            // both create on the current floor (hireFloorId, and the server's
            // shared-home rule for threads), so the current floor is its floor.
            const floor = conversationFloor(state, id, kind) || state.currentFloorId;
            const map = state.conversationByFloor || {};
            const nextMap = remember(map, floor, id, kind);
            const patch = {};
            if (nextMap !== map) patch.conversationByFloor = nextMap;
            if (floor !== state.currentFloorId) patch.currentFloorId = floor;
            if (Object.keys(patch).length > 0) store.setState(patch);
        }

        function onFloor() {
            if (!ready) return;
            const state = store.getState();
            const target = resolve(state, state.currentFloorId);
            if (target.conversationId === state.conversationId
                && target.conversationKind === state.conversationKind) return;
            store.setState(target);
        }

        const offFloor = store.subscribe((s) => s.currentFloorId, onFloor);
        const offConversation = store.subscribe(
            (s) => `${s.conversationKind}:${s.conversationId}`, onConversation);

        function settle() {
            if (ready) return;
            ready = true;
            const state = store.getState();
            // A click during boot is the operator's latest choice; it wins
            // over the floor's remembered chat.
            if (state.conversationId && state.conversationKind) onConversation();
            else onFloor();
        }

        function destroy() {
            offFloor();
            offConversation();
        }

        return { settle, destroy };
    }

    return { conversationFloor, resolve, remember, attach };
})();
