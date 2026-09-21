/**
 * BossMod AI — the Tasks place's toolbar.
 *
 * Search, the assignee filter, the subtask toggle, the `⋯`, `+ New task`, and
 * the bulk cancel that appears only once something is selected. It owns the
 * filter state and reports every change through one `onChange` callback, so
 * the place never reads a control's value out of the DOM and the controls are
 * never rebuilt on a repaint — which is what keeps the caret in the search box
 * while tasks stream in.
 *
 * The `⋯` is handed in rather than built here: its panel reads the place's
 * Done window and sort, which this module has no business holding.
 */
const BossModTasksToolbar = (() => {
    const { h } = BossModDom;

    /** The assignee dropdown's first option: no filter at all. */
    const EVERYONE = Object.freeze({ value: '', label: 'Everyone' });

    const SEARCH_DELAY_MS = 200;

    /**
     * Build the toolbar.
     *
     * @param {object} deps
     * @param {string|null} [deps.agentId]  Preset by the Desk's "See all",
     *   which arrives as a place param rather than as a click.
     * @param {HTMLElement} deps.menuButton  The `⋯` (BossModTasksMenu), placed
     *   between the subtask toggle and `+ New task`.
     * @param {(agentId: string) => ({name: string, color: string}|null)} deps.rosterAgent
     *   The roster's entry for an id, or null: the avatars' colours, and the
     *   name of a preset assignee who holds no task in the list.
     * @param {() => void} deps.onChange  A filter moved; repaint.
     * @param {() => void} deps.onNewTask
     * @param {() => void} deps.onCancelSelected
     * @returns {{element: HTMLElement, filters: () => object,
     *   setAgents: (agents: object[]) => void, setSelectedCount: (n: number) => void,
     *   destroy: () => void}}
     * @throws {Error} When a callback or the menu button is missing.
     */
    function createToolbar(deps) {
        const {
            agentId = null, menuButton, rosterAgent, onChange, onNewTask, onCancelSelected,
        } = deps || {};
        if (!menuButton) throw new Error('[tasks-toolbar] deps.menuButton is required');
        if (typeof rosterAgent !== 'function') throw new Error('[tasks-toolbar] deps.rosterAgent is required');
        if (typeof onChange !== 'function') throw new Error('[tasks-toolbar] deps.onChange is required');
        if (typeof onNewTask !== 'function') throw new Error('[tasks-toolbar] deps.onNewTask is required');
        if (typeof onCancelSelected !== 'function') {
            throw new Error('[tasks-toolbar] deps.onCancelSelected is required');
        }

        let currentAgent = agentId;
        let showChildren = false;
        let searchTimer = null;

        const search = BossModSearchField.create({
            placeholder: 'Search all tasks',
            label: 'Search tasks by title',
            className: 'tasks-search',
            onInput: () => {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(onChange, SEARCH_DELAY_MS);
            },
        });

        // "Everyone" is the first option and the way back from a filter, so
        // there is no separate clear-filter chip to keep in step with it. It
        // starts on Everyone even when the Desk preset an agent: the name is
        // not known until the tasks load, and setAgents() then shows the
        // choice `currentAgent` already holds.
        const agents = BossModMenuSelect.create({
            label: 'Filter by assignee',
            options: [EVERYONE],
            onChange: (value) => {
                currentAgent = value || null;
                onChange();
            },
        });

        const children = BossModSwitch.create({
            label: 'Subtasks',
            pressed: showChildren,
            onChange: (on) => { showChildren = on; onChange(); },
        });

        const cancel = h('button', {
            class: 'btn btn-sm tasks-danger', type: 'button', onclick: onCancelSelected,
        });

        const element = h('div', { class: 'place-controls' },
            search.element,
            agents.element,
            children.element,
            menuButton,
            h('button', { class: 'btn-link', type: 'button', onclick: onNewTask }, '+ New task'),
            cancel);

        /**
         * @param {number} count  Zero hides the action: a destructive button
         *   that would cancel nothing has no reason to be on screen.
         * @returns {void}
         */
        function setSelectedCount(count) {
            // Set as the property, which reflects to the attribute that
            // `.tasks-place [hidden]` keys on over `.btn`'s own display.
            cancel.hidden = count === 0;
            cancel.textContent = `Cancel selected (${count})`;
        }
        setSelectedCount(0);

        return {
            element,

            /**
             * @returns {{agentId: string|null, query: string, showChildren: boolean}}
             */
            filters() {
                return { agentId: currentAgent, query: search.input.value, showChildren };
            },

            /**
             * Repopulate the assignee options, keeping the current choice.
             *
             * The choice is kept even when that assignee holds no task in the
             * list — the Desk's "See all" can preset one — so it is added as an
             * option under its roster name, and the dropdown names the filter
             * that is actually applied.
             *
             * @param {Array<{id: string, name: string}>} list
             * @returns {void}
             */
            setAgents(list) {
                const people = list.slice();
                if (currentAgent && !people.some((agent) => agent.id === currentAgent)) {
                    const known = rosterAgent(currentAgent);
                    people.push({ id: currentAgent, name: known ? known.name : 'Unknown agent' });
                }
                agents.setOptions([EVERYONE, ...people.map((agent) => {
                    const known = rosterAgent(agent.id);
                    return {
                        value: agent.id,
                        label: agent.name,
                        avatar: { name: agent.name, color: known ? known.color : undefined },
                    };
                })], currentAgent || '');
            },

            setSelectedCount,

            /**
             * Cancel the pending search debounce and put the dropdown away.
             * @returns {void}
             */
            destroy() {
                clearTimeout(searchTimer);
                agents.destroy();
            },
        };
    }

    return { createToolbar };
})();
