/**
 * BossMod AI — Office chatter: the floor's agent-to-agent messages, under the
 * office summary in Chat's context column.
 *
 * Agents message each other outside any thread — audit verdicts, spec splits,
 * handoffs — and those rows were saved but never shown. This lists them for
 * the floor the operator is on, newest first, whatever conversation is open.
 *
 * The history lives on the server (GET /api/office/chatter, paged by a
 * `before` cursor at a server-owned page size); the runtime only announces new
 * rows, as the `peer_message` topic, in the same row shape. So there is no
 * client buffer to lose, and the hard part is reconciling the two:
 *
 * - A load generation drops a page that answers after a floor switch or a
 *   resync, and rows are keyed by message id, so a live row that is also in
 *   the page is not doubled. Live rows that arrive while page 1 is in flight
 *   are held and merged once it lands.
 * - A floor switch or a `resync` reloads page 1 and forgets older pages.
 * - Trim: while no older page is loaded, the list is cut back to the first
 *   page's length after each live row, so a long session does not grow the
 *   DOM without limit. A cut marks the list as having more, so a trimmed row
 *   stays one "Show older" away. A first page that held the floor's whole
 *   history (no `has_more`) does not set a cap: cutting to its length would
 *   drop live rows — on an empty floor, every one — with nothing to page.
 *
 * Names and colours come from the roster; the API sends ids only. A row whose
 * sender or recipient is not on the roster is not drawn. A row is never hidden
 * because one of its agents later moved floors: a conversation stays on the
 * floor it happened on.
 *
 * No live region on the list: messages arrive in bursts, and announcing each
 * would drown a screen-reader user. The list is a document read on demand.
 */
