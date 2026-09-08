/**
 * BossMod AI — hire and edit, in one centred dialog.
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
 *
 * ONE DIALOG AT A TIME, and its state is per render. Which agent the form is
 * for lived on the module while the column could only ever host one form; the
 * rail's Hire row is reachable from anywhere now, so a second dialog would
 * have rewritten the first one's identity and turned an edit into a create.
 *
 * BOTH FLOWS ARE THE SAME DIALOG. Hiring used to be a context-column mode
 * (`contextMode: 'desk'` with no `deskAgentId`) and editing swapped the desk
 * panel out in place, so one form had two hosts, two ways in, and one shared
 * column ~320px wide to render a two-column connection matrix in. It is now
 * core/overlays.js's wide modal — centred in the viewport, wide enough for the
 * form, with the title and the dismissal pinned outside a body that scrolls.
 *
 * The dialog's action row holds the dismissal only. Save and Delete are the
 * FORM's own controls: `#agent-form-submit` is a `type="submit"` inside the
 * `<form>`, which is what makes Enter in a text field submit it, and a button
 * moved out of its form stops submitting it.
 */
const BossModAgentEdit = (() => {
    const { h, clear } = BossModDom;

    const HIRE_TITLE = 'Hire someone';
    const EDIT_TITLE = 'Edit role';

    /**
     * The open dialog, or null. One at a time: two stacked wide modals fight
     * over Escape and the focus trap, and the second one to open would be the
     * only one the keyboard could reach.
     */
    let openDialog = null;

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
        // Per render, not per module. Which agent this form is for, and whether
        // it creates or updates, belong to THIS form: as module state a second
        // form would rewrite the first one's identity, and an edit already open
        // would start saving as a create.
        let isCreating = !agent;
        let currentAgentId = agent?.id || null;
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
     * Open the agent form as a centred dialog.
     *
     * @param {object} deps
     * @param {object} deps.store        Application store.
     * @param {object|null} [deps.agent] The roster row to edit, or null/absent
     *   to hire. Both open the same dialog; only the title and whether the
     *   form offers Delete differ.
     * @param {() => void} [deps.onClosed]  Called once after the dialog
     *   closes, however it closed — a save, a delete, Cancel, or Esc. The desk
     *   uses it to repaint from what the save changed; the rail needs nothing.
     * @returns {{ close: () => void }} So a host that is being torn down can
     *   take its dialog with it.
     * @throws {Error} When store is missing. The form writes the new agent
     *   into the store on a create, so a dialog without one would save an
     *   agent the operator is then never shown.
     */
    function openAgentModal(deps) {
        const { store, agent, onClosed } = deps || {};
        if (!store) throw new Error('[agent-edit] deps.store is required');
        // The operator already has one open — the rail's Hire row is reachable
        // while a desk's Edit dialog is up. Handing back the open one is the
        // only answer that does not either stack two traps or throw away a
        // draft nobody asked to discard.
        if (openDialog) return openDialog;

        // Captured now: the form reports a save without saying which kind it
        // was, and only a create should take the operator to a new
        // conversation. Editing must leave them where they were.
        const wasCreating = !agent;
        let destroyed = false;

        const formEl = h('div', { class: 'agent-form-host' });
        const modal = BossModOverlays.createModal({
            title: wasCreating ? HIRE_TITLE : EDIT_TITLE,
            body: formEl,
            // The variant, not a second modal: same trap, same Esc, same
            // focus restoration, more room and a body that scrolls.
            size: 'wide',
            // The one dismissal, pinned outside the scroll. It is the last
            // action, so it holds focus on open and Esc agrees with it.
            actions: [{ label: 'Cancel', tone: 'quiet' }],
            onClose: () => {
                destroyed = true;
                openDialog = null;
                if (onClosed) onClosed();
            },
        });

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
            modal.close();
        }

        function onDelete() {
            if (destroyed) return;
            store.setState({ contextMode: 'office', deskAgentId: null, deskPath: null });
            modal.close();
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

        openDialog = { close: () => modal.close() };
        return openDialog;
    }

    return { openAgentModal, renderInline };
})();
