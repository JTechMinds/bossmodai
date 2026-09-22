/**
 * BossMod AI — one agent form at a time, across every dialog that hosts one.
 *
 * `#agent-form` is a document-global id, and the dialog's pinned primary
 * submits it from OUTSIDE the form with `form="agent-form"`. Two live forms
 * under one id would let one dialog's button submit the other dialog's draft,
 * or turn an edit into a create. Two dialogs host the form now — the Edit role
 * dialog (context/agent-edit.js) and the Agents dialog
 * (context/agents-dialog.js) — so the rule each used to keep as a module-level
 * `openDialog` of its own is kept here, once, where both can read it.
 *
 * It holds a HANDLE — whatever the holding dialog hands back to its opener —
 * and nothing about what that dialog is or how it closes: a caller that finds
 * the slot taken hands the holder's handle back rather than stacking a second
 * form, which neither stacks two focus traps nor discards a draft nobody asked
 * to discard.
 */
const BossModAgentDialogSlot = (() => {
    /** The handle of the dialog holding the slot, or null. */
    let holder = null;

    /**
     * Who holds the slot.
     *
     * @returns {object|null} The holding dialog's handle, or null when no
     *   agent form is open.
     */
    function current() {
        return holder;
    }

    /**
     * Take the slot for a dialog that has just opened.
     *
     * @param {object} handle  What the dialog returns to its opener.
     * @returns {void}
     * @throws {Error} When no handle is given, or a handle is already held.
     *   A caller must ask `current()` first; claiming over a holder would be
     *   the two-live-forms state this module exists to make impossible.
     */
    function claim(handle) {
        if (!handle) throw new Error('[agent-dialog-slot] claim() needs the dialog handle');
        if (holder) {
            throw new Error('[agent-dialog-slot] an agent dialog is already open; ask current() first');
        }
        holder = handle;
    }

    /**
     * Give the slot back as the dialog closes.
     *
     * @param {object} handle  The handle that claimed it.
     * @returns {void}
     * @throws {Error} When `handle` is not the holder. A release by the wrong
     *   dialog would free the slot while the real holder's form is still live.
     */
    function release(handle) {
        if (!handle || handle !== holder) {
            throw new Error('[agent-dialog-slot] released by a dialog that does not hold it');
        }
        holder = null;
    }

    return { current, claim, release };
})();
