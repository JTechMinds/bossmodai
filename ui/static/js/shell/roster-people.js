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
 */
const BossModRosterPeople = (() => {
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
        const { store, onOpenConversation, onOpenDesk, onSelectionChange } = deps || {};
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
        const element = h('section', { class: 'roster-section' },
            h('h2', { class: 'roster-section-title' }, 'People'),
            list);

        function agentsWithNeeds() {
            return new Set(store.getState().needs.map((need) => need.agentId));
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
         * @returns {HTMLElement}
         */
        function personRow(agent, paused, needy) {
            const selecting = selectMode;
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
                    h('span', { class: 'roster-name' }, agent.name),
                    h('span', { class: 'roster-status' }, statusLine(agent, paused, needy))),
                // A sibling of the name button: inside the text column the
                // dot pushed the status line around.
                needy.has(agent.id)
                    ? h('span', { class: 'roster-need-dot', 'aria-hidden': 'true' })
                    : null);
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
            const roster = store.getState().roster;
            if (roster.length === 0) {
                list.append(h('li', { class: 'roster-empty' }, 'No one is hired yet.'));
                return;
            }

            const query = String(store.getState().rosterQuery).trim().toLowerCase();
            const visible = roster.filter((agent) => matches(agent, query));
            if (visible.length === 0) {
                list.append(h('li', { class: 'roster-empty' }, 'No one matches that search.'));
                return;
            }

            const paused = store.getState().runtimePaused === true;
            const needy = agentsWithNeeds();
            visible.forEach((agent) => list.append(personRow(agent, paused, needy)));
            lucide.createIcons();
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
        disposers.push(store.subscribe((s) => s.needs, render));
        disposers.push(store.subscribe((s) => s.runtimePaused, render));

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
             * Drain every subscription this half created.
             * @returns {void}
             */
            destroy() {
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createPeople };
})();
