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
    const { h } = BossModDom;

    const SEARCH_DELAY_MS = 300;
    const TYPE_LABELS = Object.freeze({
        agent: 'Agents', task: 'Tasks', error: 'Errors', system: 'System',
    });

    /** Each dropdown's first option: no filter at all. */
    const ALL_AGENTS = Object.freeze({ value: '', label: 'All agents' });
    const ALL_TYPES = Object.freeze({ value: '', label: 'All types' });

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

        // Starts on "All agents" even for a deep link: the agent's name is not
        // known until setAgents(), which the place calls before its first read
        // and which then shows the choice `chosen` already holds.
        const agents = BossModMenuSelect.create({
            label: 'Filter by agent',
            options: [ALL_AGENTS],
            onChange: (id) => {
                const match = known.find((agent) => agent.id === id);
                chosen = { id, name: match ? match.name : '' };
                onChange();
            },
        });

        const types = BossModMenuSelect.create({
            label: 'Filter by type',
            options: [ALL_TYPES, ...BossModLogShape.TYPES.map((type) =>
                ({ value: type, label: TYPE_LABELS[type] }))],
            onChange,
        });

        const search = BossModSearchField.create({
            placeholder: 'Search the log',
            label: 'Search the log',
            onInput: () => {
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

        const element = h('div', { class: 'place-controls' },
            agents.element, types.element, search.element, followSwitch.element);

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
                    type: types.getValue(),
                    search: search.input.value,
                };
            },

            /** @returns {boolean} */
            following() {
                return follow;
            },

            /**
             * Repopulate the agent options, keeping the current choice — which
             * may be a deep link to someone who has not appeared in the rows
             * yet, so the id is honoured whether or not it is in the list: it
             * is added as its own option, named "Unknown agent" until a row or
             * the roster names it, so the dropdown shows the filter applied.
             *
             * @param {Array<{id: string, name: string}>} list
             * @returns {void}
             */
            setAgents(list) {
                known = list;
                const match = list.find((agent) => agent.id === chosen.id);
                if (match) chosen = { id: match.id, name: match.name };
                const options = list.map((agent) => ({
                    value: agent.id,
                    label: agent.name,
                    avatar: { name: agent.name, color: agent.color },
                }));
                if (chosen.id && !match) options.push({ value: chosen.id, label: 'Unknown agent' });
                agents.setOptions([ALL_AGENTS, ...options], chosen.id);
            },

            /**
             * Cancel the pending search debounce and put the dropdowns away.
             * @returns {void}
             */
            destroy() {
                clearTimeout(searchTimer);
                agents.destroy();
                types.destroy();
            },
        };
    }

    return { createFilters, TYPE_LABELS };
})();
