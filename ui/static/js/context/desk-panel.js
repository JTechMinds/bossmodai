/**
 * BossMod AI — one agent's desk in the context column.
 *
 * Profile, Tasks, Files, Notes, and a footer of actions, in that order
 * (spec 7). It composes rather than renders: Tasks is context/desk-tasks.js,
 * Files is context/desk-files.js, Notes is context/desk-notes.js, and the
 * footer is context/desk-actions.js, each owning its own request and its own
 * load generation.
 *
 * THE SECTION VOCABULARY IS THIS FILE'S. The panel stacked eight blocks at one
 * visual level and each module authored its own header, so nothing said which
 * block was more important than which — the operator's words were "looks like
 * it was slapped together". section() below is the one header: a label, an
 * optional right-aligned action, and the content under it. A module renders
 * content and nothing else, so a header cannot drift one module at a time and
 * an action cannot end up floating under the list it belongs to.
 *
 * Three levels now, not one. The IDENTITY block answers "who is this" and is
 * closed by a rule. The SECTIONS are the work. The FOOTER is pinned to the
 * bottom with the quiet key/value facts and the actions on the agent.
 *
 * The done/fail contract is a DISCLOSURE. It is the agent's role contract —
 * reference material read once, not a standing alert — and at full amber
 * volume above the task list it outranked the task it qualifies. Nothing is
 * removed: it is one click away, with a summary that says what is behind it.
 * Phase 2B had it standing in for Notes, which had no data behind it; Phase 4
 * gave Notes the workspace it was always meant to read (spec 7).
 *
 * Editing the role opens the one centred dialog (context/agent-edit.js) over
 * this panel, which stays mounted underneath — so the operator comes back to
 * the desk they opened rather than having to find it again.
 */
