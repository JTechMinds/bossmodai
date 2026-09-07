/**
 * BossMod AI — the Log place.
 *
 * activity.js and diagnostics.js merged into one surface (spec 6.6): one list,
 * one filter bar, one row renderer, and a diagnostic that expands IN PLACE
 * rather than taking over the centre pane. The two feeds meet at
 * log-source.js's boundary and nothing above it knows there were two.
 *
 * Follow appends and sticks only when the operator is already at the edge new
 * rows arrive at — the transcript's rule, taken from core/dom.js rather than
 * written a second time. Rows arrive newest-first, so that edge is the top.
 *
 * Expanded detail nodes are cached by row key, so a row that arrives while a
 * turn is open does not make that turn re-fetch itself.
 */
const BossModLogPlace = (() => {
    const { h, clear, isNearEdge } = BossModDom;
    const ROW = BossModLogRow;

    let ctxRef = null;
    let source = null;
    let filters = null;
    let detail = null;
    let listEl = null;
    let scrollEl = null;
    let errorEl = null;
    let summaryEl = null;
    let moreBtn = null;
    let expanded = new Set();
    let details = new Map();
    let loading = true;
    let agentSignature = '';
    const disposers = [];

    function setError(message) {
        errorEl.textContent = message || '';
        errorEl.hidden = !message;
    }

    /** Everyone the rows or the roster know about, for the agent select. */
    function agentOptions(rows) {
        const seen = new Map();
        ctxRef.store.getState().roster.forEach((agent) => {
            seen.set(agent.id, { id: agent.id, name: agent.name });
        });
        rows.forEach((row) => {
            if (row.agentId && !seen.has(row.agentId)) {
                seen.set(row.agentId, { id: row.agentId, name: row.agentName });
            }
        });
        return Array.from(seen.values()).sort((a, b) => a.name.localeCompare(b.name));
    }

    function detailFor(row) {
        if (!details.has(row.key)) details.set(row.key, detail.render(row));
        return details.get(row.key);
    }

    function toggleRow(row) {
        if (expanded.has(row.key)) {
            expanded.delete(row.key);
            // The node is kept: reopening must not refetch a turn already read.
        } else {
            expanded.add(row.key);
            detailFor(row);
        }
        paint();
    }

    /**
     * Repaint the list.
     *
     * The viewport is restored unless the operator was already at the top and
     * following, which is the one case where moving it is what they asked for.
     *
     * @returns {void}
     */
    function paint() {
        const rows = source.filter(filters.filters());
        const stick = filters.following() && isNearEdge(scrollEl, 'top');
        const previousScroll = scrollEl.scrollTop;

        const options = agentOptions(source.rows());
        const signature = options.map((agent) => agent.id).join('|');
        // Rebuilding a <select> closes it. A row landing while the operator has
        // the agent list open must not take it away from them.
        if (signature !== agentSignature) {
            agentSignature = signature;
            filters.setAgents(options);
        }
        setError(source.getError());
        summaryEl.textContent = loading
            ? 'Loading…'
            : `${rows.length} event${rows.length === 1 ? '' : 's'}`;

        clear(listEl);
        if (loading) {
            listEl.append(ROW.renderSkeleton());
            return;
        }
        if (rows.length === 0) {
            const active = filters.filters();
            listEl.append(ROW.renderEmpty(
                Boolean(active.agentId || active.type || active.search)));
            return;
        }
        const list = h('div', { class: 'log-list' });
        rows.forEach((row) => list.append(ROW.renderRow(row, {
            expanded: expanded.has(row.key),
            onToggle: toggleRow,
            detail: expanded.has(row.key) ? detailFor(row) : null,
        })));
        listEl.append(list);
        moreBtn.hidden = !source.hasMore();
        listEl.append(moreBtn);

        scrollEl.scrollTop = stick ? 0 : previousScroll;
    }

    /**
     * Re-read both feeds for the current filters.
     *
     * Filters narrow BOTH server-side and client-side, and both halves are
     * needed: the activity feed pages, so an agent whose rows are older than
     * the first page would otherwise vanish when you filtered to them — which
     * is what activity.js re-fetched on every filter change to avoid.
     *
     * @param {boolean} [quiet=false] Skip the skeleton, for a re-read the
     *   operator did not explicitly ask for. A skeleton flashing on every
     *   keystroke is worse than a list that updates a beat late.
     * @returns {Promise<void>} Never rejects; the source reports failures.
     */
    async function reload(quiet) {
        loading = !quiet;
        paint();
        await source.load(filters.filters());
        loading = false;
        paint();
    }

    /**
     * Open the row a deep link named, if it is in the first page.
     *
     * @param {string} diagnosticId
     * @returns {void}
     */
    function expandDeepLink(diagnosticId) {
        const key = `diagnostic:${diagnosticId}`;
        const row = source.rows().find((item) => item.key === key);
        if (!row) {
            // Said rather than silently ignored: the need that linked here may
            // point at a turn older than the window this place loads.
            setError('That diagnostic is not in the most recent turns.');
            return;
        }
        expanded.add(key);
        detailFor(row);
        paint();
    }

    return {
        label: 'Log',
        icon: 'activity',

        /**
         * @param {HTMLElement} el
         * @param {object} ctx  `{ store, bus, api, needs, navigate }`.
         * @returns {void}
         */
        mount(el, ctx) {
            ctxRef = ctx;
            expanded = new Set();
            details = new Map();
            loading = true;
            agentSignature = '';
            const params = ctx.store.getState().placeParams;

            source = BossModLogSource.createLogSource({ api: ctx.api, bus: ctx.bus });
            detail = BossModDiagnosticDetail.createDetail({ api: ctx.api });
            filters = BossModLogFilters.createFilters({
                agentId: params.agentId || '',
                onChange: () => { void reload(true); },
                // Turning Follow back on catches the operator up immediately;
                // leaving them mid-list would make the toggle look inert.
                onFollow: (on) => { if (on) scrollEl.scrollTop = 0; },
            });

            summaryEl = h('p', { class: 'board-summary' }, '');
            errorEl = h('p', { class: 'files-error', role: 'alert', hidden: true });
            listEl = h('div', { class: 'log-body' });
            scrollEl = h('div', { class: 'log-scroll' }, listEl);
            moreBtn = h('button', {
                class: 'btn log-more', type: 'button', hidden: true,
                onclick: () => { void source.loadMore(filters.filters()).then(paint); },
            }, 'Load more');

            clear(el);
            el.append(h('div', { class: 'log-place' },
                h('header', { class: 'board-header' },
                    h('div', { class: 'board-title' },
                        h('h1', { tabindex: '-1' }, 'Log'), summaryEl),
                    filters.element),
                errorEl, scrollEl));

            // Resolve the deep-linked agent id to a name BEFORE the first
            // read. The activity feed narrows by name and carries no agent id,
            // so a link that arrived with only an id would otherwise miss every
            // activity row older than the first page until the operator
            // touched the filter.
            agentSignature = agentOptions([]).map((agent) => agent.id).join('|');
            filters.setAgents(agentOptions([]));

            disposers.push(source.subscribe(paint));
            disposers.push(ctx.bus.subscribe('resync', () => BossModLogPlace.resync()));

            void reload().then(() => {
                if (params.diagnosticId) expandDeepLink(String(params.diagnosticId));
            });
        },

        /**
         * Re-read both feeds after an outage without remounting, so the search
         * text, the filters and the open row survive the gap (spec 1.4).
         * @returns {void}
         */
        resync() {
            if (!ctxRef) return;
            void reload();
        },

        /**
         * Drain both subscriptions, the debounce, and the cached detail nodes.
         * @returns {void}
         */
        unmount() {
            disposers.splice(0).forEach((off) => off());
            if (filters) filters.destroy();
            if (source) source.destroy();
            expanded = new Set();
            details = new Map();
            source = null;
            filters = null;
            detail = null;
            listEl = null;
            scrollEl = null;
            errorEl = null;
            summaryEl = null;
            moreBtn = null;
            ctxRef = null;
        },
    };
})();

BossModPlaces.register('log', BossModLogPlace);
