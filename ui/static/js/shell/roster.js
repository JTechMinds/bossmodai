/**
 * BossMod AI — the roster rail.
 *
 * The assembler. Search, the rail's one error line, a Hire row pinned to the
 * bottom, the world requests, and the wiring between the two halves: People is
 * shell/roster-people.js and Threads is shell/roster-threads.js.
 *
 * People owns the multi-select a thread is created from; Threads reads it and
 * asks for the mode to open or close. Neither half names the other — both
 * arrive here as dependencies, which is what keeps the seam a seam.
 *
 * Only the two lists re-render. The search input is built once at mount and
 * never replaced, so typing — and the caret — survive every world_update that
 * lands mid-keystroke.
 */
const BossModRoster = (() => {
    const { h, clear } = BossModDom;

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

        const errorEl = h('p', { class: 'roster-error', role: 'alert' });

        // ─── The two halves ───
        //
        // People is built first because Threads reads its selection. The
        // callbacks pointing the other way all run later, so neither half needs
        // a reference to the other at construction.

        const people = BossModRosterPeople.createPeople({
            store,
            onOpenConversation: (agentId) => openConversation(agentId, 'agent'),
            onOpenDesk: openDesk,
            onSelectionChange: () => threads.applySelection(),
        });

        const threads = BossModRosterThreads.createThreads({
            store,
            readJson,
            getSelection: () => people.selection(),
            isSelecting: () => people.isSelecting(),
            onEnterSelect: () => people.enterSelectMode(),
            onExitSelect: () => people.exitSelectMode(),
            onConsumed: () => people.exitSelectMode(),
            onOpen: openConversation,
        });

        // An empty seat, not a stray glyph: the dashed circle lines up with the
        // avatars above it, so the row reads as one more person here.
        const hire = h('button', {
            class: 'roster-hire',
            type: 'button',
            onclick: () => onHire(),
        },
            h('span', { class: 'avatar avatar-md avatar-empty', 'aria-hidden': 'true' }, '+'),
            h('span', {}, 'Hire someone'));

        el.append(searchRow,
            h('div', { class: 'roster-body' }, people.element, threads.element, errorEl),
            hire);

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
                people.setLoadState('error');
                return;
            }
            people.setLoadState('ready');
            store.setState({ roster: world.map(BossModAgentStatus.normalizeAgent) });
        }

        // ─── Live updates ───

        disposers.push(bus.subscribe('world_update', (world) => {
            people.setLoadState('ready');
            store.setState({
                roster: BossModAgentStatus.mergeRosterFromWorld(
                    store.getState().roster,
                    world.map(BossModAgentStatus.normalizeAgent),
                ),
            });
        }));
        // A dropped socket loses every world_update and channel_updated in the gap.
        disposers.push(bus.subscribe('resync', () => { void loadPeople(); void threads.refresh(); }));
        disposers.push(bus.subscribe('channel_updated', (summary) => {
            threads.handleChannelUpdated(summary);
        }));
        disposers.push(() => people.destroy());
        disposers.push(() => threads.destroy());

        threads.applySelection();
        lucide.createIcons();
        void loadPeople();
        void threads.refresh();

        return () => { disposers.splice(0).forEach((off) => off()); };
    }

    return { mount };
})();
