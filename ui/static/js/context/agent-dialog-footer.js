/**
 * BossMod AI — the agent dialog's pinned action row, and everything in it.
 *
 * Split out of context/agent-edit.js when that owned the whole create flow.
 * Two dialogs spend it now: the Edit role dialog (context/agent-edit.js, one
 * step) and the Agents dialog's Add agent pane (context/agent-add-pane.js,
 * the picker and then the form). Each says which step is on screen; this owns
 * the row underneath — which actions each step offers — and the STATE of the
 * primary.
 *
 * ONE SURFACE, ONE OWNER. `#agent-form-submit` is a
 * `<button type="submit" form="agent-form">` living in the footer, outside the
 * form it submits. That attribute is what lets a form whose body scrolls keep
 * its primary action in sight — round three left it at the bottom of the
 * scroll, where the operator could not find it — and it is also how the button
 * came to have THREE owners: the dialog that rebuilds the footer per step, the
 * render that withholds it while it builds, and the save that paints it busy.
 * The last two reached it with a document-wide lookup and wrote over whatever
 * they found, so a save that was refused re-enabled a button a newer build was
 * holding down, and a build whose own dialog had been dismissed relabelled the
 * NEXT dialog's primary. Splitting the row's buttons from one button's state
 * would rebuild that defect in miniature, so both live here.
 *
 * BACK IS NOT IN THE ROW: overlays.js closes the dialog after every action
 * that is not a form submit, and Back must leave it open with the draft still
 * in it — so it is the pane's own chevron on the title row. Delete did not
 * travel up here at all — it is destructive and stays in the form body, away
 * from the primary.
 *
 * SAVE AS TEMPLATE IS, at the other end of the row: it is about the form as a
 * whole, like the primary, and it leaves the dialog open (`keepOpen`) because
 * it opens a layer over the form it reads. It is WITHHELD for exactly as long
 * as the primary is `building` — the host then still holds the pick the
 * operator just left, and a template saved from it would be that draft.
 *
 * THE ROW CAN BE SUSPENDED. The Agents dialog has two tabs over one footer
 * band, and the Add agent pane's async work — a build landing, a build
 * failing, a save settling — can finish while the Marketplace tab is up. A
 * suspended footer keeps the row EMPTY and only records which row the pane is
 * owed; resuming applies that row without moving the keyboard. The Edit
 * dialog never suspends, so for it nothing here changes.
 *
 * THE PRIMARY'S FIVE STATES, and who enters each:
 *
 *  absent    no primary in the row at all — step one of the create flow, which
 *            has no form for a `form=` button to submit, the row the failure
 *            path leaves, and a suspended row. A write still RECORDS the
 *            state it would have painted, so the button a later row builds
 *            comes back in it; it only has nothing to paint now.
 *  building  `Loading…`, disabled — a render, before it publishes.
 *  ready     `Create Agent` / `Save Changes`, live — a render that landed, and
 *            a save on its way out.
 *  blocked   the resting label, disabled, described by the line that says why
 *            — a render whose connections read failed.
 *  saving    `Creating…` / `Saving…`, disabled — the form's submit handler.
 *
 * Every write refuses twice: it is SCOPED to one dialog's root and stops while
 * that root is out of the document, and it is CLAIMED — each render takes a
 * claim, only the holder may move the button, and a save inherits its render's
 * claim, so a save settling after a newer build took over cannot undo that
 * build's withhold.
 *
 * FOCUS travels with the state. Disabling the element that holds the keyboard
 * strands focus on <body>, so a withhold that has a reason node hands the
 * keyboard to that node — the thing that explains the control that just went
 * away — and going live hands it back.
 */
