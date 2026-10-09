/**
 * BossMod AI — the Tasks place's `⋯`: the Done window, the sort, Archive and
 * Refresh.
 *
 * Built the way shell/thread-view-menu.js is, and for its reasons. It owns the
 * `⋯`, the panel behind it and the controls inside that; it owns no STATE. The
 * window and the sort belong to the place that paints with them, so this asks
 * (`getState`) and reports (`onWindow`, `onToggleSort`) and never keeps a copy
 * — two copies of one setting is how a control and the list under it end up
 * disagreeing.
 *
 * The window is a labelled segment, not a select: three short options, the
 * filled one is where you are, and the answer is a shape rather than a word to
 * read. The sort is one row that says the order it is in and flips it; the
 * dropped keys (title, assignee, subtasks) answered questions search and the
 * assignee filter already answer.
 */
const BossModTasksMenu = (() => {
    const { h } = BossModDom;
    const DATA = BossModTasksData;

    /** The `⋯`'s accessible name and its tooltip: one string, never two. */
    const MENU_LABEL = 'Task list options';
    /** The segment's caption, and the id the group is labelled by. */
    const WINDOW_LABEL = 'Done window';
    const WINDOW_LABEL_ID = 'tasks-window-label';

    const SORT_LABELS = Object.freeze({ desc: 'Sort: newest first', asc: 'Sort: oldest first' });

    /**
     * Build the options menu.
     *
     * @param {object} deps
     * @param {() => HTMLElement} deps.getContainer  What the panel hangs off —
     *   the place header. A thunk, because the header is built after the
     *   toolbar that holds this `⋯`.
     * @param {() => {windowDays: number, sortDirection: 'desc'|'asc',
     *   olderCount: number}} deps.getState  Read on open and after every pick.
     * @param {(days: number) => void} deps.onWindow  A window was picked.
     * @param {() => void} deps.onToggleSort  Flip newest-first / oldest-first.
     * @param {() => void} deps.onOpenArchive
     * @param {() => void} deps.onRefresh  The manual refetch. The list
     *   refreshes itself on task events and after an outage, but an operator
     *   who wants to be certain still needs a way to ask.
     * @returns {{button: HTMLElement, close: () => void, destroy: () => void}}
     * @throws {Error} When any dependency is missing — a `⋯` whose panel has
     *   nowhere to hang or no one to report to would render and do nothing.
     */
    function create(deps) {
        const {
            getContainer, getState, onWindow, onToggleSort, onOpenArchive, onRefresh,
        } = deps || {};
        const required = { getContainer, getState, onWindow, onToggleSort, onOpenArchive, onRefresh };
        Object.entries(required).forEach(([name, fn]) => {
            if (typeof fn !== 'function') throw new Error(`[tasks-menu] deps.${name} is required`);
        });

        // Built once, because they live inside the panel while it is open and
        // rebuilding them per open would swap a node under the pointer that is
        // already on it. Picking a window or flipping the sort does NOT close
        // the panel: the filled pill moving, or the row relabelling, is the
        // confirmation, and a menu that vanished as it answered would take the
        // answer with it.
        const windows = DATA.DONE_WINDOWS.map((entry) => h('button', {
            class: 'menu-segment-option',
            id: `tasks-window-${entry.days}`,
            type: 'button',
            onclick: () => { onWindow(entry.days); sync(); },
        }, entry.label));

        // The shared titled section (core/menu.js) is the labelled group, so
        // the segment inside carries no role of its own.
        const group = BossModMenu.createMenuSection({ id: WINDOW_LABEL_ID, label: WINDOW_LABEL,
            children: [h('div', { class: 'menu-segment' }, windows)] });

        const sortRow = h('button', {
            class: 'menu-action', id: 'tasks-sort', type: 'button',
            onclick: () => { onToggleSort(); sync(); },
        });
        const olderCount = h('span', { class: 'menu-action-count' });
        const actions = h('div', { class: 'menu-actions' },
            sortRow,
            // Archive and Refresh take the operator somewhere else, so the
            // panel goes first and focus is not left inside a closed menu.
            h('button', {
                class: 'menu-action', id: 'tasks-archive', type: 'button',
                onclick: () => { menu.close(); onOpenArchive(); },
            }, 'Archive', olderCount),
            h('button', {
                class: 'menu-action', id: 'tasks-refresh', type: 'button',
                onclick: () => { menu.close(); onRefresh(); },
            }, 'Refresh'));

        // The `⋯` and its panel's lifecycle are core/menu-button.js's. The
        // controls are written from the place's state each time it opens.
        const menu = BossModMenuButton.create({
            id: 'tasks-options',
            label: MENU_LABEL,
            size: 'header',
            menuName: 'tasks',
            getContainer,
            getItems: () => [group, actions],
            onOpen: () => sync(),
        });

        /**
         * Write every control FROM the place's state: the filled window, the
         * sort row's words, and the Archive count.
         * @returns {void}
         */
        function sync() {
            const state = getState();
            windows.forEach((option, index) => {
                option.setAttribute('aria-pressed',
                    String(DATA.DONE_WINDOWS[index].days === state.windowDays));
            });
            const label = SORT_LABELS[state.sortDirection];
            if (!label) throw new Error(`[tasks-menu] unknown sort direction "${state.sortDirection}"`);
            sortRow.textContent = label;
            olderCount.textContent = String(state.olderCount);
        }

        return { button: menu.button, close: menu.close, destroy: menu.destroy };
    }

    return { create };
})();
