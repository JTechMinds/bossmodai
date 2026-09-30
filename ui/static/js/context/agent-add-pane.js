/**
 * BossMod AI — Add agent: the Agents dialog's create pane, in two steps.
 *
 * Extracted from context/agent-edit.js when Add agent and the Agent
 * Marketplace became two tabs of ONE dialog (context/agents-dialog.js). This
 * is the create flow with no modal of its own: the template picker, the form
 * host, the step the form puts on the frame's trail (`Agents › Code Auditor`),
 * and the wiring a create does once it lands — the new agent's conversation
 * opens, and the dialog closes. The dialog owns the frame, the tabs and which pane is up;
 * building the form and saving through it is context/agent-form-save.js, which
 * this hosts and calls but does not re-export.
 *
 * TWO STEPS OVER ONE BODY: the picker, then the form, swapped with `hidden`.
 * The form is built on the first pick and rebuilt only when the CHOICE changes,
 * so a draft survives Back and re-picking the same cell and no "discard your
 * draft?" prompt has to exist. A second stacked dialog for step two was
 * rejected: two focus traps over one task.
 *
 * THREE KINDS OF PICK, one form. Blank builds it empty, a template hydrates
 * the role contract into it, and a RECENT agent — a snapshot — is built from
 * as `prefill`: values only, never identity, so the form creates a NEW agent
 * rather than editing the one it came from (context/agent-form.js).
 *
 * The footer is PER STEP, and it is context/agent-dialog-footer.js's — the row
 * itself and the state of the primary inside it. This says which step is on
 * screen; that says what the row holds.
 *
 * THE PANE CAN BE AWAY WHILE ITS OWN WORK FINISHES. The Marketplace tab can be
 * up while a build lands, a build fails or a save settles. So while the pane
 * is inactive its footer is SUSPENDED — it records the row it is owed and
 * writes nothing into the other tab's footer — and nothing here moves the
 * keyboard: focus on a control in a hidden pane is a no-op in a browser and a
 * theft in a test. The pane is hidden, never destroyed, on a tab switch, so the
 * picker's filter and a half-typed form both survive the trip.
 */
