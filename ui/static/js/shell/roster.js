/**
 * BossMod AI — the roster rail.
 *
 * Search, People, the Threads half (shell/roster-threads.js), and a Hire row
 * pinned to the bottom. People owns the multi-select a thread is created from;
 * Threads only reads it.
 *
 * Only the two lists re-render. The search input is built once at mount and
 * never replaced, so typing — and the caret — survive every world_update that
 * lands mid-keystroke.
 */
const BossModRoster = (() => {
    const { h, clear } = BossModDom;

    /**
     * The one-line state under an agent's name.
     *
     * Precedence is deliberate: a paused runtime makes every other state a
     * lie, and an open need outranks whatever the agent was doing, because it
     * is the only one of the three the operator can act on.
     *
     * @param {object} agent
     * @param {boolean} runtimePaused
     * @param {Set<string>} agentsWithNeeds
     * @returns {string}
     */
    function statusLine(agent, runtimePaused, agentsWithNeeds) {
        if (runtimePaused) return 'Paused';
        if (agentsWithNeeds.has(agent.id)) return 'Needs you';
        return BossModAgentStatus.getStatusLabel(agent.status, agent.currentActivityKind);
    }

    /**
     * Render the roster into `el`.
     *
     * @param {HTMLElement} el
     * @param {object} deps
     * @param {object} deps.store
     * @param {object} deps.bus
     * @param {Function} deps.apiFetch                Authenticated request helper.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @param {() => void} deps.onHire                Starts the agent-create flow.
     * @returns {() => void} disposer — drains every subscription.
     */
    function mount(el, deps) {
        const { store, bus, apiFetch, navigate, onHire } = deps;
        const disposers = [];

        let peopleState = 'loading';
        const selected = new Set();

        clear(el);

        // ─── Search ───

        const search = h('input', {
            class: 'roster-search',
            type: 'search',
            id: 'roster-search',
            placeholder: 'Search by name or role',
            oninput: (event) => { store.setState({ rosterQuery: event.target.value }); },
        });
        const searchRow = h('div', { class: 'roster-search-row' },
            h('label', { class: 'visually-hidden', for: 'roster-search' }, 'Search people and threads'),
            search);

        // ─── People ───

        const peopleList = h('ul', { class: 'roster-people' });
        const peopleSection = h('section', { class: 'roster-section' },
            h('h2', { class: 'roster-section-title' }, 'People'),
            peopleList);

        const errorEl = h('p', { class: 'roster-error', role: 'alert' });

        // ─── Threads ───

        const threads = BossModRosterThreads.createThreads({
            store,
            readJson,
            getSelection: () => Array.from(selected),
            onConsumed: () => {
                selected.clear();
                threads.applySelection();
                renderPeople();
            },
            onOpen: openConversation,
        });

        const hire = h('button', {
            class: 'roster-hire',
            type: 'button',
            onclick: () => onHire(),
        },
            h('i', { 'data-lucide': 'user-plus', 'aria-hidden': 'true' }),
            h('span', {}, 'Hire someone'));

        el.append(searchRow,
            h('div', { class: 'roster-body' }, peopleSection, threads.element, errorEl),
            hire);

        // ─── Rendering ───

        /** The roster lives in the store: the footer counts it and places read it. */
        function people() {
            return store.getState().roster;
        }

        function agentsWithNeeds() {
            return new Set(store.getState().needs.map((need) => need.agentId));
        }

        function matches(agent, query) {
            if (!query) return true;
            const name = String(agent.name || '').toLowerCase();
            const role = String(agent.role || '').toLowerCase();
            return name.includes(query) || role.includes(query);
        }

        function renderPeople() {
            clear(peopleList);
            if (peopleState === 'loading') {
                peopleList.append(h('li', { class: 'roster-skeleton' }, 'Loading people…'));
                return;
            }
            if (peopleState === 'error') {
                peopleList.append(h('li', { class: 'roster-empty' }, 'Could not load the roster.'));
                return;
            }
            if (people().length === 0) {
                peopleList.append(h('li', { class: 'roster-empty' }, 'No one is hired yet.'));
                return;
            }

            const query = String(store.getState().rosterQuery).trim().toLowerCase();
            const visible = people().filter((agent) => matches(agent, query));
            if (visible.length === 0) {
                peopleList.append(h('li', { class: 'roster-empty' }, 'No one matches that search.'));
                return;
            }

            const paused = store.getState().runtimePaused === true;
            const needy = agentsWithNeeds();
            visible.forEach((agent) => {
                const checkbox = h('input', {
                    class: 'roster-select',
                    type: 'checkbox',
                    'aria-label': `Include ${agent.name} in a new thread`,
                    onchange: (event) => {
                        if (event.target.checked) selected.add(agent.id);
                        else selected.delete(agent.id);
                        threads.applySelection();
                    },
                });
                checkbox.checked = selected.has(agent.id);

                peopleList.append(h('li', { class: 'roster-row' },
                    checkbox,
                    h('button', {
                        class: 'roster-avatar',
                        type: 'button',
                        style: `background:${agent.color}`,
                        'aria-label': `Open ${agent.name}'s desk`,
                        onclick: () => openDesk(agent.id),
                    }),
                    h('button', {
                        class: 'roster-person',
                        type: 'button',
                        'data-agent-id': agent.id,
                        onclick: () => openConversation(agent.id, 'agent'),
                    },
                        h('span', { class: 'roster-name' }, agent.name),
                        h('span', { class: 'roster-status' }, statusLine(agent, paused, needy)),
                        needy.has(agent.id)
                            ? h('span', { class: 'roster-need-dot', 'aria-hidden': 'true' })
                            : null)));
            });
            lucide.createIcons();
        }

        /**
         * Navigation is only how the operator REACHES Chat; the store is what
         * switches the conversation. Navigating while already there would
         * remount the place and take the transcript cache, the composer draft,
         * and the caret with it on every roster click.
         *
         * @param {string} id
         * @param {'agent'|'thread'} kind
         */
        function openConversation(id, kind) {
            store.setState({ conversationId: id, conversationKind: kind });
            if (store.getState().place !== 'chat') navigate('chat');
        }

        function openDesk(agentId) {
            store.setState({ contextMode: 'desk', deskAgentId: agentId });
            if (store.getState().place !== 'chat') navigate('chat');
        }

        function reportError(message, err) {
            console.error(`[roster] ${message}`, err);
            clear(errorEl);
            errorEl.append(message);
        }

        // ─── Loading ───

        /**
         * Read JSON from the API, reporting either failure mode the same way.
         * A transport error and a rejected response are the same news to the
         * operator, and neither is ever swallowed. Shared with the Threads
         * half so the rail has exactly one error line.
         *
         * @param {string} url
         * @param {object} init
         * @param {string} failure Operator-facing message on either failure.
         * @returns {Promise<any|null>} null when the request failed.
         */
        async function readJson(url, init, failure) {
            clear(errorEl);
            let response;
            try {
                response = await apiFetch(url, init);
            } catch (err) {
                reportError(failure, err);
                return null;
            }
            if (!response.ok) {
                reportError(failure, new Error(`HTTP ${response.status}`));
                return null;
            }
            return response.json();
        }

        async function loadPeople() {
            const world = await readJson('/api/world', { cache: 'no-store' }, 'Could not load the roster.');
            if (world === null) {
                peopleState = 'error';
                renderPeople();
                return;
            }
            peopleState = 'ready';
            store.setState({ roster: world.map(BossModAgentStatus.normalizeAgent) });
        }

        // ─── Live updates ───

        disposers.push(bus.subscribe('world_update', (world) => {
            peopleState = 'ready';
            store.setState({
                roster: BossModAgentStatus.mergeRosterFromWorld(people(), world.map(BossModAgentStatus.normalizeAgent)),
            });
        }));
        // A dropped socket loses every world_update and channel_updated in the gap.
        disposers.push(bus.subscribe('resync', () => { void loadPeople(); void threads.refresh(); }));
        disposers.push(store.subscribe((s) => s.roster, renderPeople));
        disposers.push(bus.subscribe('channel_updated', (summary) => {
            threads.handleChannelUpdated(summary);
        }));
        disposers.push(store.subscribe((s) => s.rosterQuery, renderPeople));
        disposers.push(store.subscribe((s) => s.needs, renderPeople));
        disposers.push(store.subscribe((s) => s.runtimePaused, renderPeople));
        disposers.push(() => threads.destroy());

        renderPeople();
        threads.applySelection();
        lucide.createIcons();
        void loadPeople();
        void threads.refresh();

        return () => { disposers.splice(0).forEach((off) => off()); };
    }

    return { mount };
})();