const BossModDeskPanel = (() => {
    const { h, clear } = BossModDom;

    const NO_SPECIALTY = 'No specialty';
    const DONE_BAR_TITLE = 'What done looks like for this agent:';
    const NO_DONE_BAR = 'No done/fail bar set for this agent yet. Edit the role to add one.';
    /** The summary the operator opens the contract from. */
    const CONTRACT_SUMMARY = 'Done/fail contract';

    /**
     * One labelled section: a header row, an optional right-aligned action,
     * and the content under it.
     *
     * The action lives ON the header rather than under the list, which is what
     * makes it belong to the section rather than float between two of them.
     *
     * @param {string} title
     * @param {{label: string, onSelect: () => void}|null} action
     * @param {HTMLElement} content
     * @returns {HTMLElement}
     */
    function section(title, action, content) {
        return h('section', { class: 'desk-section' },
            h('div', { class: 'desk-section-head' },
                h('h3', { class: 'desk-section-title' }, title),
                action
                    ? h('button', {
                        class: 'btn-link desk-section-action',
                        type: 'button',
                        onclick: action.onSelect,
                    }, action.label)
                    : null),
            content);
    }

    /**
     * Build the desk panel.
     *
     * @param {object} deps
     * @param {object}   deps.store    Application store; the roster names the agent.
     * @param {object}   deps.bus      Topic bus, handed to the file browser.
     * @param {Function} deps.api      Authenticated fetch helper.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @param {string}   deps.agentId
     * @param {() => void} deps.onBack Returns the column to the office summary.
     * @returns {{ element: HTMLElement, destroy: () => void }}
     * @throws {Error} When any dependency is missing.
     */
    function createDeskPanel(deps) {
        const { store, bus, api, navigate, agentId, onBack } = deps || {};
        if (!store) throw new Error('[desk-panel] deps.store is required');
        if (!bus) throw new Error('[desk-panel] deps.bus is required');
        if (typeof api !== 'function') throw new Error('[desk-panel] deps.api is required');
        if (typeof navigate !== 'function') throw new Error('[desk-panel] deps.navigate is required');
        if (!agentId) throw new Error('[desk-panel] deps.agentId is required');
        if (typeof onBack !== 'function') throw new Error('[desk-panel] deps.onBack is required');

        const disposers = [];

        const profileEl = h('section', { class: 'desk-profile' });
        // Built once and refilled, so opening the disclosure survives a roster
        // tick: a <details> replaced on every world_update would snap shut
        // under an operator who had just opened it.
        const contractEl = h('p', { class: 'desk-bar' });
        const tasks = BossModDeskTasks.createDeskTasks({ api, agentId });
        const actions = BossModDeskActions.createDeskActions({
            store, api, navigate, agentId, onEdit: () => openEdit(),
        });
        const files = BossModDeskFiles.createDeskFiles({ api, bus, agentId });
        // A folder inside /me/notes is the browser's job, not a second one.
        const notes = BossModDeskNotes.createDeskNotes({
            api, agentId, onOpenFolder: (path) => { void files.open(path); },
        });
        /** The open role dialog, or null. One at a time. */
        let edit = null;
        /** Set before teardown closes the dialog, so its onClosed does nothing. */
        let destroyed = false;

        const bodyEl = h('div', { class: 'desk-body' },
            h('button', { class: 'btn btn-sm context-link', type: 'button', onclick: () => onBack() },
                '← The office'),
            profileEl,
            h('details', { class: 'desk-contract' },
                h('summary', { class: 'desk-contract-summary' }, CONTRACT_SUMMARY),
                contractEl),
            section('Tasks', {
                label: 'See all',
                // The panel already takes `navigate`, and where "all of this
                // agent's tasks" lives is a panel-level fact rather than
                // something the list that loads three of them should know.
                onSelect: () => navigate('board', { agentFilter: agentId }),
            }, tasks.element),
            section('Files', null, files.element),
            section('Notes', null, notes.element),
            actions.element);

        const element = h('section', { class: 'desk-panel' }, bodyEl);

        /** The roster row for this agent, or null while the roster is loading. */
        function agent() {
            return store.getState().roster.find((item) => item && item.id === agentId) || null;
        }

        // ─── Profile ───

        function renderProfile() {
            const who = agent();
            clear(profileEl);
            clear(contractEl);
            if (!who) {
                profileEl.append(h('p', { class: 'context-skeleton' }, 'Loading this desk…'));
                contractEl.append(NO_DONE_BAR);
                return;
            }
            const bar = who.done_fail_bar ? String(who.done_fail_bar).trim() : '';
            // ONE block that answers "who is this": face, name, specialty,
            // description, and the state pill, closed by a rule. Built through
            // h(), which drops a null child; Element.append does not, and an
            // agent with no description has one.
            profileEl.append(h('div', { class: 'desk-profile-body' },
                h('div', { class: 'desk-profile-head' },
                    // Decorative: the name sits beside it, so a second
                    // announcement of the same person would be noise.
                    BossModAvatar.create({ name: who.name, color: who.color, size: 'lg' }),
                    h('div', {},
                        h('h2', { class: 'desk-name' }, String(who.name)),
                        // An agent with no role has no specialty; saying so is
                        // more useful than an empty line.
                        h('p', { class: 'desk-role' }, String(who.role || NO_SPECIALTY)))),
                who.description
                    ? h('p', { class: 'desk-about' }, String(who.description))
                    : null,
                h('span', { class: 'desk-state-pill' },
                    BossModAgentStatus.getStatusLabel(who.status, who.currentActivityKind))));
            // The contract itself, inside the disclosure above the tasks it
            // qualifies. Unchanged copy; only its volume changed.
            contractEl.append(bar ? `${DONE_BAR_TITLE} ${bar}` : NO_DONE_BAR);
        }

        /**
         * Open the role form over the desk.
         *
         * The desk stays mounted underneath: the operator comes back to the
         * desk they opened rather than being sent somewhere else and having to
         * find it again. One dialog at a time — a second Edit click while one
         * is open would stack two forms over the same agent.
         *
         * @returns {void}
         */
        function openEdit() {
            if (edit) return;
            edit = BossModAgentEdit.openAgentModal({
                store,
                agent: agent(),
                onClosed: closeEdit,
            });
        }

        function closeEdit() {
            edit = null;
            // destroy() closes the dialog on its way out; refreshing three
            // sections of a panel that is being unmounted would fire requests
            // whose answers nothing will paint.
            if (destroyed) return;
            // The saved role, description, and done bar arrive with the next
            // world_update; repaint from what the store holds now regardless.
            renderProfile();
            void actions.refresh();
            // A role edit can rewrite the workspace; re-read rather than trust
            // what was on screen before the form opened.
            void notes.refresh();
        }

        disposers.push(store.subscribe((s) => s.roster, () => { renderProfile(); }));
        // "Open in Desk" on a note for the agent whose desk is ALREADY open
        // changes only the path, so the column never rebuilds this panel and
        // nothing else would move the browser to the file.
        disposers.push(store.subscribe((s) => s.deskPath, (path) => {
            if (path) void files.open(path);
        }));

        renderProfile();
        // A note's "Open in Desk" puts the path in the store; without one, the
        // desk root is where a desk opens.
        void files.open(store.getState().deskPath || '/me');

        return {
            element,

            /**
             * Drain this panel and both of its sections.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                disposers.splice(0).forEach((off) => off());
                actions.destroy();
                // A dialog outliving the desk that opened it would keep
                // writing into a store the operator has navigated away from.
                if (edit) edit.close();
                edit = null;
                tasks.destroy();
                files.destroy();
                notes.destroy();
            },
        };
    }

    return { createDeskPanel };
})();
