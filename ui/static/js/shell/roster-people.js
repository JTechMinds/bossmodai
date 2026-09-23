/**
 * BossMod AI — the People half of the roster rail.
 *
 * The mirror of shell/roster-threads.js. Phase 2B split Threads out of
 * shell/roster.js when the needs surfaces pushed it past the 300-line cap;
 * select mode pushed it past again, and this is the same split on the other
 * half. shell/roster.js is now the assembler: search, the one error line, the
 * hire row, the world requests, and the wiring between the two halves.
 *
 * It owns the multi-select a thread is created from. That is the seam Threads
 * documents from its side: the selection lives with the list that produces it,
 * and Threads only reads it and asks for the mode to open or close.
 *
 * The mode matters more than it looks. A checkbox on every row all the time was
 * permanent clutter for an occasional action, so the box is BUILT or NOT BUILT
 * — never hidden with CSS. A hidden checkbox is still a tab stop and still
 * holds a stale `checked`, which is exactly how a selection nobody can see
 * builds the wrong thread the next time.
 *
 * In select mode the ROW is what selects. A row already carries two controls
 * with their own jobs — the avatar opens the desk, the name opens the
 * conversation — and while the mode is on both of them toggle instead. They
 * are RE-BOUND rather than wrapped: a button inside a button is invalid markup
 * and unreachable by keyboard, so there is no row-sized control over the top
 * of the two that are already there. What that costs is honest: the gaps and
 * the padding between the two controls are not targets, so "the whole row" is
 * really "the face and the name", which is what the operator was aiming at.
 *
 * The checkbox stays, and stops being the hit target: it is the mode's only
 * visible state, and a 16px box is a poor thing to have to hit. The row
 * carries the state too, through `data-selected`.
 *
 * The header's `⋯` is shell/people-view-menu.js. It owns the "Show roles"
 * preference and where it is kept; this half only asks `view.showRoles()`
 * when it renders.
 */
