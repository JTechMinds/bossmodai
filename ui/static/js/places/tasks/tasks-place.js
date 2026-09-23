/**
 * BossMod AI — the Tasks place.
 *
 * Four columns over GET /api/tasks; finished work inside the Done window is
 * Done, older is Archive. This file owns all the state the views read, and
 * the views take it through callbacks. The chrome is built once and never
 * replaced: a refresh repaints the columns only, so the search box, its caret
 * and the selection survive every task event that lands mid-typing.
 */
const BossModTasksPlace = (() => {
    const { h, clear } = BossModDom;
    const DATA = BossModTasksData;
    const COLUMNS = BossModTasksColumns;

    /** Debounces a burst of task events into one refetch. */
    const REFRESH_DELAY_MS = 500;

    let ctxRef = null;
    let toolbar = null;
    let menu = null;
    let canceller = null;
    let tasks = [];
    let progress = new Map();
    let selected = new Set();
    // Toolbar state, like the filters: reset on every mount, never persisted.
    let windowDays = DATA.DEFAULT_WINDOW_DAYS;
    let sortDirection = 'desc';
    /** The finished tasks the last paint put past the window: Archive's list. */
    let lastOlder = [];
    let summaryLine = null;
    let headerEl = null;
    let bodyEl = null;
    /** The open task layers, base first. */
    let details = [];
    /** The open Archive, or null. */
    let archive = null;
    let refreshTimer = null;
    let load = null;
    const disposers = [];

    // ─── Painting ───

    function setBody(...nodes) {
        clear(bodyEl);
        bodyEl.append(...nodes);
    }

    function paintError(message) {
        setBody(h('div', { class: 'place-error-panel', role: 'alert' },
            h('p', { class: 'place-error-title' }, 'Could not load tasks'),
            h('p', { class: 'place-error-detail' }, message),
            h('button', { class: 'btn', type: 'button', onclick: () => { void refresh(); } },
                'Try again')));
    }

    function paintEmpty() {
        setBody(h('div', { class: 'place-empty' },
            h('p', { class: 'place-empty-title' }, 'No tasks yet'),
            h('p', { class: 'place-empty-hint' },
                'Assign the first task and it will appear in Backlog.'),
            h('button', { class: 'btn', type: 'button', onclick: openAssign }, '+ New task')));
    }

    /** @returns {object|null} The roster's entry for an agent id, or null. */
    function rosterAgent(agentId) {
        return ctxRef.store.getState().roster.find((entry) => entry.id === agentId) || null;
    }

    /** The roster colour; undefined (not rostered) is the avatar's neutral treatment. */
    function colorOf(agentId) {
        const agent = rosterAgent(agentId);
        return agent ? agent.color : undefined;
    }

    function renderCard(task, onOpen) {
        // undefined for a top-level task, which is exactly "no parent title".
        const parent = tasks.find((item) => item.id === task.parent_task_id);
        return BossModTaskCard.renderCard(task, {
            onOpen,
            onToggleSelect: toggleSelect,
            onOpenChat: openChat,
            selected: selected.has(task.id),
            selectable: !COLUMNS.isTerminal(task.status),
            progress: progress.get(task.id) || null,
            parentTitle: parent ? parent.title : '',
            colorOf,
        });
    }

    /** A card on the page opens its task as a new errand. */
    const card = (task) => renderCard(task, openDetail);
    /** A card in Archive opens its task as a layer, so ‹ returns to Archive. */
    const archiveCard = (task) => renderCard(task, pushDetail);

    function paint() {
        const visible = BossModFloorScope.filterTasks(ctxRef.store.getState(), DATA.filterTasks(tasks, toolbar.filters()));
        const ordered = DATA.sortTasks(visible, sortDirection);
        const grouped = DATA.groupIntoColumns(ordered, { windowDays, now: Date.now() });
        const totals = DATA.counts(grouped);
        const windowPhrase = DATA.windowFor(windowDays).phrase;

        summaryLine.textContent = `${totals.open} active · ${totals.done} done ${windowPhrase}`;
        lastOlder = grouped.older;
        // Archive shows the same filters and the same moment as the page.
        if (archive) archive.update(grouped.older);

        if (tasks.length === 0) {
            paintEmpty();
            return;
        }
        setBody(BossModTasksGrid.renderGrid(grouped, {
            renderCard: card,
            windowPhrase,
            olderCount: grouped.older.length,
            onOpenArchive: openArchive,
        }));

        // Always empty while the totality test passes; surfaced, never dropped.
        if (grouped.unplaced.length > 0) {
            const unknown = grouped.unplaced.map((task) => task.status).join(', ');
            bodyEl.append(h('p', { class: 'place-error-detail', role: 'alert' },
                `${grouped.unplaced.length} task(s) carry an unknown status: ${unknown}`));
        }
        // Empty while every finish is stamped; one with no stamp is reported
        // rather than filed under a day nobody recorded.
        if (grouped.undated.length > 0) {
            bodyEl.append(h('p', { class: 'place-error-detail', role: 'alert' },
                `${grouped.undated.length} finished task(s) carry no finish time`));
        }
        BossModIcons.paint(bodyEl, 'tasks-place');
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
        if (!quiet) setBody(BossModTasksGrid.renderSkeleton());
        try {
            const rows = await DATA.loadTasks(ctxRef.api);
            if (!load.isCurrent(loadId)) return;
            tasks = rows;
            progress = DATA.subtaskProgress(tasks);
            // A task that vanished or ended can no longer be cancelled; leaving
            // it selected would send ids the server must reject.
            for (const id of [...selected]) {
                const task = tasks.find((item) => item.id === id);
                if (!task || COLUMNS.isTerminal(task.status)) selected.delete(id);
            }
            toolbar.setAgents(DATA.uniqueAgents(tasks));
            toolbar.setSelectedCount(selected.size);
            paint();
        } catch (err) {
            if (!load.isCurrent(loadId)) return;
            console.error('[tasks] could not load tasks', err);
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

    /** Close every open task layer, top first. */
    function closeDetails() {
        details.slice().reverse().forEach((handle) => handle.close());
        details = [];
    }

    /** Put Archive away, if it is open. */
    function closeArchive() {
        if (!archive) return;
        const open = archive;
        archive = null;
        open.close();
    }

    /**
     * Open one task as a layer over whatever is on screen.
     *
     * @param {string} taskId
     * @returns {void}
     */
    function showDetail(taskId) {
        const handle = BossModTaskDetail.openTaskDetail({
            api: ctxRef.api,
            taskId,
            tasks,
            colorOf,
            onNavigate: pushDetail,
            onCancel: (task) => canceller.cancelOne(task),
            onOpenChat: openChat,
            onClose: () => { details = details.filter((item) => item !== handle); },
        });
        details.push(handle);
    }

    /** A card in the list: a new errand, so any open task layers go first. */
    function openDetail(taskId) {
        closeDetails();
        showDetail(taskId);
    }

    /** A link inside a task: one step deeper — ‹ walks back to the task it came from. */
    function pushDetail(taskId) {
        showDetail(taskId);
    }

    /** Archive, fresh: the page's own layers go first, as for any new errand. */
    function openArchive() {
        closeDetails();
        closeArchive();
        archive = BossModTasksArchive.open({
            tasks: lastOlder,
            renderCard: archiveCard,
            onClose: () => { archive = null; },
        });
    }

    /**
     * Leave for the task's conversation, closing every overlay first so none
     * is left sitting over the chat.
     * @param {object} task
     * @returns {void}
     * @throws {Error} When the task has nowhere to chat — the card offers the
     *   button only when it has, so reaching here without one is a bug.
     */
    function openChat(task) {
        const target = DATA.chatTargetFor(task);
        if (!target) throw new Error(`[tasks] no chat to open for task "${task.id}"`);
        closeDetails();
        closeArchive();
        BossModAgentRoutes.openConversation(
            { store: ctxRef.store, navigate: ctxRef.navigate }, target.id, target.kind);
    }

    /**
     * Same open path blocked needs use: `navigate('tasks', { taskId })`.
     * A missing id says so rather than opening an empty task dialog.
     */
    function openLinkedDetail(taskId) {
        const id = String(taskId || '').trim();
        if (!id) return;
        if (!tasks.some((item) => item && item.id === id)) {
            bodyEl.append(h('p', { class: 'place-error-detail', role: 'alert' },
                'That task is not in the list.'));
            return;
        }
        openDetail(id);
    }

    function openAssign() {
        BossModAssignForm.openAssignForm({
            api: ctxRef.api,
            store: ctxRef.store,
            onCreated: () => { void refresh(true); },
        });
    }

    return {
        label: 'Tasks',
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
            progress = new Map();
            windowDays = DATA.DEFAULT_WINDOW_DAYS;
            sortDirection = 'desc';
            lastOlder = [];

            menu = BossModTasksMenu.create({
                getContainer: () => headerEl,
                getState: () => ({ windowDays, sortDirection, olderCount: lastOlder.length }),
                onWindow: (days) => { windowDays = days; paint(); },
                onToggleSort: () => {
                    sortDirection = sortDirection === 'desc' ? 'asc' : 'desc';
                    paint();
                },
                onOpenArchive: openArchive,
                onRefresh: () => { void refresh(); },
            });
            toolbar = BossModTasksToolbar.createToolbar({
                // The Desk's "See all" arrives as a place param, not as a click.
                agentId: ctx.store.getState().placeParams.agentFilter || null,
                menuButton: menu.button,
                rosterAgent,
                onChange: paint,
                onNewTask: openAssign,
                onCancelSelected: () => canceller.cancelMany(
                    [...selected].map((id) => tasks.find((task) => task.id === id))),
            });

            canceller = BossModTasksCancel.createCanceller({
                api: ctx.api,
                onCancelled: (ids) => {
                    ids.forEach((id) => selected.delete(id));
                    void refresh(true);
                },
                onError: paintError,
            });

            clear(el);
            summaryLine = h('p', { class: 'place-summary' }, '');
            bodyEl = h('div', { class: 'tasks-body' });
            headerEl = h('header', { class: 'place-header tasks-header' },
                h('div', { class: 'place-title' },
                    h('h1', { tabindex: '-1' }, 'Tasks'), summaryLine),
                toolbar.element);
            el.append(h('div', { class: 'tasks-place' }, headerEl, bodyEl));
            // The header's `⋯` glyph; the body is painted on every repaint.
            BossModIcons.paint(headerEl, 'tasks-place');

            disposers.push(ctx.bus.subscribe('activity', (entry) => {
                if (!entry) return;
                if (DATA.TASK_ACTIVITY_EVENTS.indexOf(String(entry.event || '')) === -1) return;
                clearTimeout(refreshTimer);
                refreshTimer = setTimeout(() => { void refresh(true); }, REFRESH_DELAY_MS);
            }));

            // The shell does not drive Place.resync() yet; subscribing here is
            // what keeps the list from sitting stale after an outage.
            disposers.push(ctx.bus.subscribe('resync', () => BossModTasksPlace.resync()));
            disposers.push(ctx.store.subscribe((s) => `${s.floorScope}|${s.currentFloorId}|${s.browseFloorId}`, () => { if (ctxRef) paint(); }));

            const linkedTaskId = String(ctx.store.getState().placeParams.taskId || '').trim();
            void refresh().then(() => {
                if (!linkedTaskId || !ctxRef || tasks.length === 0) return;
                openLinkedDetail(linkedTaskId);
            });
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
            // with then, and no list left to paint.
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
            if (menu) menu.destroy();
            closeDetails();
            closeArchive();
            toolbar = null;
            menu = null;
            canceller = null;
            tasks = [];
            selected = new Set();
            progress = new Map();
            lastOlder = [];
            summaryLine = null;
            headerEl = null;
            bodyEl = null;
            ctxRef = null;
        },
    };
})();

BossModPlaces.register('tasks', BossModTasksPlace);
