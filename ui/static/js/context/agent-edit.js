/**
 * BossMod AI — Edit role: one agent's form, in one centred dialog.
 *
 * The entry point the desk's Edit role action calls, and the dialog around the
 * form rather than the form itself: which footer it gets, and where a save or
 * a delete leaves the operator. Building the form and saving through it is
 * context/agent-form-save.js, which this hosts and calls but does not
 * re-export — nothing outside reached it through here, and a second name for
 * one function is a seam that is not there. What it owns beyond the dialog is
 * the wiring the dock-era host did: a save closes the form and leaves the
 * operator where they were, and a delete falls the column back to the office.
 *
 * EDIT ONLY, and one step: the full form for an agent that exists. Creating
 * one is the Agents dialog's Add agent tab (context/agents-dialog.js over
 * context/agent-add-pane.js), which is where the template picker, the step
 * Back chevron and the create routing moved; this throws without the agent it
 * edits rather than quietly opening a create form with no picker in front.
 *
 * ONE AGENT FORM AT A TIME, across both dialogs that host one —
 * context/agent-dialog-slot.js. `#agent-form` is one id for the whole
 * document, so two live forms would let one dialog's primary submit the
 * other's draft. Both doors — the rail's Add agent menu and the desk's Edit
 * role — sit behind the modal scrim and its focus trap while either dialog is
 * up, so the slot is defence in depth rather than a flow anyone can walk. The
 * dialog is core/overlays.js's panel modal — one trap, one Esc, the title and
 * the dismissal pinned outside a body that scrolls.
 *
 * The footer is context/agent-dialog-footer.js's — the row itself, and the
 * state of the primary inside it. `Save as template` is in that row here too:
 * a role contract worth keeping is as likely to be one already in front of the
 * operator as one they are typing (context/agent-save-template.js).
 */
const BossModAgentEdit = (() => {
    const { h, clear } = BossModDom;
    const FOOTER = BossModAgentDialogFooter;
    const SLOT = BossModAgentDialogSlot;
    // The form's own wiring — building it, saving it, deleting through it — is
    // context/agent-form-save.js. Named here because this module hosts it.
    const { renderInline } = BossModAgentFormSave;

    const EDIT_TITLE = 'Edit role';
    // An edit that cannot render has no step to go back to, so its way out is
    // to be reopened. Not "refresh the page", which is what this said once.
    const FAILED_COPY = 'The agent editor failed to load.';
    const FAILED_CLOSE = 'Close this dialog and open it again to retry.';

    /**
     * Open an agent's role form as a centred dialog.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store. A delete writes the
     *   context column back to the office.
     * @param {object} deps.agent  The roster row to edit.
     * @param {() => void} [deps.onClosed]  Called once after the dialog closes,
     *   however it closed — a save, a delete, Cancel, or Esc. Not called at
     *   all when another agent dialog already holds the slot: nothing opened.
     * @returns {{ close: () => void }} So a host being torn down can take its
     *   dialog with it. When another agent dialog is already open, THAT
     *   dialog's handle — handing back the open one is the only answer that
     *   neither stacks two traps nor throws away a draft nobody asked to
     *   discard.
     * @throws {Error} When store or agent is missing. There is no create path
     *   here any more, so a missing agent is a caller that wanted the Agents
     *   dialog, and saying so beats opening a form that would create.
     */
    function openAgentModal(deps) {
        const { store, agent, onClosed } = deps || {};
        if (!store) throw new Error('[agent-edit] deps.store is required');
        if (!agent) {
            throw new Error('[agent-edit] deps.agent is required: creating an agent is '
                + 'the Agents dialog\'s (context/agents-dialog.js)');
        }
        const held = SLOT.current();
        if (held) return held;

        let destroyed = false;
        const formEl = h('div', { class: 'agent-form-host' });

        /**
         * The footer's `Save as template`: this agent's role contract into the
         * library, read off the CURRENT form at click time
         * (context/agent-save-template.js). No `onSaved`: nothing this dialog
         * shows lists the library, and only one agent dialog is open at a time.
         *
         * @returns {void}
         * @throws {Error} With no form on screen — the row withholds this
         *   action while one is building, so there should always be one.
         */
        function saveTemplate() {
            const form = formEl.querySelector('#agent-form');
            if (!form) throw new Error('[agent-edit] Save as template with no form');
            // Named at click time, like the add pane's: nothing in this
            // dialog's build path reaches it.
            const SAVE_TEMPLATE = BossModAgentSaveTemplate;
            void SAVE_TEMPLATE.open({
                form,
                defaultTitle: SAVE_TEMPLATE.defaultTitle(form),
                // An edit came off no library shelf, so it starts on the one
                // every saved template can go on.
                defaultCategory: SAVE_TEMPLATE.CUSTOM,
            });
        }

        // What the pinned row offers, and what its primary is allowed to say,
        // is one owner's — scoped to THIS dialog, so a build that outlives its
        // own cannot repaint the next one's button.
        const chrome = { creating: false, onSaveTemplate: saveTemplate };
        const handle = { close: () => modal.close() };

        const modal = BossModOverlays.createModal({
            title: EDIT_TITLE,
            // The panel size, not a second modal: same trap, same Esc, same
            // focus restoration, more room and a body that scrolls.
            body: formEl,
            size: 'panel',
            actions: FOOTER.actionsFor('form', chrome),
            onClose: () => {
                destroyed = true;
                SLOT.release(handle);
                if (onClosed) onClosed();
            },
        });
        // Taken the moment the dialog exists, so nothing between here and the
        // end of this function can leave an open form the slot does not know.
        SLOT.claim(handle);

        const footer = FOOTER.createFooter(modal, chrome);
        const { primary } = footer;

        /**
         * A save landed. `savedAgent` is absent for the recovery tools (clear
         * chat, reset runtime), which save nothing about the agent itself.
         * Either way the dialog closes, and editing leaves the operator where
         * they were — only a create moves the conversation.
         *
         * @returns {void}
         */
        function onSave() {
            if (destroyed) return;
            modal.close();
        }

        function onDelete() {
            if (destroyed) return;
            store.setState({ contextMode: 'office', deskAgentId: null, deskPath: null });
            modal.close();
        }

        /**
         * A form that cannot render is the operator's to see, not the console's
         * alone.
         *
         * The FOOTER is repaired with the body — the primary submits
         * `#agent-form` by id and the host no longer holds one, so a row left
         * as it was would offer a button that does nothing at all and says
         * nothing about why. What that leaves is
         * context/agent-dialog-footer.js's `recovery()`, which puts the
         * keyboard on the dismissal: an edit has nothing to go back to.
         *
         * @param {Error} err
         * @returns {void}
         */
        function failed(err) {
            console.error('[agent-edit] the agent form failed to render', err);
            if (destroyed) return;
            clear(formEl);
            formEl.append(h('p', { class: 'context-error', role: 'alert' },
                `${FAILED_COPY} ${FAILED_CLOSE}`));
            footer.recovery();
        }

        void renderInline({ container: formEl, agent, primary, onSave, onDelete })
            .then((landed) => {
                if (!landed || destroyed) return;
                BossModAgentFloor.mount({ container: formEl, agent, store });
            })
            .catch(failed);

        return handle;
    }

    return { openAgentModal };
})();
