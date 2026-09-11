/**
 * BossMod AI — create and edit, in one centred dialog.
 *
 * The entry point the column already called, and the dialog around the form
 * rather than the form itself: what the operator is asked FIRST, which footer
 * each step gets, and where a save or a delete leaves them. Building the form
 * and saving through it is context/agent-form-save.js, which this hosts and
 * calls but does not re-export — nothing outside reached it through here, and
 * a second name for one function is a seam that is not there. The marketplace
 * is js/marketplace/. What it owns beyond the dialog is the wiring the
 * dock-era host did: a successful CREATE closes the form and opens the new
 * agent's conversation and desk, and a delete falls the column back to the
 * office.
 *
 * ONE DIALOG AT A TIME, and its state is per render. The rail's Add agent row
 * is reachable while a desk's Edit dialog is up, so a second dialog would
 * rewrite the first one's identity and turn an edit into a create. Both flows
 * are core/overlays.js's wide modal — one trap, one Esc, the title and the
 * dismissal pinned outside a body that scrolls.
 *
 * CREATE IS TWO STEPS OVER ONE BODY: the template picker, then the form,
 * swapped with `hidden`. The form is built on the first pick and rebuilt only
 * when the picked template CHANGES, so a draft survives Back and re-picking
 * the same cell and no "discard your draft?" prompt has to exist. A second
 * stacked dialog for step two was rejected for the reason above.
 *
 * The footer is PER STEP, and it is context/agent-dialog-footer.js's — the row
 * itself, the Back that has to outlive a click, and the state of the primary
 * inside it. This says which step is on screen; that says what the row holds.
 *
 * `Browse marketplace` CLOSES this dialog and the takeover reopens it on step
 * one. Never both at once — two focus traps over one task is exactly what the
 * one-dialog guard exists to prevent.
 */
