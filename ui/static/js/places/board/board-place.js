/**
 * BossMod AI — the Board place.
 *
 * Four columns over GET /api/tasks. Everything the dock-era table could do has
 * a home here: sorting became a board-level control applying within each
 * column, multi-select became a checkbox on each card, and bulk cancel became a
 * header action confirmed through a dialog.
 *
 * The chrome is built once and never replaced. A refresh repaints the columns
 * only, so the search box, the caret inside it, and the current selection all
 * survive every task event that lands while the operator is typing.
 */
const BossModBoardPlace = (() => {
    const { h, clear } = BossModDom;
    const DATA = BossModBoardData;
    const COLUMNS = BossModBoardColumns;

    /** Debounces a burst of task events into one refetch. */
    const REFRESH_DELAY_MS = 500;

    let ctxRef = null;
    let toolbar = null;
    let canceller = null;
    let tasks = [];
    let subCounts = new Map();
    let selected = new Set();
    let summaryLine = null;
    let boardEl = null;
    let detail = null;
    let refreshTimer = null;
    let load = null;
    const disposers = [];

    // ─── Painting ───

    function setBody(...nodes) {
        clear(boardEl);
        boardEl.append(...nodes);
    }

    function paintError(message) {
        setBody(h('div', { class: 'place-error-panel', role: 'alert' },
            h('p', { class: 'place-error-title' }, 'Could not load the board'),
            h('p', { class: 'place-error-detail' }, message),
            h('button', { class: 'btn', type: 'button', onclick: () => { void refresh(); } },
                'Try again')));
    }

    function paintEmpty() {
        setBody(h('div', { class: 'place-empty' },
            h('p', { class: 'place-empty-title' }, 'Nothing is on the board'),
            h('p', { class: 'place-empty-hint' },
                'Assign the first task and it will appear in Backlog.'),
            h('button', { class: 'btn', type: 'button', onclick: openAssign }, '+ New task')));
    }

    function card(task) {
        // undefined for a top-level task, which is exactly "no parent title".
        const parent = tasks.find((item) => item.id === task.parent_task_id);
        return BossModTaskCard.renderCard(task, {
            onOpen: openDetail,
            onToggleSelect: toggleSelect,
            selected: selected.has(task.id),
            selectable: !COLUMNS.isTerminal(task.status),
            subtaskCount: subCounts.get(task.id) || 0,
            parentTitle: parent ? parent.title : '',
        });
    }

    function paintColumns() {
        const visible = DATA.filterTasks(tasks, toolbar.filters());
        const order = toolbar.sort();
        const ordered = DATA.sortTasks(visible, order.key, order.direction, subCounts);
        const grouped = DATA.groupIntoColumns(ordered);
        const totals = DATA.counts(visible);

        summaryLine.textContent = `${totals.open} active · ${totals.done} done · `
            + `${totals.closed} closed without completing · ${totals.total} total`;

        if (tasks.length === 0) {
            paintEmpty();
            return;
        }
        setBody(BossModBoardGrid.renderGrid(grouped, card));

        // Always empty while the totality test passes; surfaced, never dropped.
        if (grouped.unplaced.length > 0) {
            const unknown = grouped.unplaced.map((task) => task.status).join(', ');
            boardEl.append(h('p', { class: 'place-error-detail', role: 'alert' },
                `${grouped.unplaced.length} task(s) carry an unknown status: ${unknown}`));
        }
    }

    // ─── Data ───

    /**
     * Refetch and repaint the columns.
     *
     * @param {boolean} [quiet=false] Skip the loading state, for a refresh the
     *   operator did not ask for.
     * @returns {Promise<void>} Never rejects: a failure becomes the error state.
     *   The dock-era table swallowed a failed silent refresh and left the
     *   operator looking at rows it could not confirm.
     */
    async function refresh(quiet) {
        const loadId = load.next();
        if (!quiet) setBody(BossModBoardGrid.renderSkeleton());
        try {
            const rows = await DATA.loadTasks(ctxRef.api);
            if (!load.isCurrent(loadId)) return;
            tasks = rows;
            subCounts = DATA.subtaskCounts(tasks);
            // A task that vanished or ended can no longer be cancelled; leaving
            // it selected would send ids the server must reject.
            for (const id of [...selected]) {
                const task = tasks.find((item) => item.id === id);
                if (!task || COLUMNS.isTerminal(task.status)) selected.delete(id);
            }
            toolbar.setAgents(DATA.uniqueAgents(tasks));
            toolbar.setSelectedCount(selected.size);
            paintColumns();
        } catch (err) {
            if (!load.isCurrent(loadId)) return;
            console.error('[board] could not load tasks', err);
            paintError((err && err.message) || 'The request failed.');
        }
    }

    // ─── Actions ───

    /** The card paints itself; the place only records what is selected. */
    function toggleSelect(taskId, on) {
        if (on) selected.add(taskId);
        else selected.delete(taskId);
        toolbar.setSelectedCount(selected.size);
    }

    function closeDetail() {
        if (detail) detail.close();
        detail = null;
    }

    /** Open one task beside the board; opening another replaces it. */
    function openDetail(taskId) {
        closeDetail();
        detail = BossModTaskDetail.openTaskDetail({
            api: ctxRef.api,
            taskId,
            tasks,
            onNavigate: openDetail,
            onCancel: (task) => canceller.cancelOne(task),
            onClose: () => { detail = null; },
        });
    }

    function openAssign() {
        BossModAssignForm.openAssignForm({
            api: ctxRef.api,
            store: ctxRef.store,
            onCreated: () => { void refresh(true); },
        });
    }

    return {
        label: 'Board',
        icon: 'list-todo',

        /**
         * @param {HTMLElement} el
         * @param {object} ctx  `{ store, bus, api, needs, navigate }`.
         * @returns {void}
         */
        mount(el, ctx) {
            ctxRef = ctx;
            load = BossModGates.createLoadGeneration();
            tasks = [];
            selected = new Set();
            subCounts = new Map();
            toolbar = BossModBoardToolbar.createToolbar({
                sortKeys: DATA.SORT_KEYS,
                // The Desk's "See all" arrives as a place param, not as a click.
                agentId: ctx.store.getState().placeParams.agentFilter || null,
                onChange: paintColumns,
                onNewTask: openAssign,
                onRefresh: () => { void refresh(); },
                onCancelSelected: () => canceller.cancelMany(
                    [...selected].map((id) => tasks.find((task) => task.id === id))),
            });

            canceller = BossModBoardCancel.createCanceller({
                api: ctx.api,
                onCancelled: (ids) => {
                    ids.forEach((id) => selected.delete(id));
                    void refresh(true);
                },
                onError: paintError,
            });

            clear(el);
            summaryLine = h('p', { class: 'board-summary' }, '');
            boardEl = h('div', { class: 'board-body' });
            el.append(h('div', { class: 'board-place' },
                h('header', { class: 'board-header' },
                    h('div', { class: 'board-title' },
                        h('h1', { tabindex: '-1' }, 'Board'), summaryLine),
                    toolbar.element),
                boardEl));

            disposers.push(ctx.bus.subscribe('activity', (entry) => {
                if (!entry) return;
                if (DATA.TASK_ACTIVITY_EVENTS.indexOf(String(entry.event || '')) === -1) return;
                clearTimeout(refreshTimer);
                refreshTimer = setTimeout(() => { void refresh(true); }, REFRESH_DELAY_MS);
            }));

            // The shell does not drive Place.resync() yet; subscribing here is
            // what keeps the board from sitting stale after an outage.
            disposers.push(ctx.bus.subscribe('resync', () => BossModBoardPlace.resync()));

            void refresh();
        },

        /**
         * Re-fetch after a WebSocket outage without remounting (spec 1.4), so
         * the search text and the selection survive the gap. Reached both from
         * the `resync` topic and, when the shell grows the call, from the place
         * contract.
         * @returns {void}
         */
        resync() {
            // Public, so it can arrive after unmount; there is no ctx to fetch
            // with then, and no board left to paint.
            if (!ctxRef) return;
            void refresh(true);
        },

        /**
         * Drain every subscription, timer, and overlay.
         * @returns {void}
         */
        unmount() {
            disposers.splice(0).forEach((off) => off());
            clearTimeout(refreshTimer);
            if (load) load.next();
            if (toolbar) toolbar.destroy();
            closeDetail();
            toolbar = null;
            canceller = null;
            tasks = [];
            selected = new Set();
            subCounts = new Map();
            summaryLine = null;
            boardEl = null;
            ctxRef = null;
        },
    };
})();

BossModPlaces.register('board', BossModBoardPlace);
