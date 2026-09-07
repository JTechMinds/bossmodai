/**
 * BossMod AI — hire and edit, hosted in the context column.
 *
 * A thin host over AgentPanel.renderInline. agent-panel.js is 824 lines and
 * already carries the form, its duplicate-name warning, its in-flight submit
 * gate, and the recovery tools — all covered by test_hire_ui_poke.py. Rewriting
 * a working, well-tested hire form inside a phase about needs and context would
 * be scope creep, not cleanup; spec 2 renames it to context/agent-edit.js
 * proper in Phase 4's "split the remainder" work.
 *
 * What this module owns is the wiring the dock-era host used to do: after a
 * successful CREATE the form closes and the new agent becomes the open
 * conversation, and after a delete the column falls back to the office.
 */
const BossModAgentEdit = (() => {
    const { h, clear } = BossModDom;

    /**
     * Host the agent form.
     *
     * @param {object} deps
     * @param {object}   deps.store    Application store.
     * @param {object|null} deps.agent The roster row to edit, or null to hire.
     * @param {() => void} deps.onDone Called after a save or a delete has been
     *   applied, so the host can put its own view back.
     * @param {() => void} deps.onCancel  Called when the operator backs out.
     * @returns {{ element: HTMLElement, destroy: () => void }}
     * @throws {Error} When store, onDone, or onCancel is missing.
     */
    function createAgentEdit(deps) {
        const { store, agent, onDone, onCancel } = deps || {};
        if (!store) throw new Error('[agent-edit] deps.store is required');
        if (typeof onDone !== 'function') throw new Error('[agent-edit] deps.onDone is required');
        if (typeof onCancel !== 'function') throw new Error('[agent-edit] deps.onCancel is required');

        // Captured now: the panel reports a save without saying which kind it
        // was, and only a create should take the operator to a new
        // conversation. Editing must leave them where they were.
        const wasCreating = !agent;
        let destroyed = false;

        const formEl = h('div', { class: 'agent-edit-form' });
        const element = h('section', { class: 'agent-edit' },
            h('button', { class: 'context-link', type: 'button', onclick: () => onCancel() },
                wasCreating ? '← Cancel' : '← Back to the desk'),
            h('h2', { class: 'context-title' }, wasCreating ? 'Hire someone' : 'Edit role'),
            formEl);

        /**
         * A save landed. `savedAgent` is absent for the recovery tools (clear
         * chat, reset runtime), which save nothing about the agent itself.
         *
         * @param {object} [savedAgent]
         * @returns {void}
         */
        function onSave(savedAgent) {
            if (destroyed) return;
            if (savedAgent && wasCreating) {
                // Preserves the dock-era behaviour: creating someone opens
                // their conversation and their desk, so the operator lands
                // somewhere rather than back at a blank form.
                store.setState({
                    conversationId: savedAgent.id,
                    conversationKind: 'agent',
                    contextMode: 'desk',
                    deskAgentId: savedAgent.id,
                    deskPath: null,
                });
            }
            onDone();
        }

        function onDelete() {
            if (destroyed) return;
            store.setState({ contextMode: 'office', deskAgentId: null, deskPath: null });
            onDone();
        }

        // renderInline resolves after the form is in the DOM; a failure is the
        // operator's to see, not the console's alone.
        void AgentPanel.renderInline(formEl, agent || null, onSave, onDelete)
            .catch((err) => {
                console.error('[agent-edit] the agent form failed to render', err);
                if (destroyed) return;
                clear(formEl);
                formEl.append(h('p', { class: 'context-error', role: 'alert' },
                    'The agent editor failed to load. Refresh the page and try again.'));
            });

        return {
            element,

            /**
             * The form holds no store or bus subscriptions; this only stops a
             * late render callback from writing into a detached element.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
            },
        };
    }

    return { createAgentEdit };
})();