const BossModAgentEdit = (() => {
    const { h, clear } = BossModDom;
    const HYDRATE = BossModAgentFormHydrate;
    const TEMPLATE = BossModAgentFormTemplate;
    const FOOTER = BossModAgentDialogFooter;
    // The form's own wiring — building it, saving it, deleting through it — is
    // context/agent-form-save.js. Named here because this module hosts it.
    const { renderInline } = BossModAgentFormSave;

    const HIRE_TITLE = 'Add agent';
    /** The back chevron's accessible name, and its tooltip. */
    const BACK_LABEL = 'Back to the template picker';
    const EDIT_TITLE = 'Edit role';
    // A failed render is recoverable in both flows, and differently in each:
    // step one is still mounted with its list, and an edit has to be reopened.
    // Neither is "refresh the page", which is what this said before step two.
    const FAILED_COPY = 'The agent editor failed to load.';
    const FAILED_BACK = 'Go back and pick again.';
    const FAILED_CLOSE = 'Close this dialog and open it again to retry.';

    /** The open dialog, or null. One at a time — see the header. */
    let openDialog = null;

    /**
     * Open the agent form as a centred dialog.
     *
     * @param {object} deps
     * @param {object} deps.store        Application store.
     * @param {object|null} [deps.agent] The roster row to edit, or null/absent
     *   to create. Editing is one step and the full form; creating is the
     *   picker first.
     * @param {() => void} [deps.onClosed]  Called once after the dialog closes,
     *   however it closed — a save, a delete, Cancel, or Esc. It does NOT fire
     *   when the dialog stands down for the marketplace: the flow has not ended
     *   there, and the dialog the takeover reopens carries the same callback.
     * @returns {{ close: () => void }} So a host being torn down can take its
     *   dialog with it.
     * @throws {Error} When store is missing. The form writes the new agent into
     *   the store on a create, so a dialog without one would save an agent the
     *   operator is then never shown.
     */
    function openAgentModal(deps) {
        const { store, agent, onClosed } = deps || {};
        if (!store) throw new Error('[agent-edit] deps.store is required');
        // Handing back the open one is the only answer that neither stacks two
        // traps nor throws away a draft nobody asked to discard.
        if (openDialog) return openDialog;

        // Captured now: the form reports a save without saying which kind it
        // was, and only a create should take the operator to a new
        // conversation. Editing must leave them where they were.
        const wasCreating = !agent;
        let destroyed = false;
        /** Which template the form on screen holds; `null` is Blank, and the
         *  sentinel is "no form built yet". */
        let builtFor;
        let step = null;
        // Set by Browse marketplace so the takeover opens from onClose, once
        // this dialog has released its trap and put focus back. Opening it from
        // the action itself would mount a second trap over a live one.
        let browsing = false;

        const formEl = h('div', { class: 'agent-form-host' });
        let picker = null;
        let body = formEl;
        /** The title row's Back, on a create. Null on an edit, which has one
         *  step and nothing to go back to. */
        let backBtn = null;
        if (wasCreating) {
            picker = BossModAgentTemplatePicker.createPicker({
                onPick: (template) => { void pickTemplate(template).catch(failed); },
                // Still handed over: the picker's EMPTY state carries its own
                // door, in the middle of the panel where a first-run operator
                // with nothing installed is actually looking. What left the
                // picker is the header button beside the filter.
                onBrowse: () => browse(),
            });
            // ON THE TITLE ROW — `‹ Add agent` reads as one heading. It spent a
            // round as a bordered square floating in the band between the title
            // and the first card, aligned to neither and filling nothing. The
            // row is also OUTSIDE the body, so a build that fails and empties
            // the form host cannot take the way back with it.
            backBtn = h('button', {
                class: 'btn btn-sm step-back', type: 'button', id: 'agent-add-back',
                'aria-label': BACK_LABEL, 'data-tooltip': BACK_LABEL,
                onclick: () => showStep('picker'),
            }, h('i', { 'data-lucide': 'chevron-left', 'aria-hidden': 'true' }));
            backBtn.hidden = true;
            formEl.hidden = true;
            body = h('div', { class: 'agent-add-body' }, picker.element, formEl);
        }

        // What the pinned row offers, and what its primary is allowed to say,
        // is one owner's — scoped to THIS dialog, so a build that outlives its
        // own cannot repaint the next one's button.
        //
        const chrome = {
            creating: wasCreating,
            onBrowse: () => browse(),
        };

        const modal = BossModOverlays.createModal({
            title: wasCreating ? HIRE_TITLE : EDIT_TITLE,
            // The variant, not a second modal: same trap, same Esc, same focus
            // restoration, more room and a body that scrolls.
            body,
            // `‹ Add agent`: the chevron sits on the title row, not in the
            // body and not in the footer.
            lead: backBtn,
            size: 'wide',
            actions: FOOTER.actionsFor(wasCreating ? 'picker' : 'form', chrome),
            onClose: () => {
                destroyed = true;
                openDialog = null;
                if (browsing) {
                    browsing = false;
                    BossModMarketplace.open({
                        onClosed: () => { openAgentModal({ store, agent, onClosed }); },
                    });
                    return;
                }
                if (onClosed) onClosed();
            },
        });

        // The chevron is a lucide placeholder until the panel is mounted, and
        // createModal has just mounted it. Scoped to this panel, never the
        // document: painting wider would rebuild every icon in the shell.
        if (backBtn) BossModIcons.paint(modal.element, 'agent-edit.back');

        const footer = FOOTER.createFooter(modal, chrome);
        const { primary } = footer;

        /** Stand down for the takeover. close() is idempotent, so the action
         *  row's own close after this one is a no-op. */
        function browse() {
            browsing = true;
            modal.close();
        }

        /**
         * Swap between the picker and the form.
         *
         * The footer rebuilds in place, destroying whichever button held focus
         * — in BOTH directions — and createModal only places focus on mount.
         * So the swap places it again: the Find box on the way back, and the
         * Name input once its caller has built the form.
         *
         * @param {'picker'|'form'} next
         * @returns {void}
         */
        function showStep(next) {
            if (step === next) return;
            step = next;
            picker.element.hidden = next === 'form';
            formEl.hidden = next !== 'form';
            // Step one has nowhere to go back TO, so the chevron is not drawn
            // there — a live control that does nothing is worse than no control.
            backBtn.hidden = next !== 'form';
            footer.show(next);
            if (next !== 'form') picker.focus();
        }

        /**
         * A cell was picked. The form is rebuilt only for a DIFFERENT pick, so
         * Back and re-picking the same cell keep the draft; picking another
         * template replaces its fields wholesale and is meant to lose it.
         *
         * ONE PICK OWNS THE DIALOG: renderInline answers whether this build is
         * still the one being waited for, and a pick that lost writes nothing —
         * no form, no chip, no `builtFor`, no focus. `builtFor` records only a
         * build that LANDED and is on screen, so a lost pick retries, not no-ops.
         *
         * @param {object|null} template  null is Blank.
         * @returns {Promise<void>}
         */
        async function pickTemplate(template) {
            const key = template ? template.id : null;
            // The panel swaps FIRST, so the pick is acknowledged while the form
            // loads rather than after it. The primary that swap pins is
            // withheld by renderInline until the form it submits is on screen.
            showStep('form');
            if (key !== builtFor) {
                builtFor = undefined;
                const landed = await renderInline({ container: formEl, agent: null, primary, onSave, onDelete });
                if (!landed || step !== 'form') return;
                builtFor = key;
                if (template) {
                    HYDRATE.applyHireFields(formEl, TEMPLATE.templateFields(template));
                    TEMPLATE.applyTemplate(formEl, template);
                }
            }
            const name = formEl.querySelector('input[name="name"]');
            if (name) name.focus();
        }

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

        /**
         * A form that cannot render is the operator's to see, not the console's
         * alone. Nothing stays recorded as built, so the next pick is a retry.
         *
         * ONLY ON THE STEP THAT ASKED FOR IT, which `pickTemplate` checks and
         * this did not. Back is live while a build runs, so a build can fail
         * after the operator has left it: the error paragraph then went into
         * the hidden form host where nobody could read it, and "repairing the
         * footer" took `Browse marketplace` off the picker — the only door out
         * of an empty library — and put back a Back that led to the step
         * already on screen, then gave it focus. On the picker there is
         * nothing to repair and nothing to say; the build is recorded as not
         * built and picking again rebuilds.
         *
         * Where it does repair, the FOOTER is repaired with the body — the
         * primary submits `#agent-form` by id and the host no longer holds
         * one, so a row left as it was would offer a button that does nothing
         * at all and says nothing about why. What that leaves is
         * context/agent-dialog-footer.js's `recovery()`.
         *
         * @param {Error} err
         * @returns {void}
         */
        function failed(err) {
            console.error('[agent-edit] the agent form failed to render', err);
            builtFor = undefined;
            if (destroyed) return;
            if (wasCreating && step !== 'form') return;
            clear(formEl);
            formEl.append(h('p', { class: 'context-error', role: 'alert' },
                `${FAILED_COPY} ${wasCreating ? FAILED_BACK : FAILED_CLOSE}`));
            footer.recovery();
            // The row's rebuild took the keyboard; on a create the way out is
            // the body's own chevron, which the failure did not touch, so it
            // takes focus back from the dismissal footer.recovery() lit.
            if (backBtn) backBtn.focus();
        }

        // The picker owns its own loading, empty, failed and ready states, so
        // step one has nothing to await.
        if (wasCreating) void picker.refresh();
        else void renderInline({ container: formEl, agent: agent || null, primary, onSave, onDelete }).catch(failed);

        openDialog = { close: () => modal.close() };
        return openDialog;
    }

    return { openAgentModal };
})();
