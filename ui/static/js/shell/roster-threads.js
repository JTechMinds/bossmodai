/**
 * BossMod AI — the Threads half of the roster rail.
 *
 * People owns the multi-select; Threads consumes it. That is the seam: a
 * thread is created FROM a selection, so selection state stays with the list
 * that produces it and this module only reads it — and asks for the mode to
 * open or close, which is the one thing it drives rather than reads.
 *
 * Creation is two states, not one permanent button. `New thread` opens select
 * mode; `Create with N` and `Cancel` close it. The old always-on Create Thread
 * button was disabled most of the time, which made a permanent row of
 * checkboxes the only way to understand what it wanted.
 *
 * Split out of shell/roster.js in Phase 2B, before the needs surfaces added
 * anything else to a file that had already passed the ~300-line guideline.
 */
const BossModRosterThreads = (() => {
    const { h, clear } = BossModDom;

    const THREAD_HINT = 'Select teammates and start a shared thread.';
    const NEW_THREAD_LABEL = 'New thread';

    /**
     * Build the Threads section.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; `threads` and `rosterQuery`
     *   are the two slices this module reads.
     * @param {(url: string, init: object, failure: string) => Promise<any|null>}
     *   deps.readJson  The rail's shared request helper. It resolves null and
     *   reports on the rail's one error line when the request fails, so both
     *   halves surface a failure the same way rather than each inventing one.
     * @param {() => string[]} deps.getSelection  Selected agent ids, from People.
     * @param {() => boolean} deps.isSelecting  Whether People is showing its
     *   checkboxes. Read rather than mirrored: two copies of one mode is how
     *   the button and the rows end up disagreeing about which state they are in.
     * @param {() => void} deps.onEnterSelect  Ask People to show the checkboxes.
     * @param {() => void} deps.onExitSelect   Ask it to hide them and forget.
     * @param {() => void} deps.onConsumed  Called once a selection has become a
     *   thread. Threads reads the selection but never owns it, so clearing the
     *   checkboxes is People's job and this is the only way to ask for it.
     * @param {(id: string, kind: 'thread') => void} deps.onOpen
     * @returns {{ element: HTMLElement,
     *             refresh: () => Promise<void>,
     *             applySelection: () => void,
     *             handleChannelUpdated: (summary: object) => void,
     *             destroy: () => void }}
     * @throws {Error} When any dependency is missing — a half-wired rail must
     *   fail at mount rather than render a list that never loads.
     */
    function createThreads(deps) {
        const {
            store, readJson, getSelection, isSelecting,
            onEnterSelect, onExitSelect, onConsumed, onOpen,
        } = deps || {};
        if (!store) throw new Error('[roster-threads] deps.store is required');
        if (typeof readJson !== 'function') throw new Error('[roster-threads] deps.readJson is required');
        if (typeof getSelection !== 'function') {
            throw new Error('[roster-threads] deps.getSelection is required');
        }
        if (typeof isSelecting !== 'function') {
            throw new Error('[roster-threads] deps.isSelecting is required');
        }
        if (typeof onEnterSelect !== 'function') {
            throw new Error('[roster-threads] deps.onEnterSelect is required');
        }
        if (typeof onExitSelect !== 'function') {
            throw new Error('[roster-threads] deps.onExitSelect is required');
        }
        if (typeof onConsumed !== 'function') {
            throw new Error('[roster-threads] deps.onConsumed is required');
        }
        if (typeof onOpen !== 'function') throw new Error('[roster-threads] deps.onOpen is required');

        const disposers = [];
        let threadsState = 'loading';
        let threadFilter = 'active';

        const threadList = h('ul', { class: 'roster-threads' });
        // Archiving a thread is reversible, so the archived list has to stay
        // reachable — otherwise "seals the room" would read as "deletes it".
        const filterActive = h('button', {
            class: 'roster-thread-filter',
            id: 'channels-filter-active',
            type: 'button',
            onclick: () => setThreadFilter('active'),
        }, 'Active');
        const filterArchived = h('button', {
            class: 'roster-thread-filter',
            id: 'channels-filter-archived',
            type: 'button',
            onclick: () => setThreadFilter('archived'),
        }, 'Archived');
        const threadFilters = h('div', {
            class: 'roster-thread-filters',
            role: 'group',
            'aria-label': 'Thread list filter',
        }, filterActive, filterArchived);
        // One button, one listener, two jobs — the state decides which. Rebinding
        // a fresh handler on every repaint would stack listeners on a node the
        // operator is already pointing at.
        const createThread = h('button', {
            class: 'btn btn-quiet roster-create-thread',
            type: 'button',
            onclick: () => {
                if (isSelecting()) void createThreadFromSelection();
                else onEnterSelect();
            },
        }, NEW_THREAD_LABEL);
        // Built once, attached only while the mode is on: a Cancel that is
        // merely hidden is still a tab stop for a mode nobody is in.
        const cancelSelect = h('button', {
            class: 'btn btn-quiet roster-thread-cancel',
            type: 'button',
            onclick: () => onExitSelect(),
        }, 'Cancel');
        const threadActions = h('div', { class: 'roster-thread-actions' }, createThread);
        // Tracked here rather than read back off the DOM: "is it attached" is
        // this module's own bookkeeping, and asking the node makes the answer
        // depend on which parent property the host happens to expose.
        let cancelAttached = false;
        const element = h('section', { class: 'roster-section' },
            h('h2', { class: 'roster-section-title' }, 'Threads'),
            threadFilters,
            h('p', { class: 'roster-thread-hint' }, THREAD_HINT),
            threadActions,
            threadList);

        /** Threads live in the store too: boot validates a restored thread against them. */
        function threads() {
            return store.getState().threads;
        }

        function renderThreads() {
            clear(threadList);
            if (threadsState === 'loading') {
                threadList.append(h('li', { class: 'roster-skeleton' }, 'Loading threads…'));
                return;
            }
            if (threadsState === 'error') {
                threadList.append(h('li', { class: 'roster-empty' }, 'Could not load threads.'));
                return;
            }
            if (threads().length === 0) {
                threadList.append(h('li', { class: 'roster-empty' }, threadFilter === 'archived'
                    ? 'No archived threads.'
                    : 'No threads yet.'));
                return;
            }
            const query = String(store.getState().rosterQuery).trim().toLowerCase();
            const visible = threads().filter((thread) => !query
                || String(thread.name || '').toLowerCase().includes(query));
            if (visible.length === 0) {
                threadList.append(h('li', { class: 'roster-empty' }, 'No threads match that search.'));
                return;
            }
            visible.forEach((thread) => {
                threadList.append(h('li', { class: 'roster-row' },
                    h('button', {
                        class: 'roster-thread',
                        type: 'button',
                        'data-thread-id': thread.id,
                        onclick: () => onOpen(thread.id, 'thread'),
                    },
                        h('span', { class: 'roster-name' }, thread.name),
                        h('span', { class: 'roster-status' },
                            `${thread.member_count} member${thread.member_count === 1 ? '' : 's'}`))));
            });
        }

        function applyThreadFilter() {
            filterActive.setAttribute('aria-pressed', String(threadFilter === 'active'));
            filterArchived.setAttribute('aria-pressed', String(threadFilter === 'archived'));
        }

        /**
         * Switch which thread list is shown and re-fetch it.
         * @param {'active'|'archived'} next
         */
        function setThreadFilter(next) {
            if (threadFilter === next) return;
            threadFilter = next;
            applyThreadFilter();
            threadsState = 'loading';
            renderThreads();
            void loadThreads();
        }

        async function loadThreads() {
            const channels = await readJson(
                `/api/channels?status=${encodeURIComponent(threadFilter)}`,
                { cache: 'no-store' },
                'Could not load threads.',
            );
            if (channels === null) {
                threadsState = 'error';
                renderThreads();
                return;
            }
            threadsState = 'ready';
            store.setState({ threads: channels });
        }

        async function createThreadFromSelection() {
            const agentIds = getSelection();
            if (agentIds.length === 0) return;
            const channel = await readJson('/api/channels', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ agent_ids: agentIds }),
            }, 'Could not create the thread.');
            if (channel === null) return;
            onConsumed();
            // A new thread is active, so show the list it landed in.
            threadFilter = 'active';
            applyThreadFilter();
            await loadThreads();
            onOpen(channel.id, 'thread');
        }

        disposers.push(store.subscribe((s) => s.threads, renderThreads));
        disposers.push(store.subscribe((s) => s.rosterQuery, renderThreads));

        renderThreads();
        applyThreadFilter();

        return {
            element,

            /**
             * Re-fetch the current list. Called on boot and after an outage;
             * a dropped socket loses every channel_updated in the gap.
             * @returns {Promise<void>}
             */
            refresh() {
                return loadThreads();
            },

            /**
             * Re-read the People selection and repaint the creation controls.
             *
             * Out of select mode the button invites; in it, the button counts
             * what is picked and Cancel appears beside it. The floor is one
             * teammate, which is what POST /api/channels accepts — a thread of
             * one is a real thing the operator can already make.
             *
             * @returns {void}
             */
            applySelection() {
                const selecting = isSelecting();
                const count = getSelection().length;
                clear(createThread);
                createThread.append(selecting ? `Create with ${count}` : NEW_THREAD_LABEL);
                createThread.disabled = selecting && count === 0;
                if (selecting && !cancelAttached) {
                    threadActions.append(cancelSelect);
                    cancelAttached = true;
                } else if (!selecting && cancelAttached) {
                    cancelSelect.remove();
                    cancelAttached = false;
                }
            },

            /**
             * React to a live `channel_updated` broadcast.
             *
             * Reopening from the archived list must show where the thread
             * went, so a non-archived summary pulls the filter back to Active.
             *
             * @param {object} summary  The broadcast payload; a missing or
             *   status-less summary still re-fetches, because "something
             *   changed" is the only claim the topic makes.
             * @returns {void}
             */
            handleChannelUpdated(summary) {
                if (summary && summary.status !== 'archived' && threadFilter === 'archived') {
                    threadFilter = 'active';
                    applyThreadFilter();
                }
                void loadThreads();
            },

            /**
             * Drain every subscription this half created.
             * @returns {void}
             */
            destroy() {
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createThreads };
})();