const BossModAgentAddPane = (() => {
    const { h, clear } = BossModDom;
    const HYDRATE = BossModAgentFormHydrate;
    const TEMPLATE = BossModAgentFormTemplate;
    const FOOTER = BossModAgentDialogFooter;
    // The form's own wiring — building it, saving it — is
    // context/agent-form-save.js. Named here because this module hosts it.
    const { renderInline } = BossModAgentFormSave;

    /** The form step's crumb for a Blank pick, which has no name of its own. */
    const BLANK_TITLE = 'New agent';
    // A failed render is recoverable here: step one is still mounted with its
    // list. That is not "refresh the page", which is what this said before
    // step two existed.
    const FAILED_COPY = 'The agent editor failed to load.';
    const FAILED_BACK = 'Go back and pick again.';

    /**
     * Build the create pane.
     *
     * The picker is built at once; the footer — and with it the row the pane
     * is owed — waits for `attach`, which needs the dialog it lives in. The
     * dialog then calls `activate` or `deactivate` for whichever tab it opens
     * on.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store. A successful create
     *   routes to the new agent's conversation, so the operator lands
     *   somewhere rather than back at a blank form.
     * @param {() => void} deps.onBrowse  The picker's empty-state door: the
     *   dialog switches to its Marketplace tab.
     * @param {() => void} deps.onDone  The create finished (or a recovery tool
     *   saved); the dialog closes.
     * @param {(template: object) => void} deps.onTemplateSaved  The form's
     *   role contract was saved as a local template; every library view the
     *   dialog holds has to re-read.
     * @param {() => void} deps.onStepsChange  The step changed; the dialog
     *   re-reads `steps()` into the frame's trail.
     * @returns {{element: HTMLElement, steps: () => Array<{title: string,
     *   onBack: () => void}>,
     *   attach: (modal: object) => void, activate: () => void,
     *   deactivate: () => void, refresh: () => Promise<void>,
     *   pick: (choice: object) => Promise<void>,
     *   holdsDraft: () => boolean, dispose: () => void}}
     *   `element` is the body (`.agent-add-body`: picker and form host).
     *   `steps` is where the pane is, for the frame's trail: none on the
     *   picker; on the form, one crumb named for the pick — the template's
     *   title, the snapshot's name, or `New agent` for Blank — whose way back
     *   is the picker. `attach` builds the footer on the dialog, once. `activate`/`deactivate` say whether this pane is the
     *   visible tab. `refresh` re-reads the picker's two lists. `pick` is a
     *   cell click by another name — the Marketplace's "Add agent from this"
     *   — and never rejects: a failed build becomes the in-pane recovery
     *   state. `holdsDraft` answers whether a form build has landed (a draft
     *   exists to lose). `dispose` is the dialog closing: a save or a failure
     *   that settles afterwards routes nowhere and repairs nothing.
     * @throws {Error} When store, onBrowse, onDone, onTemplateSaved or
     *   onStepsChange is missing. `attach` throws when called twice; `pick`, `activate` and
     *   `deactivate` throw before `attach` — each needs the footer that only
     *   it can build — and `pick` throws on a choice that is none of the three.
     */
    function create(deps) {
        const {
            store, onBrowse, onDone, onTemplateSaved, onStepsChange,
        } = deps || {};
        if (!store) throw new Error('[agent-add-pane] deps.store is required');
        if (typeof onBrowse !== 'function') throw new Error('[agent-add-pane] deps.onBrowse is required');
        if (typeof onDone !== 'function') throw new Error('[agent-add-pane] deps.onDone is required');
        if (typeof onTemplateSaved !== 'function') {
            throw new Error('[agent-add-pane] deps.onTemplateSaved is required');
        }
        if (typeof onStepsChange !== 'function') {
            throw new Error('[agent-add-pane] deps.onStepsChange is required');
        }

        let destroyed = false;
        /** Which CHOICE the form on screen was built from — `keyOf`'s string,
         *  `null` for Blank — and the sentinel is "no form built yet". */
        let builtFor;
        /** That choice itself, for what the key cannot carry: the category a
         *  template was filed under, which Save as template starts from. */
        let builtFrom = null;
        /** Step one is on screen from the start; the form host is hidden. */
        let step = 'picker';
        /** The form step's crumb, named at the PICK: the step is up from then. */
        let formTitle = BLANK_TITLE;
        /** Whether this pane is the visible tab. The dialog's first showTab
         *  sets it, whichever tab that is. */
        let active = false;
        /** Built by attach(), on the dialog the row belongs to. */
        let footer = null;
        let primary = null;
        let modal = null; // attach()'s frame: a failed build focuses its ‹

        const formEl = h('div', { class: 'agent-form-host' });
        formEl.hidden = true;
        const picker = BossModAgentTemplatePicker.createPicker({
            onPick: (choice) => { void pick(choice); },
            // The picker's EMPTY state carries its own door, in the middle of
            // the pane where a first-run operator with nothing installed is
            // actually looking. It switches tabs now; it used to close this
            // dialog and reopen it behind the marketplace.
            onBrowse: () => onBrowse(),
        });
        const element = h('div', { class: 'agent-add-body' }, picker.element, formEl);

        /** The footer, or a named error for a call that came before it. */
        function requireFooter(what) {
            if (!footer) throw new Error(`[agent-add-pane] ${what} before attach()`);
            return footer;
        }

        /**
         * Swap between the picker and the form.
         *
         * The footer rebuilds in place, destroying whichever button held focus
         * — in BOTH directions — and createModal only places focus on mount.
         * So the swap places it again: the Find box on the way back, and the
         * Name input once its caller has built the form. Neither while the
         * pane is away.
         *
         * @param {'picker'|'form'} next
         * @returns {void}
         */
        function showStep(next) {
            if (step === next) return;
            step = next;
            picker.element.hidden = next === 'form';
            formEl.hidden = next !== 'form';
            onStepsChange(); // the frame's ‹ and trail: outside what a failure empties
            footer.show(next);
            if (next !== 'form' && active) picker.focus();
        }

        /**
         * What identifies the form one choice builds, so the same cell picked
         * twice is the same form and a different one rebuilds it.
         *
         * @param {object} choice  `{kind: 'blank'|'template'|'snapshot', row?}`.
         * @returns {string|null} null for Blank; `tpl:`/`snap:` prefixed
         *   otherwise, so a template and a snapshot sharing an id cannot read
         *   as one build.
         * @throws {Error} On a kind this pane cannot build a form from — a
         *   caller's bug, and a silent no-op would look like a dead cell.
         */
        function keyOf(choice) {
            const kind = choice && choice.kind;
            if (kind === 'blank') return null;
            if (kind === 'template') return `tpl:${choice.row.id}`;
            if (kind === 'snapshot') return `snap:${choice.row.id}`;
            throw new Error(`[agent-add-pane] no form for a "${kind}" pick`);
        }

        /** The form step's crumb: what the picked card is called. A row
         *  with no name reaches setSteps as-is, which throws on it. */
        function titleOf(choice) {
            if (choice.kind === 'template') return choice.row.title;
            return choice.kind === 'snapshot' ? choice.row.name : BLANK_TITLE;
        }

        /**
         * A cell was picked. The form is rebuilt only for a DIFFERENT pick, so
         * Back and re-picking the same cell keep the draft; picking another
         * cell replaces its fields wholesale and is meant to lose it.
         *
         * ONE PICK OWNS THE PANE: renderInline answers whether this build is
         * still the one being waited for, and a pick that lost writes nothing —
         * no form, no chip, no `builtFor`, no focus. `builtFor` records only a
         * build that LANDED and is on screen, so a lost pick retries, not
         * no-ops.
         *
         * @param {object} choice  Blank, a template, or a snapshot to recreate.
         * @returns {Promise<void>}
         */
        async function pickChoice(choice) {
            const key = keyOf(choice);
            formTitle = titleOf(choice); // before the swap reports its crumb
            // The pane swaps FIRST, so the pick is acknowledged while the form
            // loads rather than after it. The primary that swap pins is
            // withheld by renderInline until the form it submits is on screen.
            showStep('form');
            // EVERY BUILD STARTS WITH THE FOOTER OWING THE FORM ROW. showStep
            // returns early on the step it is already on, and "Add agent from
            // this" can pick while this pane sits on a FAILED form step, whose
            // row is `Cancel` alone — the build would then paint a primary
            // that is not in the row. Rebuilding it is cheap and idempotent:
            // the row comes back and the primary is repainted into its state.
            footer.show('form');
            if (key !== builtFor) {
                builtFor = undefined;
                // A snapshot is VALUES: the form it fills creates a new agent,
                // which is why it is a prefill and never an `agent`.
                const prefill = choice.kind === 'snapshot' ? choice.row : null;
                const landed = await renderInline({
                    container: formEl, agent: null, prefill, primary, onSave,
                });
                if (!landed || step !== 'form') return;
                builtFor = key;
                builtFrom = choice;
                if (choice.kind === 'template') {
                    HYDRATE.applyHireFields(formEl, TEMPLATE.templateFields(choice.row));
                    TEMPLATE.applyTemplate(formEl, choice.row);
                } else if (choice.kind === 'snapshot') {
                    // Start blank is a PICK, not a field-by-field undo: every
                    // field is the snapshot's, so half-clearing them would
                    // leave a form that is neither agent.
                    TEMPLATE.applySnapshotChip(formEl, choice.row,
                        () => { void pick({ kind: 'blank' }); });
                }
            }
            // A build can land while the Marketplace tab is up; the keyboard
            // stays where the operator has it.
            if (!active) return;
            const name = formEl.querySelector('input[name="name"]');
            if (name) name.focus();
        }

        /**
         * A cell was picked, from the grid or from the Marketplace.
         *
         * @param {object} choice  `{kind: 'blank'}`, `{kind: 'template', row}`
         *   or `{kind: 'snapshot', row}`.
         * @returns {Promise<void>} Never rejects: a build that fails is
         *   `failed`'s to show.
         * @throws {Error} Before attach() — there is no footer to swap — and
         *   on an unknown choice, which is checked before anything is swapped
         *   so a caller's bug is not shown to the operator as a failed build.
         */
        function pick(choice) {
            requireFooter('pick()');
            keyOf(choice);
            return pickChoice(choice).catch(failed);
        }

        /**
         * The footer's `Save as template`: the form's role contract into the
         * library (context/agent-save-template.js).
         *
         * The CURRENT form, read at click time, so what is saved is what is on
         * screen. It cannot be a form that is still building: the row withholds
         * this action for exactly as long as it withholds the primary
         * (context/agent-dialog-footer.js), because the host still holds the
         * pick the operator just left.
         *
         * @returns {void}
         * @throws {Error} With no form on screen at all — the action should not
         *   have been live, and saving nothing is worse than saying so.
         */
        function saveTemplate() {
            const form = formEl.querySelector('#agent-form');
            if (!form) throw new Error('[agent-add-pane] Save as template with no form');
            // Named at CLICK time, not aliased at the top with the rest: this
            // is the one thing here nothing in a build reaches, and a pane
            // built in a harness that never opens the layer needs no stub.
            const SAVE_TEMPLATE = BossModAgentSaveTemplate;
            void SAVE_TEMPLATE.open({
                form,
                defaultTitle: SAVE_TEMPLATE.defaultTitle(form),
                // The library shelf this form came off, when it came off one.
                defaultCategory: builtFrom && builtFrom.kind === 'template'
                    ? builtFrom.row.category : SAVE_TEMPLATE.CUSTOM,
                onSaved: onTemplateSaved,
            });
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
            if (savedAgent) {
                // Creating someone opens their conversation, so the operator
                // lands somewhere rather than back at a blank form. NOT their
                // desk: the desk is a modal now, and one springing open over
                // the chat the operator just landed in would block it. It is
                // one click away, on the conversation header's lamp.
                store.setState({
                    conversationId: savedAgent.id,
                    conversationKind: 'agent',
                });
            }
            onDone();
        }

        /**
         * A form that cannot render is the operator's to see, not the console's
         * alone. Nothing stays recorded as built, so the next pick is a retry.
         *
         * ONLY ON THE STEP THAT ASKED FOR IT, which `pickTemplate` checks too.
         * Back is live while a build runs, so a build can fail after the
         * operator has left it: the error paragraph then went into the hidden
         * form host where nobody could read it, and "repairing the footer"
         * rewrote step one's row and handed the keyboard to a Back that led to
         * the step already on screen. On the picker there is nothing to repair
         * and nothing to say; the build is recorded as not built and picking
         * again rebuilds.
         *
         * Where it does repair, the FOOTER is repaired with the body — the
         * primary submits `#agent-form` by id and the host no longer holds
         * one, so a row left as it was would offer a button that does nothing
         * at all and says nothing about why. What that leaves is
         * context/agent-dialog-footer.js's `recovery()`, which only records
         * the debt while the Marketplace tab is up.
         *
         * @param {Error} err
         * @returns {void}
         */
        function failed(err) {
            console.error('[agent-add-pane] the agent form failed to render', err);
            builtFor = undefined;
            builtFrom = null;
            if (destroyed) return;
            if (step !== 'form') return;
            clear(formEl);
            formEl.append(h('p', { class: 'context-error', role: 'alert' },
                `${FAILED_COPY} ${FAILED_BACK}`));
            footer.recovery();
            // The row's rebuild took the keyboard; the way out is the frame's
            // ‹, which the failure did not touch, so it takes focus back from
            // the dismissal footer.recovery() lit. Not while the pane is away:
            // that row was only recorded, and the keyboard is the other tab's.
            if (active) modal.focusBack();
        }

        return {
            element,
            steps() {
                if (step !== 'form') return [];
                return [{ title: formTitle, onBack: () => showStep('picker') }];
            },
            attach(frame) {
                if (footer) throw new Error('[agent-add-pane] attach() was called twice');
                modal = frame;
                footer = FOOTER.createFooter(frame, {
                    creating: true, onSaveTemplate: saveTemplate,
                });
                primary = footer.primary;
                // Owed from the start, so a tab switch before the first pick
                // has a row to put back — an empty one, on step one. The pane
                // cannot have left step one yet: pick() refuses until now.
                footer.show('picker');
            },
            activate() {
                requireFooter('activate()');
                active = true;
                footer.resume();
            },
            deactivate() {
                requireFooter('deactivate()');
                active = false;
                footer.suspend();
            },
            refresh: () => picker.refresh(),
            pick,
            holdsDraft: () => builtFor !== undefined,
            dispose() { destroyed = true; },
        };
    }

    return { create };
})();