const BossModOfficeChatter = (() => {
    const { h } = BossModDom;

    const TITLE = 'Office chatter';
    const LOADING_COPY = 'Loading conversations…';
    const EMPTY_COPY = 'No one on this floor has messaged a coworker yet.';
    const MORE_LABEL = 'Show more';

    /** Ids for `aria-labelledby`; one per instance, so two never collide. */
    let instances = 0;

    /**
     * Read one chatter page off a response.
     *
     * @param {Response} res
     * @returns {Promise<{messages: object[], has_more: boolean}>}
     * @throws {Error} (rejects) The server's string `detail`, else
     *   `HTTP <status>`; or a malformed answer, which is said rather than
     *   painted as an empty floor.
     */
    async function readPage(res) {
        if (!res.ok) {
            let detail = '';
            try {
                const body = await res.json();
                detail = body && typeof body.detail === 'string' ? body.detail.trim() : '';
            } catch (err) {
                // A non-JSON error body has no sentence to show; the status is it.
                console.error('[office-chatter] unreadable error body', err);
            }
            throw new Error(detail || `HTTP ${res.status}`);
        }
        const body = await res.json();
        if (!body || !Array.isArray(body.messages) || typeof body.has_more !== 'boolean') {
            throw new Error('The office chatter answer is malformed');
        }
        return body;
    }

    /**
     * Build the Office chatter panel.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; `currentFloorId`,
     *   `roster` and `floors` are read.
     * @param {object} deps.bus  Event bus; `peer_message` and `resync`.
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {(agentId: string) => void} deps.openDesk  A name opens that
     *   agent's desk modal, the same door the office summary's seats use.
     * @returns {{ element: HTMLElement, destroy: () => void }}
     * @throws {Error} When store, bus, api or openDesk is missing.
     */
    function createOfficeChatter(deps) {
        const { store, bus, api, openDesk } = deps || {};
        if (!store) throw new Error('[office-chatter] deps.store is required');
        if (!bus) throw new Error('[office-chatter] deps.bus is required');
        if (typeof api !== 'function') throw new Error('[office-chatter] deps.api is required');
        if (typeof openDesk !== 'function') throw new Error('[office-chatter] deps.openDesk is required');

        const disposers = [];
        const load = BossModGates.createLoadGeneration();
        let destroyed = false;
        let floorId = BossModFloorScope.visibleFloorId(store.getState());
        // Names come from the roster, so rows wait for its first publish.
        let rosterLoaded = store.getState().roster.length > 0;
        /** 'loading' | 'error' | 'ready' for page 1. */
        let status = 'loading';
        /** Loaded rows, newest first, and the same rows by message id. */
        let items = [];
        let byId = new Map();
        let hasMore = false;
        /** The trim length, or null when page 1 held the whole history. */
        let trimCap = null;
        let olderLoaded = false;
        let olderPending = false;
        /** Live rows that landed while page 1 was in flight. */
        let heldLive = [];
        /** Clamps waiting for the list to be on screen and have a size. */
        const unmeasured = new Set();

        instances += 1;
        const titleId = `office-chatter-title-${instances}`;
        const metaEl = h('span', { class: 'context-meta' });
        // Empty until there is something to say; `.context-error:empty` hides it.
        const errorEl = h('p', { class: 'context-error', role: 'alert' });
        const stateEl = h('div', { class: 'office-chatter-state' });
        const listEl = h('ol', { class: 'office-chatter-list' });
        const rows = BossModDom.createKeyedList(listEl);
        const olderBtn = h('button', {
            class: 'btn-link office-chatter-older', type: 'button', onclick: () => void loadOlder(),
        }, 'Show older');
        const element = h('section', { class: 'office-chatter', 'aria-labelledby': titleId },
            h('div', { class: 'context-head' },
                h('h2', { class: 'context-title', id: titleId }, TITLE),
                metaEl),
            errorEl, stateEl, listEl, olderBtn);

        /** The roster entry for an id, or undefined when they are not on it. */
        function agentOf(agentId) {
            return store.getState().roster.find((agent) => agent && agent.id === agentId);
        }

        function who(agent) {
            return h('button', {
                class: 'office-chatter-who',
                type: 'button',
                // Contains the visible name, so speech input finds it (SC 2.5.3).
                'aria-label': `Open ${agent.name}'s desk`,
                onclick: () => openDesk(agent.id),
            },
                BossModAvatar.create({ name: agent.name, color: agent.color, size: 'chip' }),
                h('span', { class: 'office-chatter-name' }, agent.name));
        }

        /** One message row. Its clamp is measured once the list is on screen. */
        function buildRow({ row, sender, recipient }) {
            const clamp = BossModClampedMarkdown.create({
                text: row.content, className: 'office-chatter-text', moreLabel: MORE_LABEL,
            });
            unmeasured.add(clamp);
            return h('li', { class: 'office-chatter-row' },
                h('div', { class: 'office-chatter-head' },
                    who(sender),
                    h('span', { class: 'office-chatter-arrow', 'aria-hidden': 'true' }, '→'),
                    who(recipient),
                    h('time', { class: 'office-chatter-time', datetime: row.created_at },
                        BossModFormat.formatActivityTime(row.created_at))),
                clamp.element);
        }

        /**
         * Decide every pending clamp, but only while the list has a width: a
         * hidden column (the narrow layout, before its modal opens) measures
         * every text as fitting and would lift every clamp for good.
         */
        function measurePending(width) {
            if (!(width > 0)) return;
            unmeasured.forEach((clamp) => clamp.measure());
            unmeasured.clear();
        }
        const observer = new ResizeObserver((entries) => {
            measurePending(entries[entries.length - 1].contentRect.width);
        });
        observer.observe(listEl);
        disposers.push(() => observer.disconnect());

        /** Replace the state slot with one node, or empty it. */
        function showState(node) {
            stateEl.replaceChildren(...(node ? [node] : []));
        }

        function render() {
            const state = store.getState();
            metaEl.textContent = BossModFloorScope.floorName(state, floorId);
            if (status === 'loading' || !rosterLoaded) {
                rows.reset();
                unmeasured.clear();
                olderBtn.hidden = true;
                showState(h('p', { class: 'context-skeleton' }, LOADING_COPY));
                return;
            }
            if (status === 'error') {
                rows.reset();
                unmeasured.clear();
                olderBtn.hidden = true;
                showState(h('button', { class: 'btn', type: 'button', onclick: () => void reload() },
                    'Try again'));
                return;
            }
            const visible = items
                .map((row) => ({ row, sender: agentOf(row.from_agent_id), recipient: agentOf(row.to_agent_id) }))
                .filter((entry) => entry.sender && entry.recipient);
            showState(visible.length ? null : h('p', { class: 'context-empty' }, EMPTY_COPY));
            // Rebuilt only when a name or colour changes: a world tick hands
            // out new roster objects, and rebuilding would re-clamp a text the
            // operator had opened.
            rows.sync(visible, (entry) => entry.row.message_id,
                ({ sender, recipient }) => [sender.name, sender.color, recipient.name, recipient.color],
                buildRow);
            for (const clamp of unmeasured) {
                if (!clamp.element.isConnected) unmeasured.delete(clamp);
            }
            measurePending(listEl.clientWidth);
            olderBtn.hidden = !hasMore;
            olderBtn.disabled = olderPending;
        }

        /** Put one live row on top, unless it is already listed; then trim. */
        function prepend(row) {
            if (byId.has(row.message_id)) return;
            items.unshift(row);
            byId.set(row.message_id, row);
            if (olderLoaded || trimCap === null || items.length <= trimCap) return;
            items.splice(trimCap).forEach((cut) => byId.delete(cut.message_id));
            hasMore = true;
        }

        /**
         * Load page 1 for the visible floor, forgetting older pages.
         * @returns {Promise<void>} Never rejects; a failure is the error state.
         */
        async function reload() {
            const loadId = load.next();
            const requested = floorId;
            status = 'loading';
            items = [];
            byId = new Map();
            hasMore = false;
            trimCap = null;
            olderLoaded = false;
            olderPending = false;
            heldLive = [];
            errorEl.textContent = '';
            render();
            let page;
            try {
                page = await readPage(await api(
                    `/api/office/chatter?floor_id=${encodeURIComponent(requested)}`, { cache: 'no-store' }));
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[office-chatter] could not load the conversations', err);
                status = 'error';
                errorEl.textContent = err.message;
                render();
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;
            items = page.messages.slice();
            byId = new Map(items.map((row) => [row.message_id, row]));
            hasMore = page.has_more;
            trimCap = page.has_more ? items.length : null;
            status = 'ready';
            heldLive.splice(0).forEach(prepend);
            render();
        }

        /**
         * Append the page before the oldest loaded row.
         * @returns {Promise<void>} Never rejects; a failure keeps the loaded
         *   rows and is said in the error line.
         */
        async function loadOlder() {
            if (olderPending || status !== 'ready' || items.length === 0) return;
            const loadId = load.next();
            const oldest = items[items.length - 1].message_id;
            olderPending = true;
            render();
            let page;
            try {
                page = await readPage(await api(
                    `/api/office/chatter?floor_id=${encodeURIComponent(floorId)}`
                    + `&before=${encodeURIComponent(oldest)}`, { cache: 'no-store' }));
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[office-chatter] could not load older conversations', err);
                olderPending = false;
                errorEl.textContent = err.message;
                render();
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;
            page.messages.forEach((row) => {
                if (byId.has(row.message_id)) return;
                items.push(row);
                byId.set(row.message_id, row);
            });
            hasMore = page.has_more;
            olderLoaded = true;
            olderPending = false;
            errorEl.textContent = '';
            render();
        }

        disposers.push(bus.subscribe('peer_message', (row) => {
            if (!row || !row.message_id || row.floor_id !== floorId) return;
            if (status === 'loading') {
                heldLive.push(row);
                return;
            }
            if (status !== 'ready') return; // The retry reloads it.
            prepend(row);
            render();
        }));
        disposers.push(bus.subscribe('resync', () => void reload()));
        disposers.push(store.subscribe((s) => s.currentFloorId, () => {
            floorId = BossModFloorScope.visibleFloorId(store.getState());
            void reload();
        }));
        disposers.push(store.subscribe((s) => s.roster, () => {
            rosterLoaded = true;
            render();
        }));

        void reload();

        return {
            element,

            /**
             * Drain every subscription and invalidate any load in flight, so
             * a late page never lands in a detached node.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                load.next();
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createOfficeChatter };
})();
