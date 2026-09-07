/**
 * BossMod AI — the Board's toolbar.
 *
 * Search, assignee filter, sort, the subtask toggle, and the two actions. It
 * owns the filter state and reports every change through one `onChange`
 * callback, so the place never reads a control's value out of the DOM and the
 * controls are never rebuilt on a repaint — which is what keeps the caret in
 * the search box while tasks stream in.
 */
const BossModBoardToolbar = (() => {
    const { h, clear } = BossModDom;

    const SEARCH_DELAY_MS = 200;

    /**
     * Build the toolbar.
     *
     * @param {object} deps
     * @param {Array<{key: string, label: string}>} deps.sortKeys
     * @param {string|null} [deps.agentId]  Preset by the Desk's "See all",
     *   which arrives as a place param rather than as a click.
     * @param {() => void} deps.onChange  A filter or sort moved; repaint.
     * @param {() => void} deps.onNewTask
     * @param {() => void} deps.onRefresh  The manual refetch the dock-era table
     *   had. The board refreshes itself on task events and after an outage, but
     *   an operator who wants to be certain still needs a way to ask.
     * @param {() => void} deps.onCancelSelected
     * @returns {{element: HTMLElement, filters: () => object, sort: () => object,
     *   setAgents: (agents: object[]) => void, setSelectedCount: (n: number) => void,
     *   destroy: () => void}}
     * @throws {Error} When a callback is missing.
     */
    function createToolbar(deps) {
        const {
            sortKeys, agentId = null, onChange, onNewTask, onRefresh, onCancelSelected,
        } = deps || {};
        if (!Array.isArray(sortKeys)) throw new Error('[board-toolbar] deps.sortKeys is required');
        if (typeof onChange !== 'function') throw new Error('[board-toolbar] deps.onChange is required');
        if (typeof onNewTask !== 'function') throw new Error('[board-toolbar] deps.onNewTask is required');
        if (typeof onRefresh !== 'function') throw new Error('[board-toolbar] deps.onRefresh is required');
        if (typeof onCancelSelected !== 'function') {
            throw new Error('[board-toolbar] deps.onCancelSelected is required');
        }

        let currentAgent = agentId;
        let showChildren = false;
        let sortKey = sortKeys[0].key;
        let sortDir = 'desc';
        let searchTimer = null;

        const search = h('input', {
            type: 'search', class: 'board-search', placeholder: 'Search tasks',
            'aria-label': 'Search tasks by title',
            oninput: () => {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(onChange, SEARCH_DELAY_MS);
            },
        });

        const clearFilter = h('button', {
            class: 'board-chip-clear', type: 'button', hidden: !currentAgent,
            onclick: () => {
                currentAgent = null;
                agents.value = '';
                clearFilter.hidden = true;
                onChange();
            },
        }, 'Clear filter');

        const agents = h('select', {
            class: 'board-select', 'aria-label': 'Filter by assignee',
            onchange: (event) => {
                currentAgent = event.target.value || null;
                clearFilter.hidden = !currentAgent;
                onChange();
            },
        });

        const sort = h('select', {
            class: 'board-select', 'aria-label': 'Sort tasks within each column',
            onchange: (event) => { sortKey = event.target.value; onChange(); },
        }, ...sortKeys.map((entry) =>
            h('option', { value: entry.key, selected: entry.key === sortKey }, entry.label)));

        const direction = h('button', {
            class: 'board-dir', type: 'button', 'aria-label': 'Reverse the sort order',
            onclick: () => {
                sortDir = sortDir === 'asc' ? 'desc' : 'asc';
                direction.textContent = sortDir === 'asc' ? 'Oldest first' : 'Newest first';
                onChange();
            },
        }, 'Newest first');

        const children = h('label', { class: 'board-toggle' },
            h('input', {
                type: 'checkbox',
                onchange: (event) => { showChildren = event.target.checked; onChange(); },
            }), 'Show subtasks');

        const cancel = h('button', {
            class: 'board-danger', type: 'button', disabled: true, onclick: onCancelSelected,
        }, 'Cancel selected');

        function labelled(text, node) {
            return h('label', { class: 'board-control' },
                h('span', { class: 'board-control-label' }, text), node);
        }

        const element = h('div', { class: 'board-controls' },
            search,
            labelled('Assignee', agents), clearFilter,
            labelled('Sort', sort), direction,
            children,
            h('button', {
                class: 'board-dir', type: 'button', 'aria-label': 'Refresh the board',
                onclick: onRefresh,
            }, 'Refresh'),
            h('button', { class: 'btn', type: 'button', onclick: onNewTask }, '+ New task'),
            cancel);

        return {
            element,

            /**
             * @returns {{agentId: string|null, query: string, showChildren: boolean}}
             */
            filters() {
                return { agentId: currentAgent, query: search.value, showChildren };
            },

            /** @returns {{key: string, direction: string}} */
            sort() {
                return { key: sortKey, direction: sortDir };
            },

            /**
             * Repopulate the assignee options, keeping the current choice.
             * @param {Array<{id: string, name: string}>} list
             * @returns {void}
             */
            setAgents(list) {
                clear(agents);
                agents.append(h('option', { value: '' }, 'All assignees'));
                list.forEach((agent) => agents.append(h('option', { value: agent.id }, agent.name)));
                agents.value = currentAgent || '';
                clearFilter.hidden = !currentAgent;
            },

            /**
             * @param {number} count  Zero disables the action; a disabled
             *   destructive button is better than one that does nothing.
             * @returns {void}
             */
            setSelectedCount(count) {
                cancel.disabled = count === 0;
                cancel.textContent = count ? `Cancel selected (${count})` : 'Cancel selected';
            },

            /**
             * Cancel the pending search debounce.
             * @returns {void}
             */
            destroy() {
                clearTimeout(searchTimer);
            },
        };
    }

    return { createToolbar };
})();
