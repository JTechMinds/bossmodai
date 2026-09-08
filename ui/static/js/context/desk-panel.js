/**
 * BossMod AI — one agent's desk in the context column.
 *
 * Profile, Tasks, Files, Notes, and a footer of actions, in that order
 * (spec 7). It composes rather than renders: Tasks is context/desk-tasks.js,
 * Files is context/desk-files.js, Notes is context/desk-notes.js, and the
 * footer is context/desk-actions.js, each owning its own request and its own
 * load generation.
 *
 * The profile carries the agent's done/fail bar. Phase 2B had it standing in
 * for Notes, which had no data behind it; Phase 4 gave Notes the workspace it
 * was always meant to read (spec 7), so the bar moved to where it belongs
 * rather than being deleted along with the stand-in.
 *
 * Editing the role swaps this panel for the hosted form in place, so the
 * operator comes back to the desk they opened rather than having to find it
 * again.
 */
const BossModDeskPanel = (() => {
    const { h, clear } = BossModDom;

    const NO_SPECIALTY = 'No specialty';
    const DONE_BAR_TITLE = 'What done looks like for this agent:';
    const NO_DONE_BAR = 'No done/fail bar set for this agent yet. Edit the role to add one.';

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
        const tasks = BossModDeskTasks.createDeskTasks({ api, agentId, navigate });
        const actions = BossModDeskActions.createDeskActions({
            store, api, navigate, agentId, onEdit: () => openEdit(),
        });
        const files = BossModDeskFiles.createDeskFiles({ api, bus, agentId });
        // A folder inside /me/notes is the browser's job, not a second one.
        const notes = BossModDeskNotes.createDeskNotes({
            api, agentId, onOpenFolder: (path) => { void files.open(path); },
        });
        /** The hosted role form, while the operator is editing. */
        let edit = null;

        const bodyEl = h('div', { class: 'desk-body' },
            h('button', { class: 'context-link', type: 'button', onclick: () => onBack() },
                '← The office'),
            profileEl,
            tasks.element,
            h('section', { class: 'desk-section' },
                h('p', { class: 'desk-section-title' }, 'Files'),
                files.element),
            notes.element,
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
            if (!who) {
                profileEl.append(h('p', { class: 'context-skeleton' }, 'Loading this desk…'));
                return;
            }
            const initial = String(who.name || '?').trim().charAt(0).toUpperCase() || '?';
            const bar = who.done_fail_bar ? String(who.done_fail_bar).trim() : '';
            // Built through h(), which drops a null child; Element.append does
            // not, and an agent with no description has one.
            profileEl.append(h('div', { class: 'desk-profile-body' },
                h('div', { class: 'desk-profile-head' },
                    h('span', {
                        class: 'desk-avatar',
                        'aria-hidden': 'true',
                        style: `background:${who.color}`,
                    }, initial),
                    h('div', {},
                        h('h2', { class: 'desk-name' }, String(who.name)),
                        // An agent with no role has no specialty; saying so is
                        // more useful than an empty line.
                        h('p', { class: 'desk-role' }, String(who.role || NO_SPECIALTY)))),
                who.description
                    ? h('p', { class: 'desk-about' }, String(who.description))
                    : null,
                h('span', { class: 'desk-state-pill' },
                    BossModAgentStatus.getStatusLabel(who.status, who.currentActivityKind)),
                // The agent's role contract belongs with the rest of who they
                // are. It sat in the Notes slot only while Notes had no data
                // behind it (spec 7); now that Notes reads the workspace, the
                // bar comes home to the profile rather than being dropped.
                h('p', { class: 'desk-bar' }, bar
                    ? `${DONE_BAR_TITLE} ${bar}`
                    : NO_DONE_BAR)));
        }

        /**
         * Swap the desk for the role form, and back when it is done.
         *
         * Editing happens in place: the operator stays on the desk they opened
         * rather than being sent somewhere else and having to find it again.
         *
         * @returns {void}
         */
        function openEdit() {
            if (edit) return;
            edit = BossModAgentEdit.createAgentEdit({
                store,
                agent: agent(),
                onDone: closeEdit,
                onCancel: closeEdit,
            });
            clear(element);
            element.append(edit.element);
        }

        function closeEdit() {
            if (!edit) return;
            edit.destroy();
            edit = null;
            clear(element);
            element.append(bodyEl);
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
                disposers.splice(0).forEach((off) => off());
                actions.destroy();
                if (edit) edit.destroy();
                edit = null;
                tasks.destroy();
                files.destroy();
                notes.destroy();
            },
        };
    }

    return { createDeskPanel };
})();
