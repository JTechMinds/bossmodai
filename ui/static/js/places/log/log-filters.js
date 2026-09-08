/**
 * BossMod AI — the Log's filter bar.
 *
 * Agent, type, search, and Follow. Built ONCE and never replaced, so the caret
 * survives every row that lands while the operator is typing — the property the
 * dock-era feed lost by rebuilding its filter markup on every fetch.
 *
 * It owns the filter state and reports every change through one `onChange`; the
 * place never reads a control's value out of the DOM.
 */
const BossModLogFilters = (() => {
    const { h, clear } = BossModDom;

    const SEARCH_DELAY_MS = 300;
    const TYPE_LABELS = Object.freeze({
        agent: 'Agents', task: 'Tasks', error: 'Errors', system: 'System',
    });

    /**
     * Build the bar.
     *
     * @param {object} deps
     * @param {string} [deps.agentId]  From placeParams: a Metrics token bar and
     *   an error need both deep-link into a Log filtered to one agent.
     * @param {() => void} deps.onChange
     * @param {(on: boolean) => void} deps.onFollow
     * @param {boolean} [deps.following=true]
     * @returns {{element: HTMLElement, filters: () => object,
     *            following: () => boolean, setAgents: (list: object[]) => void,
     *            destroy: () => void}}
     * @throws {Error} When a callback is missing.
     */
    function createFilters(deps) {
        const { agentId, onChange, onFollow, following = true } = deps || {};
        if (typeof onChange !== 'function') throw new Error('[log-filters] deps.onChange is required');
        if (typeof onFollow !== 'function') throw new Error('[log-filters] deps.onFollow is required');

        // The two feeds identify agents differently — diagnostics by id, the
        // activity feed by name alone — so the selection carries both.
        let chosen = { id: agentId || '', name: '' };
        let follow = following !== false;
        let searchTimer = null;
        let known = [];

        const agents = h('select', {
            class: 'board-select', 'aria-label': 'Filter by agent',
            onchange: (event) => {
                const id = event.target.value;
                const match = known.find((agent) => agent.id === id);
                chosen = { id, name: match ? match.name : '' };
                onChange();
            },
        });

        const types = h('select', {
            class: 'board-select', 'aria-label': 'Filter by type',
            onchange: onChange,
        },
            h('option', { value: '' }, 'All types'),
            ...BossModLogShape.TYPES.map((type) =>
                h('option', { value: type }, TYPE_LABELS[type])));

        const search = h('input', {
            type: 'search', class: 'board-search', placeholder: 'Search the log',
            'aria-label': 'Search the log',
            oninput: () => {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(onChange, SEARCH_DELAY_MS);
            },
        });

        // Follow is a switch, not a button that rewrites its own label: on/off
        // is state, and the switch role announces it as state rather than as a
        // pressed button. The label stays "Follow" in both positions —
        // "Following" as a label meant the control's accessible name changed
        // every time its value did.
        const followSwitch = BossModSwitch.create({
            label: 'Follow',
            pressed: follow,
            onChange: (on) => {
                follow = on;
                onFollow(follow);
            },
        });

        const element = h('div', { class: 'board-controls' },
            agents, types, search, followSwitch.element);

        return {
            element,

            /**
             * @returns {{agentId: string, agentName: string, type: string,
             *            search: string}}
             */
            filters() {
                return {
                    agentId: chosen.id,
                    agentName: chosen.name,
                    type: types.value,
                    search: search.value,
                };
            },

            /** @returns {boolean} */
            following() {
                return follow;
            },

            /**
             * Repopulate the agent options, keeping the current choice — which
             * may be a deep link to someone who has not appeared in the rows
             * yet, so the id is honoured whether or not it is in the list.
             *
             * @param {Array<{id: string, name: string}>} list
             * @returns {void}
             */
            setAgents(list) {
                known = list;
                clear(agents);
                agents.append(h('option', { value: '' }, 'All agents'));
                list.forEach((agent) => {
                    agents.append(h('option', { value: agent.id }, agent.name));
                });
                agents.value = chosen.id;
                const match = list.find((agent) => agent.id === chosen.id);
                if (match) chosen = { id: match.id, name: match.name };
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

    return { createFilters, TYPE_LABELS };
})();