const BossModRosterPeople = (() => {
    const { h, clear } = BossModDom;

    /**
     * The one-line state under an agent's name.
     *
     * Precedence is live first: a paused runtime makes every other state a
     * lie, then Working / Idle / Break from current activity (the same truth
     * as Writing / Done in the thread). Needs you is only an uncleared Focus
     * ask — a stale error card or denied consent must not outrank writing.
     *
     * @param {object} agent
     * @param {boolean} runtimePaused
     * @param {Set<string>} agentsWithNeeds
     * @returns {string}
     */
    function statusLine(agent, runtimePaused, agentsWithNeeds) {
        if (runtimePaused) return 'Paused';
        if (hasLiveActivity(agent)) {
            return BossModAgentStatus.getStatusLabel(agent.status, agent.currentActivityKind);
        }
        if (agentsWithNeeds.has(agent.id)) return 'Needs you';
        return BossModAgentStatus.getStatusLabel(agent.status, agent.currentActivityKind);
    }

    /**
     * Whether the row should read the live activity word.
     *
     * A running kind is the thread's Writing / Break. work_active without a
     * kind still means they are on a turn, so an old Focus error cannot
     * relabel that row Needs you.
     *
     * @param {object} agent
     * @returns {boolean}
     */
    function hasLiveActivity(agent) {
        if (!agent) return false;
        if (agent.currentActivityKind) return true;
        return agent.status === 'work_active' || agent.status === 'social_active';
    }

    /**
     * @param {object} agent
     * @param {string} query  Already lower-cased and trimmed.
     * @returns {boolean}
     */
    function matches(agent, query) {
        if (!query) return true;
        const name = String(agent.name || '').toLowerCase();
        const role = String(agent.role || '').toLowerCase();
        return name.includes(query) || role.includes(query);
    }

    /**
     * Build the People section.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; `roster`, `rosterQuery`,
     *   `needs` and `runtimePaused` are the four slices this half reads.
     * @param {(agentId: string) => void} deps.onOpenConversation
     * @param {(agentId: string) => void} deps.onOpenDesk
     * @param {() => void} deps.onSelectionChange  Called whenever the selection
     *   or the mode changes. The Threads half re-reads the selection through
     *   it; People never names the module that listens.
     * @param {object} [deps.seat]  From BossModThreadSeat.createThreadSeat.
     *   When a live thread is open, the People header offers Add to thread.
     * @param {(message: string, err?: Error) => void} [deps.onError]
     * @returns {{ element: HTMLElement,
     *             selection: () => string[],
     *             isSelecting: () => boolean,
     *             enterSelectMode: () => void,
     *             exitSelectMode: () => void,
     *             setLoadState: (state: 'loading'|'ready'|'error') => void,
     *             destroy: () => void }}
     * @throws {Error} When any dependency is missing — a half-wired list would
     *   render rows whose clicks go nowhere.
     */
    function createPeople(deps) {
        const { store, onOpenConversation, onOpenDesk, onSelectionChange, seat, onError } = deps || {};
        if (!store) throw new Error('[roster-people] deps.store is required');
        if (typeof onOpenConversation !== 'function') {
            throw new Error('[roster-people] deps.onOpenConversation is required');
        }
        if (typeof onOpenDesk !== 'function') {
            throw new Error('[roster-people] deps.onOpenDesk is required');
        }
        if (typeof onSelectionChange !== 'function') {
            throw new Error('[roster-people] deps.onSelectionChange is required');
        }

        const disposers = [];
        let loadState = 'loading';
        let selectMode = false;
        const selected = new Set();

        const list = h('ul', { class: 'roster-people' });
        // `getContainer` is a thunk for the reason Threads gives: the row the
        // panel hangs off cannot be built until the `⋯` exists to go in it.
        const view = BossModPeopleViewMenu.createPeopleViewMenu({
            getContainer: () => head,
            onChange: render,
        });
        // The seat group is mounted only while a live thread is open — a
        // hidden `roster-section-actions` would become the rail's first header
        // group and steal Threads' `+`. The `⋯` is always here, so it sits
        // OUTSIDE any such group: inside one, that group would exist at rest.
        const head = h('div', { class: 'roster-section-head' },
            h('h2', { class: 'roster-section-title' }, 'People'),
            view.button);
        const element = h('section', { class: 'roster-section' }, head, list);
        let seatGroup = null;

        function agentsWithNeeds() {
            return new Set(store.getState().needs
                .filter((need) => BossModNeedShape.isOpenFocusNeed(need))
                .map((need) => need.agentId));
        }

        /**
         * Build one person's row.
         *
         * Out of select mode the avatar opens the desk and the name opens the
         * conversation. In it, both toggle this row's inclusion instead — the
         * same three controls, re-bound, so nothing is nested and every target
         * is still a real <button> that Enter and Space reach (SC 2.1.1).
         *
         * @param {object} agent
         * @param {boolean} paused
         * @param {Set<string>} needy
         * @param {boolean} showRoles  From the header's `⋯`.
         * @returns {HTMLElement}
         */
        function personRow(agent, paused, needy, showRoles) {
            const selecting = selectMode;
            const role = showRoles && typeof agent.role === 'string' && agent.role.trim()
                ? agent.role.trim()
                : null;
            const includeLabel = `Include ${agent.name} in a new thread`;
            const box = selecting
                ? h('input', {
                    class: 'roster-select',
                    type: 'checkbox',
                    'aria-label': includeLabel,
                    onchange: (event) => setSelected(event.target.checked === true),
                })
                : null;
            if (box) box.checked = selected.has(agent.id);

            /**
             * Record this row's inclusion, and reflect it everywhere it shows.
             *
             * In place rather than through render(): a repaint on every click
             * would replace the node the operator is pointing at and take
             * keyboard focus with it.
             *
             * @param {boolean} on
             */
            function setSelected(on) {
                if (on) selected.add(agent.id);
                else selected.delete(agent.id);
                box.checked = on;
                li.setAttribute('data-selected', String(on));
                onSelectionChange();
            }

            const toggle = () => setSelected(!selected.has(agent.id));

            const li = h('li', {
                class: 'roster-row',
                // Absent out of the mode rather than "false": the row has no
                // selectable state to report when there is no selection.
                'data-selecting': selecting ? 'true' : null,
                'data-selected': selecting ? String(selected.has(agent.id)) : null,
            },
                box,
                // The rail's circle carried no initial at all before the
                // shared primitive: eight identically-shaped colour dots
                // and no way to tell them apart on a monochrome display.
                BossModAvatar.create({
                    name: agent.name,
                    color: agent.color,
                    size: 'md',
                    interactive: true,
                    label: selecting ? includeLabel : `Open ${agent.name}'s desk`,
                    onClick: selecting ? toggle : () => onOpenDesk(agent.id),
                }),
                h('button', {
                    class: 'roster-person',
                    type: 'button',
                    'data-agent-id': agent.id,
                    // The visible label is the name, and the accessible name
                    // contains it, so the two agree (SC 2.5.3) while saying
                    // what the click actually does in this mode.
                    'aria-label': selecting ? includeLabel : null,
                    onclick: selecting ? toggle : () => onOpenConversation(agent.id),
                },
                    // One row shape, roles on or off. `title` is a native
                    // tooltip because the app's [data-tooltip] bubble is
                    // right-anchored for icon-only controls and would clip at
                    // the rail's left edge.
                    h('span', { class: 'roster-name-line' },
                        h('span', { class: 'roster-name' }, agent.name),
                        role ? h('span', { class: 'roster-role', title: role }, `– ${role}`) : null),
                    h('span', { class: 'roster-status' }, statusLine(agent, paused, needy))),
                // The row's right-hand column, shared with the Threads half:
                // when the operator last spoke to this agent, over the need
                // dot. A sibling of the name button, because inside the text
                // column either of them pushes the status line around.
                BossModRosterRowMeta.rowMeta(agent.lastMessageAt, needy.has(agent.id)));
            return li;
        }

        function render() {
            clear(list);
            if (loadState === 'loading') {
                list.append(h('li', { class: 'roster-skeleton' }, 'Loading people…'));
                return;
            }
            if (loadState === 'error') {
                list.append(h('li', { class: 'roster-empty' }, 'Could not load the roster.'));
                return;
            }
            const state = store.getState();
            const roster = typeof BossModFloorScope === 'undefined'
                ? state.roster
                : BossModFloorScope.filterPeople(state, state.roster);
            if (state.roster.length === 0) {
                list.append(h('li', { class: 'roster-empty' }, 'No one is hired yet.'));
                return;
            }
            if (roster.length === 0) { list.append(h('li', { class: 'roster-empty' }, 'No one on this floor.')); return; }

            const query = String(state.rosterQuery).trim().toLowerCase();
            const visible = roster.filter((agent) => matches(agent, query));
            if (visible.length === 0) {
                list.append(h('li', { class: 'roster-empty' }, 'No one matches that search.'));
                return;
            }

            const paused = store.getState().runtimePaused === true;
            const needy = agentsWithNeeds();
            const showRoles = view.showRoles();
            visible.forEach((agent) => list.append(personRow(agent, paused, needy, showRoles)));
            syncSeatAction();
            BossModIcons.paintDocument('roster-people');
        }

        /**
         * The open conversation, when it is a shared thread.
         * @returns {object|null}
         */
        function openThread() {
            const state = store.getState();
            if (state.conversationKind !== 'thread' || !state.conversationId) return null;
            return (state.threads || []).find((thread) => thread && thread.id === state.conversationId)
                || { id: state.conversationId, status: 'active', members: [] };
        }

        function syncSeatAction() {
            const thread = openThread();
            const show = Boolean(seat) && Boolean(thread) && thread.status !== 'archived';
            if (show === Boolean(seatGroup)) return;
            if (!show) {
                seatGroup.remove();
                seatGroup = null;
                return;
            }
            seatGroup = h('div', { class: 'roster-section-actions' },
                h('button', {
                    class: 'roster-section-action',
                    id: 'roster-seat-agent',
                    type: 'button',
                    'aria-label': 'Add to thread',
                    onclick: () => { void seatIntoOpenThread(); },
                }, h('i', { 'data-lucide': 'user-plus', 'aria-hidden': 'true' })));
            // Before the `⋯`, which stays last on the row as it does on Threads.
            head.insertBefore(seatGroup, view.button);
            BossModIcons.paint(seatGroup, 'roster-people');
        }

        async function seatIntoOpenThread() {
            const thread = openThread();
            if (!seat || !thread || thread.status === 'archived') return;
            try {
                await seat.pickAndSeat(thread.id, thread.members || []);
            } catch (err) {
                if (typeof onError === 'function') {
                    onError((err && err.message) || 'Could not add that agent to this thread.', err);
                    return;
                }
                throw err;
            }
        }

        /** Show the checkboxes. From the Threads half's `New thread`. @returns {void} */
        function enterSelectMode() {
            if (selectMode) return;
            selectMode = true;
            render();
            onSelectionChange();
        }

        /**
         * Hide them again, and forget who was picked. Cancel and a completed
         * creation both land here, so both exits leave the same state — a
         * selection that survived would be invisible next time and would build
         * the wrong thread.
         * @returns {void}
         */
        function exitSelectMode() {
            selectMode = false;
            selected.clear();
            render();
            onSelectionChange();
        }

        disposers.push(store.subscribe((s) => s.roster, render));
        disposers.push(store.subscribe((s) => s.rosterQuery, render));
        disposers.push(store.subscribe((s) => `${s.floorScope}|${s.currentFloorId}|${s.browseFloorId}`, render));
        disposers.push(store.subscribe((s) => s.needs, render));
        disposers.push(store.subscribe((s) => s.runtimePaused, render));
        disposers.push(store.subscribe((s) => s.conversationId, syncSeatAction));
        disposers.push(store.subscribe((s) => s.conversationKind, syncSeatAction));
        disposers.push(store.subscribe((s) => s.threads, syncSeatAction));

        render();

        return {
            element,
            selection: () => Array.from(selected),
            isSelecting: () => selectMode,
            enterSelectMode,
            exitSelectMode,

            /**
             * Record how the roster request went, and repaint.
             *
             * The rail owns the request and this owns the three states it can
             * land in, so a failed load shows an empty list saying so rather
             * than a skeleton that never resolves.
             *
             * @param {'loading'|'ready'|'error'} state
             * @returns {void}
             */
            setLoadState(state) {
                loadState = state;
                render();
            },

            /**
             * Drain every subscription this half created, and put the `⋯`'s
             * panel away: an open one would outlive the rail.
             * @returns {void}
             */
            destroy() {
                disposers.splice(0).forEach((off) => off());
                view.destroy();
            },
        };
    }

    return { createPeople };
})();
