/**
 * BossMod AI — the Threads half of the roster rail.
 *
 * People owns the multi-select; Threads consumes it. That is the seam: a
 * thread is created FROM a selection, so selection state stays with the list
 * that produces it and this module only reads it — and asks for the mode to
 * open or close, which is the one thing it drives rather than reads.
 *
 * MAKING a thread is shell/thread-create.js — the header row's middle slot,
 * its action group, and the POST. This module reads the list; that one writes
 * to it, and the only thing crossing between them is "a channel was created,
 * show it". Both halves are mounted here because they share a section header,
 * which is the whole of what they have in common.
 *
 * Split out of shell/roster.js in Phase 2B, before the needs surfaces added
 * anything else to a file that had already passed the ~300-line guideline; the
 * creation half was split out of THIS file for the same reason.
 */
const BossModRosterThreads = (() => {
    const { h, clear } = BossModDom;


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
        // The creation half. It owns the header row's middle slot, its
        // action group, and the POST; showing what came back is this half's
        // job, which is what onCreated hands over.
        const create = BossModThreadCreate.createThreadControls({
            readJson,
            getSelection,
            isSelecting,
            onEnterSelect,
            onExitSelect,
            onConsumed,
            onCreated: async (channel) => {
                // A new thread is active, so show the list it landed in.
                threadFilter = 'active';
                applyThreadFilter();
                await loadThreads();
                onOpen(channel.id, 'thread');
            },
        });

        // Three slots on one row: the label, what the mode is saying, and the
        // controls for it. Select mode adds no row of its own.
        const element = h('section', { class: 'roster-section' },
            h('div', { class: 'roster-section-head' },
                h('h2', { class: 'roster-section-title' }, 'Threads'),
                create.middle,
                create.actions),
            threadFilters,
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
                    // The same node and the same size a person row carries, so
                    // the two lists share one left edge. Decorative: the name
                    // beside it already says which thread this is.
                    BossModAvatar.create({ group: true, size: 'md' }),
                    h('button', {
                        class: 'roster-thread',
                        type: 'button',
                        'data-thread-id': thread.id,
                        onclick: () => onOpen(thread.id, 'thread'),
                    },
                        h('span', { class: 'roster-name' }, thread.name),
                        h('span', { class: 'roster-status' },
                            `${thread.member_count} member${thread.member_count === 1 ? '' : 's'}`)),
                    // The same right-hand column a person row carries, so the
                    // two lists share a right edge as well as a left one.
                    // GET /api/channels already carries the latest post's
                    // time; a room nobody has written in gets no timestamp
                    // rather than the moment the room was made. A thread has
                    // no need dot — needs belong to a person.
                    BossModRosterRowMeta.rowMeta(
                        thread.latest_message && thread.latest_message.created_at, false)));
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
             * Delegated whole: the selection belongs to People and the controls
             * belong to thread-create.js, so this half only forwards the news.
             * @returns {void}
             */
            applySelection() {
                create.applySelection();
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
                // The creation half owns a document listener of its own.
                create.destroy();
            },
        };
    }

    return { createThreads };
})();
