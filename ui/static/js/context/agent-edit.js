/**
 * BossMod AI — hire and edit, hosted in the context column.
 *
 * Phase 4 finished the split spec 2 planned: agent-panel.js is gone and its
 * 825 lines are eight modules under context/. What lands here is the entry
 * point the column already called plus the orchestration `renderInline` always
 * did — the in-flight submit gate and the save and delete paths. The form
 * itself is context/agent-form.js, its fields are context/agent-fields.js,
 * context/agent-form-fields.js and context/agent-form-advanced.js, its
 * per-field behaviours are context/agent-form-bindings.js, what the server is
 * told is context/agent-submit.js, the feedback line and the two destructive
 * tools are context/agent-recovery.js, and every request is
 * context/agent-api.js.
 *
 * What this module owns beyond that is the wiring the dock-era host used to
 * do: after a successful CREATE the form closes and the new agent becomes the
 * open conversation, and after a delete the column falls back to the office.
 */
const BossModAgentEdit = (() => {
    const { h, clear } = BossModDom;

    // ─── State ───
    let currentAgentId = null;
    let isCreating = false;

    /**
     * Render the form into `container` and own everything that happens after.
     *
     * @param {HTMLElement} container
     * @param {object|null} agent      null to hire.
     * @param {(saved?: object) => void} onSave    Called with the saved agent
     *   after a create/update, and with nothing after a recovery action, which
     *   saves nothing about the agent itself.
     * @param {() => void} onDelete
     * @returns {Promise<void>} Rejects only if the form itself cannot render;
     *   a failed SAVE becomes the in-form feedback and the draft is kept.
     */
    async function renderInline(container, agent, onSave, onDelete) {
        isCreating = !agent;
        currentAgentId = agent?.id || null;
        if (agent?.id) {
            const full = await BossModAgentApi.fetchAgent(agent.id);
            if (full) {
                agent = { ...agent, ...full };
            }
        }

        await BossModAgentForm.buildFormHTML(container, agent);

        const form = container.querySelector('#agent-form');
        const deleteBtn = container.querySelector('#btn-delete-agent');

        // Fetch connections for submit resolution
        let connections = [];
        try {
            const res = await apiFetch('/api/connections');
            connections = await res.json();
        } catch { /* empty */ }

        const RECOVERY = BossModAgentRecovery;
        const feedback = RECOVERY.createFeedback(form);
        const say = (tone, text) => feedback.say(tone, text);
        RECOVERY.bindRecoveryTools({
            container,
            agentId: () => currentAgentId,
            feedback,
            onSave: () => { if (onSave) onSave(); },
        });

        const submitBtn = form.querySelector('#agent-form-submit');
        const hireSubmit = BossModGates.createInFlightGate();

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (hireSubmit.busy()) return;

            await hireSubmit.run(async () => {
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.textContent = isCreating ? 'Creating…' : 'Saving…';
                }
                say('busy', 'Saving...');

                let savedAgent = null;
                try {
                    const { agentData, promptHistoryPolicy } = await BossModAgentSubmit.buildSubmitData(form, connections);
                    if (isCreating) {
                        savedAgent = await BossModAgentApi.apiCreateAgent(agentData);
                    } else {
                        savedAgent = await BossModAgentApi.apiUpdateAgent(currentAgentId, agentData);
                    }

                    try {
                        await BossModAgentApi.apiUpdatePromptHistoryPolicy(savedAgent.id, promptHistoryPolicy);
                    } catch (policyErr) {
                        console.error('[agent-edit] Prompt history policy save failed:', policyErr);
                        say('warn', 'Agent saved, but AI history settings failed to save.');
                        if (onSave) onSave(savedAgent);
                        return;
                    }

                    say('ok', 'Saved successfully');
                    setTimeout(() => feedback.hide(), 3000);
                    if (onSave) onSave(savedAgent);
                } catch (err) {
                    console.error('[agent-edit] Save failed:', err);
                    say('bad', err?.message || 'Save failed — check console for details');
                } finally {
                    if (submitBtn && (!isCreating || !savedAgent)) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = isCreating ? 'Create Agent' : 'Save Changes';
                    }
                }
            });
        });

        if (deleteBtn) {
            deleteBtn.addEventListener('click', () => {
                if (!currentAgentId) return;
                RECOVERY.confirmDestructive(
                    'Delete this agent?', 'This cannot be undone.', 'Delete agent',
                    () => {
                        void BossModAgentApi.apiDeleteAgent(currentAgentId)
                            .then(() => { if (onDelete) onDelete(); })
                            .catch((err) => {
                                console.error('[agent-edit] Delete failed:', err);
                                say('bad', err?.message || 'Delete failed — check console for details');
                            });
                    });
            });
        }
    }

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
        void renderInline(formEl, agent || null, onSave, onDelete)
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

    return { createAgentEdit, renderInline };
})();
