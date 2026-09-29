/**
 * BossMod AI — one agent's records, read-only (manifest `agent_view`).
 *
 * A `panel` modal from core/overlays.js holding a BossModDataTable. The
 * columns and the caption come from the server with the first page, so the
 * first page is read before the table is built and handed to it as its first
 * answer; every later page (and a refresh) is read through the table.
 * Activating a row opens a LAYER over the list (‹ back to it, ✕ closes both)
 * with the record's facts and its body as preformatted TEXT, never HTML.
 *
 * States: loading, failed (with retry), the table (which owns its own empty,
 * loading and error states), and the detail's loading/failed/loaded.
 */
const BossModAgentViewDialog = (() => {
    const { h, clear } = BossModDom;
    const API = BossModExtensionsApi;
    /** Rows per page; the server allows 1–100. */
    const PAGE_SIZE = 25;

    const COPY = Object.freeze({
        loading: 'Loading…',
        retry: 'Try again',
        refresh: 'Refresh',
        empty: 'Nothing here yet.',
        openItem: 'Open',
        itemLoading: 'Loading…',
        noBody: '(empty)',
    });

    /**
     * Open the view.
     *
     * @param {object} options
     * @param {string} options.extensionId
     * @param {string} options.agentId
     * @param {string} options.title  The dialog title, e.g. "Iris — inbox".
     * @returns {{close: () => void}}
     * @throws {Error} When a required option is missing.
     */
    function open(options) {
        const { extensionId, agentId, title } = options || {};
        if (!extensionId) throw new Error('[agent-view-dialog] extensionId is required');
        if (!agentId) throw new Error('[agent-view-dialog] agentId is required');
        if (!title) throw new Error('[agent-view-dialog] title is required');

        const body = h('div', { class: 'ext-scroll agent-view' });
        let table = null;
        let closed = false;
        /** The first column's key: its cell names a row. */
        let firstKey = null;

        const refresh = h('button', {
            class: 'header-icon-btn', type: 'button', 'aria-label': COPY.refresh, 'data-tooltip': COPY.refresh,
            onclick: () => { if (table) table.reload(); else void start(); },
        }, h('i', { 'data-lucide': 'refresh-cw', 'aria-hidden': 'true' }));

        const modal = BossModOverlays.createModal({
            title,
            body,
            actions: [],
            size: 'panel',
            tools: [refresh],
            onClose: () => {
                closed = true;
                if (table) table.destroy();
                table = null;
            },
        });
        BossModIcons.paint(modal.element, 'agent-view-dialog');

        function fetchPage(skip, top) {
            return API.agentView(extensionId, agentId, skip, top);
        }

        async function start() {
            clear(body);
            body.append(h('p', { class: 'field-hint', role: 'status' }, COPY.loading));
            let first;
            try {
                first = await fetchPage(0, PAGE_SIZE);
            } catch (err) {
                if (closed) return;
                clear(body);
                body.append(
                    h('p', { class: 'field-hint', role: 'alert' }, String((err && err.message) || err)),
                    h('button', { class: 'btn btn-sm', type: 'button', id: 'agent-view-retry', onclick: () => { void start(); } }, COPY.retry));
                return;
            }
            if (closed) return;
            clear(body);
            // Served once, to the table's first request; anything after reads.
            let prefetched = first;
            firstKey = first.columns[0] && first.columns[0].key;
            table = BossModDataTable.create({
                caption: first.caption,
                columns: first.columns,
                pageSize: PAGE_SIZE,
                emptyText: COPY.empty,
                rowLabel: (row) => `${COPY.openItem} ${String((row.cells || {})[firstKey] || '')}`,
                onActivate: (row) => openItem(row),
                loadPage: async ({ skip, top }) => {
                    if (prefetched && skip === 0 && top === PAGE_SIZE) {
                        const page = prefetched;
                        prefetched = null;
                        return page;
                    }
                    prefetched = null;
                    return fetchPage(skip, top);
                },
            });
            body.append(table.element);
        }

        function openItem(row) {
            const content = h('div', { class: 'ext-scroll agent-view-item' },
                h('p', { class: 'field-hint', role: 'status' }, COPY.itemLoading));
            const layer = BossModOverlays.createModal({
                title: String((row.cells || {})[firstKey] || COPY.openItem),
                body: content,
                actions: [],
                size: 'panel',
            });
            void (async () => {
                let item;
                try {
                    item = await API.agentViewItem(extensionId, agentId, row.id);
                } catch (err) {
                    clear(content);
                    content.append(h('p', { class: 'field-hint', role: 'alert' }, String((err && err.message) || err)));
                    return;
                }
                clear(content);
                layer.setTitle(item.title);
                content.append(
                    BossModFactList.create(item.facts.map(([label, value]) => ({ label, value }))),
                    h('div', { class: 'agent-view-body' }, item.body_text || COPY.noBody));
            })();
        }

        void start();
        return { close: () => modal.close() };
    }

    return { open };
})();
