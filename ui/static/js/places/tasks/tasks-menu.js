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

        const group = h('div', { class: 'menu-group' },
            h('p', { class: 'menu-label', id: WINDOW_LABEL_ID }, WINDOW_LABEL),
            h('div', {
                class: 'menu-segment', role: 'group', 'aria-labelledby': WINDOW_LABEL_ID,
            }, windows));

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
                onclick: () => { close(); onOpenArchive(); },
            }, 'Archive', olderCount),
            h('button', {
                class: 'menu-action', id: 'tasks-refresh', type: 'button',
                onclick: () => { close(); onRefresh(); },
            }, 'Refresh'));

        const button = h('button', {
            class: 'header-icon-btn',
            id: 'tasks-options',
            type: 'button',
            'aria-label': MENU_LABEL,
            'data-tooltip': MENU_LABEL,
            // dialog, not menu: core/overlays.js's panel is a role="dialog" and
            // its children are ordinary buttons rather than menuitems.
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggle(),
        }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }));

        /** The open panel, or null. One at a time, and the `⋯` toggles it. */
        let menu = null;

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

        /** @returns {void} */
        function close() {
            if (!menu) return;
            const open = menu;
            menu = null;
            open.close();
        }

        /**
         * Show the options, or put them away again. The panel is
         * core/overlays.js's, which owns the focus trap, Esc, the press-outside
         * dismiss and returning focus to the `⋯`.
         * @returns {void}
         */
        function toggle() {
            if (menu) {
                close();
                return;
            }
            sync();
            menu = BossModOverlays.createMenu({
                anchor: button,
                label: MENU_LABEL,
                items: [group, actions],
                container: getContainer(),
                onClose: () => {
                    menu = null;
                    button.setAttribute('aria-expanded', 'false');
                },
            });
            menu.element.setAttribute('data-menu', 'tasks');
            // A menu panel is detached while closed, so a sweep of the page can
            // never reach inside one; it is painted here, as it opens.
            BossModIcons.paint(menu.element, 'tasks-menu');
            button.setAttribute('aria-expanded', 'true');
        }

        return {
            button,
            close,

            /**
             * Put the panel away. A panel left open would outlive the place it
             * hangs off, and its press-outside listener would outlive both.
             * @returns {void}
             */
            destroy() {
                close();
            },
        };
    }

    return { create };
})();
