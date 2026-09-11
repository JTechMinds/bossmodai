/**
 * BossMod AI — the agent dialog's pinned action row, and everything in it.
 *
 * Split out of context/agent-edit.js, which owns the dialog and its two steps.
 * This owns the row underneath: which actions each step offers, the Back that
 * core/overlays.js cannot declare, and the STATE of the primary.
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
 * BACK IS BUILT, NOT DECLARED: overlays.js closes the dialog after every
 * action that is not a form submit, and Back must leave it open with the draft
 * still in it. Delete did not travel up here at all — it is destructive and
 * stays in the form body, away from the primary.
 *
 * THE PRIMARY'S FIVE STATES, and who enters each:
 *
 *  absent    no primary in the row at all — step one of the create flow, which
 *            has no form for a `form=` button to submit, and the row the
 *            failure path leaves. Every write finds nothing and says so.
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

    /**
     * The actions one step offers, left to right.
     *
     * `Browse marketplace` is step one's LEAD action: in the row, but pushed to
     * the left edge by `#agent-add-browse`'s own rule, away from the dismissal
     * on the right. It sat beside the filter box in the picker's header for one
     * round and read as part of the filter — "find a template" and "browse the
     * marketplace" are not the same errand, and putting them on one line said
     * they were. The row's two ends are the app's own division: what takes you
     * somewhere else on the left, what ends the task on the right.
     *
     * BACK IS NOT HERE. It is the chevron at the top-left of step two's body
     * (context/agent-edit.js), which is where every other back control in this
     * app lives — the desk's, and the marketplace detail's.
     *
     * @param {'picker'|'form'} step
     * @param {{creating: boolean, onBrowse: () => void}} chrome
     * @returns {Array<object>} core/overlays.js action descriptors. The
     *   primary carries `form`, which is both what makes it submit a form it
     *   is not inside and what tells overlays.js this action must NOT close
     *   the dialog — a refused save keeps the draft on screen.
     */
    function actionsFor(step, chrome) {
        if (step === 'form') {
            const words = chrome.creating ? WORDS.create : WORDS.edit;
            return [CANCEL, {
                label: words.resting, tone: 'primary', id: ID, form: FORM_ID,
            }];
        }
        return [
            {
                label: 'Browse marketplace', tone: 'quiet', id: 'agent-add-browse',
                onSelect: () => chrome.onBrowse(),
            },
            CANCEL,
        ];
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
     *   they are the states this owner exists to tell apart.
     */
    function createPrimary(root, creating) {
        const words = creating ? WORDS.create : WORDS.edit;

        /** The claim currently allowed to move the button, or null. */
        let holder = null;

        /**
         * The last state actually painted, so a REBUILT row can be put back
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

        /** This dialog's primary, or null when it may not be written. */
        function resolve(token) {
            if (!holds(token)) return null;
            // A closed dialog is detached with its row inside it. Painting
            // that button changes nothing anyone can see, but refusing here is
            // what makes "this owner writes to the screen or not at all" a
            // property rather than a coincidence.
            if (!document.body.contains(root)) return null;
            return root.querySelector(`#${ID}`);
        }

        /**
         * @param {object} token
         * @param {{label: string, disabled: boolean, reason?: HTMLElement|null,
         *   refocus?: boolean}} state
         * @returns {boolean} Whether it held the keyboard on the way in.
         */
        function set(token, { label, disabled, reason = null, refocus = false }) {
            const button = resolve(token);
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
            // Remembered without the focus move: a repaint restores what the
            // button says, never where the keyboard is.
            painted = { token, state: { label, disabled, reason } };
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
     * @param {object} modal  From core/overlays.js. Needed for its `element`
     *   and `setActions`, which is why this is built after the dialog while
     *   `actionsFor` — the row it opens with — is a plain function.
     * @param {{creating: boolean, onBack: () => void}} chrome
     * @returns {{show: (step: string) => void, recovery: () => void,
     *   primary: object}}
     * @throws {Error} Without a modal: a footer that could not scope its
     *   lookups would be the document-wide one this replaces.
     */
    function createFooter(modal, chrome) {
        if (!modal) throw new Error('[agent-dialog-footer] a modal is required');
        const primary = createPrimary(modal.element, chrome.creating);
        return {
            /** Swap the row for `step`. setActions rebuilds it in place. */
            show(step) {
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
             * row: it is the body's own chevron and survives the failure, so
             * a create still has its way back — context/agent-edit.js is what
             * puts the keyboard on it.
             */
            recovery() {
                modal.setActions([CANCEL]);
                modal.element.querySelector('.modal-actions').children[0].focus();
            },
            primary,
        };
    }

    return { actionsFor, createFooter };
})();
