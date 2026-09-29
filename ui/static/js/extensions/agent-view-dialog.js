/**
 * BossMod AI — one agent's records, read-only (manifest `agent_view`).
 *
 * A `panel` modal from core/overlays.js. The extension names its lists
 * (`agent_view.views`, read from the extensions list); with more than one
 * the body leads with a BossModTabs row (e.g. Inbox | Sent) and each tab owns
 * one tabpanel and one BossModDataTable. A table is built the first time its
 * tab is shown — the grid measures its host, and a hidden panel measures
 * nothing — and kept for the life of the dialog.
 *
 * Each list's columns and caption come from the server with its first page,
 * so the first page is read before the table is built and handed to it as its
 * first answer; every later page (and a refresh) is read through the table.
 * Refresh reloads the tab that is up. Activating a row opens a LAYER over the
 * list (‹ back to it, ✕ closes both) with the record's facts and its body as
 * preformatted TEXT, never HTML.
 *
 * States: loading, failed (with retry) for the views and for each list's
 * first page, the table (which owns its own empty, loading and error states),
 * and the detail's loading/failed/loaded.
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
        noViews: 'This extension has no list to show.',
    });

    /**
     * Open the view.
     *
     * @param {object} options
     * @param {string} options.extensionId
     * @param {string} options.agentId
     * @param {string} options.title  The dialog title, e.g. "Iris — Open inbox".
     * @returns {{close: () => void}}
     * @throws {Error} When a required option is missing.
     */
    function open(options) {
        const { extensionId, agentId, title } = options || {};
        if (!extensionId) throw new Error('[agent-view-dialog] extensionId is required');
        if (!agentId) throw new Error('[agent-view-dialog] agentId is required');
        if (!title) throw new Error('[agent-view-dialog] title is required');

        const body = h('div', { class: 'ext-scroll agent-view' });
        let closed = false;
        /** One per view: {view, panel, table, started}. */
        let panes = [];
        /** The pane whose tab is up. */
        let current = null;

        const refresh = h('button', {
            class: 'header-icon-btn', type: 'button', 'aria-label': COPY.refresh, 'data-tooltip': COPY.refresh,
            onclick: () => {
                if (!current) void start();
                else if (current.table) current.table.reload();
                else void startPane(current);
            },
        }, h('i', { 'data-lucide': 'refresh-cw', 'aria-hidden': 'true' }));

        const modal = BossModOverlays.createModal({
            title,
            body,
            actions: [],
            size: 'panel',
            tools: [refresh],
            onClose: () => {
                closed = true;
                panes.forEach((pane) => { if (pane.table) pane.table.destroy(); });
                panes = [];
            },
        });
        BossModIcons.paint(modal.element, 'agent-view-dialog');

        function failedState(target, message, retry) {
            clear(target);
            target.append(
                h('p', { class: 'field-hint', role: 'alert' }, message),
                h('button', { class: 'btn btn-sm', type: 'button', onclick: retry }, COPY.retry));
        }

        /** Read which lists the extension offers, then lay out the tabs. */
        async function start() {
            clear(body);
            body.append(h('p', { class: 'field-hint', role: 'status' }, COPY.loading));
            let views;
            try {
                const items = await API.listExtensions();
                const item = items.find((entry) => entry.id === extensionId);
                views = item && item.agent_view ? item.agent_view.views : null;
            } catch (err) {
                if (!closed) failedState(body, String((err && err.message) || err), () => { void start(); });
                return;
            }
            if (closed) return;
            if (!Array.isArray(views) || !views.length) {
                clear(body);
                body.append(h('p', { class: 'field-hint', role: 'alert' }, COPY.noViews));
                return;
            }
            layout(views);
        }

        function layout(views) {
            clear(body);
            const tabbed = views.length > 1;
            panes = views.map((view) => ({
                view,
                table: null,
                started: false,
                panel: h('div', tabbed ? {
                    class: 'agent-view-panel', role: 'tabpanel', id: `agent-view-panel-${view.key}`,
                    'aria-labelledby': `agent-view-tab-${view.key}`,
                } : { class: 'agent-view-panel' }),
            }));
            if (tabbed) {
                const tabs = BossModTabs.create({
                    label: title,
                    idPrefix: 'agent-view-tab',
                    tabs: views.map((view) => ({ id: view.key, label: view.label, panelId: `agent-view-panel-${view.key}` })),
                    selected: views[0].key,
                    onSelect: show,
                });
                body.append(tabs.element);
            }
            panes.forEach((pane) => body.append(pane.panel));
            show(views[0].key);
        }

        function show(key) {
            panes.forEach((pane) => { pane.panel.hidden = pane.view.key !== key; });
            current = panes.find((pane) => pane.view.key === key);
            if (!current.started) void startPane(current);
        }

        /** Read one list's first page, then build its table (first page served from hand). */
        async function startPane(pane) {
            pane.started = true;
            const { panel, view } = pane;
            clear(panel);
            panel.append(h('p', { class: 'field-hint', role: 'status' }, COPY.loading));
            const fetchPage = (skip, top) => API.agentView(extensionId, agentId, view.key, skip, top);
            let first;
            try {
                first = await fetchPage(0, PAGE_SIZE);
            } catch (err) {
                if (!closed) failedState(panel, String((err && err.message) || err), () => { void startPane(pane); });
                return;
            }
            if (closed) return;
            clear(panel);
            let prefetched = first;
            /** The first column's key: its cell names a row. */
            const firstKey = first.columns[0] && first.columns[0].key;
            pane.table = BossModDataTable.create({
                caption: first.caption,
                columns: first.columns,
                pageSize: PAGE_SIZE,
                emptyText: COPY.empty,
                rowLabel: (row) => `${COPY.openItem} ${String((row.cells || {})[firstKey] || '')}`,
                onActivate: (row) => openItem(view.key, row, firstKey),
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
            panel.append(pane.table.element);
        }

        function openItem(viewKey, row, firstKey) {
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
                    item = await API.agentViewItem(extensionId, agentId, viewKey, row.id);
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