const BossModAgentDialogFooter = (() => {
    const { h } = BossModDom;

    /** The primary's id, and the id of the form it submits from outside it. */
    const ID = 'agent-form-submit';
    const FORM_ID = 'agent-form';

    /** Everything the primary ever says, per flow. */
    const WORDS = Object.freeze({
        create: Object.freeze({ resting: 'Create Agent', busy: 'Creating…' }),
        edit: Object.freeze({ resting: 'Save Changes', busy: 'Saving…' }),
    });

    /** Said while a render is building the form the primary submits. */
    const BUILDING = 'Loading…';

    const CANCEL = Object.freeze({ label: 'Cancel', tone: 'quiet' });

    /** The one other action that reads the form, and its id. */
    const SAVE_TEMPLATE_ID = 'agent-save-template';
    const SAVE_TEMPLATE = 'Save as template';

    /**
     * The actions one step offers, left to right.
     *
     * The PICKER offers none. Its only way anywhere else is the Marketplace
     * tab beside it in the Agents dialog's head — the footer `Browse
     * marketplace` that used to lead this row closed the dialog and reopened
     * it behind the marketplace, which is gone with it — and its exit is the
     * frame's `✕`. A Cancel here would be a second control for that one
     * errand.
     *
     * BACK IS NOT HERE. It is the chevron on the title row of the form step
     * (context/agent-add-pane.js), which is where every other back control in
     * this app lives — the desk's, and the marketplace detail's.
     *
     * @param {'picker'|'form'} step
     * @param {{creating: boolean, onSaveTemplate?: () => void}} chrome  With
     *   an `onSaveTemplate`, the form row LEADS with Save as template — the
     *   stylesheet pushes it to the far end, away from the two that finish the
     *   dialog.
     * @returns {Array<object>} core/overlays.js action descriptors. The
     *   primary carries `form`, which is both what makes it submit a form it
     *   is not inside and what tells overlays.js this action must NOT close
     *   the dialog — a refused save keeps the draft on screen. Save as
     *   template says the same thing the other way, with `keepOpen`: it opens
     *   a layer over the form and the form has to be there underneath it.
     * @throws {Error} For any other step: a row nobody declared is a footer
     *   that silently shows the wrong thing.
     */
    function actionsFor(step, chrome) {
        if (step === 'form') {
            const words = chrome.creating ? WORDS.create : WORDS.edit;
            const primary = {
                label: words.resting, tone: 'primary', id: ID, form: FORM_ID,
            };
            if (typeof chrome.onSaveTemplate !== 'function') return [CANCEL, primary];
            return [{
                label: SAVE_TEMPLATE, tone: 'quiet', id: SAVE_TEMPLATE_ID,
                keepOpen: true, onSelect: chrome.onSaveTemplate,
            }, CANCEL, primary];
        }
        if (step === 'picker') return [];
        throw new Error(`[agent-dialog-footer] no row for step "${step}"`);
    }

    /**
     * The primary's state machine, for one dialog.
     *
     * @param {HTMLElement} root  The dialog panel the button lives in.
     * @param {boolean} creating  Which flow, and so which two labels.
     * @returns {{claim: () => object, holds: (token: object) => boolean,
     *   building: (token: object) => boolean,
     *   ready: (token: object, refocus?: boolean) => boolean,
     *   blocked: (token: object, reason: HTMLElement, refocus?: boolean) => boolean,
     *   saving: (token: object, reason: HTMLElement) => boolean,
     *   repaint: () => void}}
     *   Every state answers whether the button held the keyboard on the way
     *   in, which is how a caller that takes focus away knows to give it back.
     *   `false` also covers "there was nothing to paint" — an absent primary,
     *   a superseded claim, a closed dialog — and none of those are errors:
     *   they are the states this owner exists to tell apart. An ABSENT
     *   primary is the one of the three that still records the state: the
     *   claim is live and the dialog is open, so the row that next holds a
     *   primary is owed it.
     *
     *   It owns one control beyond the primary: `Save as template` is
     *   withheld while the state is `building`, because it reads the same
     *   form — see the header.
     */
    function createPrimary(root, creating) {
        const words = creating ? WORDS.create : WORDS.edit;

        /** The claim currently allowed to move the button, or null. */
        let holder = null;

        /**
         * The last state written — painted, or owed to a row that held no
         * primary when it was written — so a REBUILT row can be put back
         * into it. setActions makes a new button at the resting label and
         * enabled, and Back then re-picking the same template rebuilds the row
         * without rebuilding the form — so a primary the current render is
         * still holding down came back live, and a `blocked` form offered a
         * button whose only remaining answer was the refusal on click.
         */
        let painted = null;

        function claim() {
            holder = {};
            return holder;
        }

        function holds(token) {
            return Boolean(token) && token === holder;
        }

        /**
         * Whether `token` may write this dialog's primary at all: it holds
         * the claim, and the dialog is still in the document. Separate from
         * FINDING the button, because a row that holds no primary right now
         * is still owed the state.
         */
        function writable(token) {
            if (!holds(token)) return false;
            // A closed dialog is detached with its row inside it. Painting
            // that button changes nothing anyone can see, but refusing here is
            // what makes "this owner writes to the screen or not at all" a
            // property rather than a coincidence.
            if (!document.body.contains(root)) return false;
            return true;
        }

        /** This dialog's primary, or null when it may not be written. */
        function resolve(token) {
            if (!writable(token)) return null;
            return root.querySelector(`#${ID}`);
        }

        /**
         * @param {object} token
         * @param {{label: string, disabled: boolean, reason?: HTMLElement|null,
         *   refocus?: boolean}} state
         * @returns {boolean} Whether it held the keyboard on the way in.
         */
        function set(token, { label, disabled, reason = null, refocus = false }) {
            if (!writable(token)) return false;
            // Remembered BEFORE the button is looked for, and without the
            // focus move: a repaint restores what the button says, never where
            // the keyboard is. Recording it only once a button was found lost
            // every state that landed while the row held none — a build that
            // finished while the Agents dialog's Marketplace tab was up left
            // `building` as the last thing remembered, and the row the Add
            // agent tab got back came up `Loading…` and disabled for good.
            painted = { token, state: { label, disabled, reason } };
            // Save as template reads the form too, and while one is BUILDING
            // the form in the host is the pick the operator just left. One
            // state, both controls, so a rebuilt row cannot restore half of it.
            const saveTemplate = root.querySelector(`#${SAVE_TEMPLATE_ID}`);
            if (saveTemplate) saveTemplate.disabled = label === BUILDING;
            const button = root.querySelector(`#${ID}`);
            if (!button) return false;
            const held = document.activeElement === button;
            button.disabled = disabled;
            button.textContent = label;
            // A control withheld without a reason is a dead end, so the line
            // that carries the reason is named as this button's description —
            // and dropped the moment it is live again, or it would describe an
            // enabled control with a stale refusal.
            if (reason) button.setAttribute('aria-describedby', reason.id);
            else button.removeAttribute('aria-describedby');
            if (refocus) {
                if (!disabled) button.focus();
                else if (reason) reason.focus();
            }
            return held;
        }

        return {
            claim,
            holds,
            /** Put a rebuilt row's new button back into the state it is in. */
            repaint() {
                if (painted) set(painted.token, painted.state);
            },
            building: (token) => set(token, { label: BUILDING, disabled: true }),
            ready: (token, refocus) => set(token, {
                label: words.resting, disabled: false, refocus,
            }),
            blocked: (token, reason, refocus) => set(token, {
                label: words.resting, disabled: true, reason, refocus,
            }),
            saving: (token, reason) => {
                // Read before the write: the operator clicked this button, so
                // it holds the keyboard now and will not once it is disabled.
                const button = resolve(token);
                const held = Boolean(button) && document.activeElement === button;
                set(token, { label: words.busy, disabled: true, reason, refocus: held });
                return held;
            },
        };
    }

    /**
     * The footer of one open dialog.
     *
     * It remembers which row it OWES — `null` until a step is shown, then
     * `'picker'`, `'form'` or `'recovery'` — separately from what is on
     * screen, because a suspended footer shows nothing and must still know
     * what to put back.
     *
     * @param {object} modal  From core/overlays.js. Needed for its `element`
     *   and `setActions`, which is why this is built after the dialog while
     *   `actionsFor` — the row it opens with — is a plain function.
     * @param {{creating: boolean, onSaveTemplate?: () => void}} chrome  Which
     *   flow, and so which words the primary says — and what Save as template
     *   does, when the caller offers one. Held by reference and read on every
     *   row build.
     * @returns {{show: (step: 'picker'|'form') => void, recovery: () => void,
     *   suspend: () => void, resume: () => void, primary: object}}
     *   `show` and `recovery` write the row while the footer is live and only
     *   record it while suspended; `suspend` empties the row; `resume` puts
     *   the owed row back without moving the keyboard.
     * @throws {Error} Without a modal: a footer that could not scope its
     *   lookups would be the document-wide one this replaces.
     */
    function createFooter(modal, chrome) {
        if (!modal) throw new Error('[agent-dialog-footer] a modal is required');
        const primary = createPrimary(modal.element, chrome.creating);
        /** The row the pane is owed: null, 'picker', 'form' or 'recovery'. */
        let owed = null;
        /** True while another tab is up and the row belongs to it. */
        let suspended = false;
        return {
            /** Swap the row for `step`. setActions rebuilds it in place. */
            show(step) {
                owed = step;
                if (suspended) return;
                modal.setActions(actionsFor(step, chrome));
                // The button in that row is a new one and knows nothing, so
                // the state its current render is holding is put back on it.
                primary.repaint();
            },
            /**
             * What is left when the form could not render: the primary submits
             * `#agent-form` by id and the host no longer holds one, so the row
             * keeps only the dismissal, and takes the focus that rebuilding it
             * destroyed. Back is not rebuilt here because it is not in this
             * row: it is the pane's own chevron and survives the failure, so
             * a create still has its way back — context/agent-add-pane.js is
             * what puts the keyboard on it.
             *
             * While SUSPENDED it only records the debt. A build that fails
             * while the Marketplace tab is up must not put `Cancel` into that
             * tab's footer, and must not take the keyboard from it.
             *
             * Save as template is not offered here either: there is no form
             * left to read one off.
             */
            recovery() {
                owed = 'recovery';
                if (suspended) return;
                modal.setActions([CANCEL]);
                modal.element.querySelector('.modal-actions').children[0].focus();
            },
            /**
             * Another tab is up: the row is its, and it has none.
             *
             * @returns {void}
             */
            suspend() {
                suspended = true;
                modal.setActions([]);
            },
            /**
             * This pane's tab is back: put up the row it is owed. The keyboard
             * is NOT moved — it is on the tab the operator just chose, and the
             * row coming back is not something they asked to be taken to.
             *
             * @returns {void}
             */
            resume() {
                suspended = false;
                if (owed === null) {
                    modal.setActions([]);
                    return;
                }
                if (owed === 'recovery') {
                    modal.setActions([CANCEL]);
                    return;
                }
                modal.setActions(actionsFor(owed, chrome));
                primary.repaint();
            },
            primary,
        };
    }

    return { actionsFor, createFooter };
})();
