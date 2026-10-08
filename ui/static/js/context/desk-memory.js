/**
 * BossMod AI — an agent's memory, as a layer over its desk.
 *
 * Memory is the small store the agent is shown on every turn: facts and
 * preferences it saved with `remember` on a reply, or with `memory add` while
 * working. It lives outside every agent
 * path (core/agent_loop/standing_prefs.py), so the desk's Files and Notes
 * cannot reach it; GET /api/agents/{id}/memory is its one read. The desk
 * head's Memory tool (context/desk-panel.js) opens this as a LAYER in the
 * modal frame, so the trail reads `‹ <Agent> › Memory` and ‹ comes back to
 * the desk.
 *
 * Each row is `n — text`: the number is the store's, assigned by the system
 * and never reused, which is why a remove is sent by number — a list read a
 * moment ago cannot remove a different memory. Remove opens the standard
 * confirm layer (core/overlays.js) over this one, as desk-pack.js's update
 * does; nothing reaches the API until the operator confirms. A 404 means the
 * memory was already gone (the agent removed it meanwhile), which is said as
 * a notice rather than an error; anything else keeps the row and says why.
 */
const BossModDeskMemory = (() => {
    const { h, clear } = BossModDom;

    const COPY = Object.freeze({
        title: 'Memory',
        loading: 'Loading memory…',
        emptyTitle: 'Nothing saved yet',
        emptyHint: 'Agents save small, lasting facts and preferences here as you talk with them.',
        loadFailed: 'Memory could not be loaded.',
        retry: 'Try again',
        confirmTitle: 'Remove memory?',
        confirmNote: 'The agent will no longer see it.',
        remove: 'Remove',
        cancel: 'Cancel',
        alreadyRemoved: 'That memory was already removed.',
        removeFailed: 'That memory could not be removed.',
    });

    /** The remove button's accessible name and tooltip: one string. */
    const removeLabel = (id) => `Remove memory #${id}`;

    /**
     * The error text a failed response carries, or `fallback`.
     * @param {Response} res
     * @param {string} fallback
     * @returns {Promise<string>}
     */
    async function failureText(res, fallback) {
        const data = await res.json().catch(() => ({}));
        const detail = data && data.detail;
        return typeof detail === 'string' && detail ? detail : fallback;
    }

    /**
     * Build one agent's Memory layer.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string}   deps.agentId
     * @returns {{ open: () => void, destroy: () => void }} `open` puts the
     *   layer up over whatever modal is on screen (one at a time) and reads
     *   the memory; `destroy` closes an open confirm and the layer, and drops
     *   an in-flight read.
     * @throws {Error} When a dependency is missing.
     */
    function createDeskMemory(deps) {
        const { api, agentId } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-memory] deps.api is required');
        if (!agentId) throw new Error('[desk-memory] deps.agentId is required');

        const loads = BossModGates.createLoadGeneration();
        const body = h('div', { class: 'desk-memory' });
        /** The open Memory layer and its open confirm, or null. */
        let layer = null;
        let confirm = null;
        /** null until the first read lands; then the store's rows, in order. */
        let memories = null;
        let loadError = '';
        /** `{tone: 'info'|'alert', text}` shown above the rows, or null. */
        let notice = null;
        /** The memory number whose DELETE is in flight, or null. */
        let removing = null;
        let destroyed = false;

        function renderNotice() {
            if (!notice) return null;
            if (notice.tone === 'alert') {
                return h('p', { class: 'context-error', role: 'alert' }, notice.text);
            }
            return h('div', { class: 'callout', 'data-tone': 'info', role: 'status' },
                h('p', { class: 'callout-body' }, notice.text));
        }

        function renderRow(memory) {
            const label = removeLabel(memory.id);
            return h('li', { class: 'desk-memory-row', 'data-memory-id': String(memory.id) },
                h('span', { class: 'desk-memory-id' }, `${memory.id} —`),
                h('span', { class: 'desk-memory-text' }, memory.text),
                h('button', {
                    class: 'header-icon-btn desk-memory-remove', type: 'button',
                    'aria-label': label, 'data-tooltip': label,
                    // One removal at a time: every row waits for the one in flight.
                    disabled: removing !== null,
                    onclick: () => confirmRemove(memory),
                }, h('i', { 'data-lucide': 'trash-2', 'aria-hidden': 'true' })));
        }

        function render() {
            clear(body);
            const top = renderNotice();
            if (top) body.append(top);
            if (loadError) {
                body.append(
                    h('p', { class: 'context-error', role: 'alert' }, loadError),
                    h('button', {
                        class: 'btn btn-sm', id: 'desk-memory-retry-btn', type: 'button',
                        onclick: () => { void refresh(); },
                    }, COPY.retry));
                return;
            }
            if (memories === null) {
                body.append(h('p', { class: 'context-skeleton' }, COPY.loading));
                return;
            }
            if (!memories.length) {
                body.append(h('div', { class: 'empty-slot' },
                    h('p', { class: 'context-empty' }, COPY.emptyTitle),
                    h('p', { class: 'context-hint' }, COPY.emptyHint)));
                return;
            }
            body.append(h('ul', { class: 'desk-memory-list' }, memories.map(renderRow)));
            // Rebuilt per render, so this layer paints its own glyphs.
            BossModIcons.paint(body, 'desk-memory');
        }

        /**
         * Re-read the agent's memory.
         * @returns {Promise<void>} Never rejects; a failure is the error state.
         */
        async function refresh() {
            const loadId = loads.next();
            let next;
            try {
                const res = await api(`/api/agents/${agentId}/memory`, { cache: 'no-store' });
                if (!res.ok) throw new Error(await failureText(res, COPY.loadFailed));
                const data = await res.json();
                if (!data || !Array.isArray(data.memories)) {
                    throw new Error('the memory response has no memories list');
                }
                next = data.memories;
            } catch (err) {
                if (destroyed || !loads.isCurrent(loadId)) return;
                console.error('[desk-memory] the memory could not be read', err);
                loadError = COPY.loadFailed;
                render();
                return;
            }
            if (destroyed || !loads.isCurrent(loadId)) return;
            memories = next;
            loadError = '';
            render();
        }

        async function runRemove(id) {
            removing = id;
            notice = null;
            render();
            try {
                const res = await api(`/api/agents/${agentId}/memory/${id}`, { method: 'DELETE' });
                // Gone already — the agent removed it meanwhile. Not a failure.
                if (res.status === 404) notice = { tone: 'info', text: COPY.alreadyRemoved };
                else if (!res.ok) throw new Error(await failureText(res, COPY.removeFailed));
            } catch (err) {
                console.error('[desk-memory] the memory could not be removed', err);
                removing = null;
                if (destroyed) return;
                notice = { tone: 'alert', text: (err && err.message) || COPY.removeFailed };
                render();
                return;
            }
            removing = null;
            if (destroyed || !layer) return;
            await refresh();
        }

        function confirmRemove(memory) {
            if (confirm || removing !== null) return;
            confirm = BossModOverlays.createModal({
                title: COPY.confirmTitle,
                closeOnBackdrop: true,
                body: h('div', { class: 'desk-memory-confirm' },
                    h('p', { class: 'desk-memory-quote' }, `“${memory.text}”`),
                    h('p', {}, COPY.confirmNote)),
                actions: [
                    { label: COPY.remove, tone: 'danger', id: 'desk-memory-confirm',
                        onSelect: () => { void runRemove(memory.id); } },
                    { label: COPY.cancel, tone: 'quiet' },
                ],
                onClose: () => { confirm = null; },
            });
        }

        return {
            open() {
                if (layer || destroyed) return;
                memories = null;
                loadError = '';
                notice = null;
                render();
                layer = BossModOverlays.createModal({
                    title: COPY.title,
                    closeOnBackdrop: true,
                    body,
                    onClose: () => {
                        layer = null;
                        // A read landing after the layer closed paints nothing.
                        loads.next();
                    },
                });
                void refresh();
            },

            destroy() {
                destroyed = true;
                loads.next();
                if (confirm) confirm.close();
                if (layer) layer.close();
            },
        };
    }

    return { COPY, createDeskMemory };
})();
