/**
 * BossMod AI — the conversation title, renameable in place.
 *
 * Easy but non-obvious, which is the operator's requirement: at rest it is the
 * title and nothing else — no colour, no underline, no affordance. Click it,
 * or Tab to it and press Enter, and it turns a light shade of blue; type, then
 * Enter or the Save action that appears beside Archive.
 *
 * It is ONE control in two states rather than a label that swaps for an input.
 * A swap loses the caret and the focus at the moment the operator commits to
 * editing, and it needs two nodes that have to keep looking identical. A
 * read-only text input already looks like text, is already a tab stop, and
 * already carries a caret when it stops being read-only.
 *
 * The rename is NOT optimistic. The title shows what the server confirmed; a
 * failure keeps the operator's text, keeps the control in edit mode, and
 * reports on the surface's one error line. Painting the draft and rolling it
 * back is how a rename that silently did not happen looks exactly like one
 * that did.
 *
 * Nothing here knows what a thread is. It is handed a `title` and, when the
 * conversation can be renamed at all, a function that performs the rename —
 * so an agent conversation gets a plain heading and no tab stop, which is the
 * honest rendering of "this cannot be renamed".
 */
const BossModTitleRename = (() => {
    const { h, clear } = BossModDom;

    /** Matches the server's cap (api/routes/agents.py CHANNEL_NAME_MAX_LENGTH). */
    const MAX_LENGTH = 120;
    const EMPTY_NAME = 'A thread needs a name.';

    /**
     * Build the title slot.
     *
     * @param {object} deps
     * @param {(message: string) => void} deps.onError  The surface's one error
     *   line. A rename that fails silently would leave the operator believing
     *   a thread was renamed when it was not.
     * @param {() => void} deps.onEditingChange  Called whenever edit mode opens
     *   or closes, so the header can add or drop its Save action. The action
     *   row belongs to the chrome; this only says when the state changed.
     * @returns {{ element: HTMLElement,
     *             apply: (view: {title: string, onRename?: Function}) => void,
     *             isEditing: () => boolean,
     *             save: () => Promise<void>,
     *             cancel: () => void }}
     * @throws {Error} When either dependency is missing.
     */
    function createEditableTitle(deps) {
        const onError = deps && deps.onError;
        const onEditingChange = deps && deps.onEditingChange;
        if (typeof onError !== 'function') {
            throw new Error('[title-rename] deps.onError is required');
        }
        if (typeof onEditingChange !== 'function') {
            throw new Error('[title-rename] deps.onEditingChange is required');
        }

        /** The rename function for the open conversation, or null. */
        let renamer = null;
        /** The last name the SERVER confirmed. Cancel restores this. */
        let committed = '';
        let editing = false;
        /** 'text' or 'input': which shape is mounted, so a repaint moves nothing. */
        let shape = null;

        const input = h('input', {
            class: 'conversation-title-edit',
            type: 'text',
            id: 'conversation-title-edit',
            // It looks like the title, so the name has to say what it is.
            'aria-label': 'Rename this thread',
            'data-editing': 'false',
            readonly: true,
            maxlength: String(MAX_LENGTH),
            onclick: () => beginEdit(),
            onkeydown: (event) => onKeydown(event),
        });

        const element = h('h2', { class: 'conversation-title' });

        /** Keep the field the width of what it holds; an input has no auto. */
        function sizeToValue() {
            input.size = Math.max(8, Math.min(48, String(input.value || '').length + 1));
        }

        function setEditing(on) {
            editing = on;
            input.readOnly = !on;
            input.setAttribute('data-editing', String(on));
            onEditingChange();
        }

        /** @returns {void} */
        function beginEdit() {
            if (!renamer || editing) return;
            setEditing(true);
            input.focus();
            if (input.select) input.select();
        }

        /**
         * Leave edit mode with the operator's text discarded and the confirmed
         * name back. Esc's job, and Esc's alone — nothing here commits by
         * accident.
         * @returns {void}
         */
        function cancel() {
            if (!editing) return;
            input.value = committed;
            sizeToValue();
            setEditing(false);
        }

        /**
         * Send the typed name, and adopt it only once the server has it.
         *
         * @returns {Promise<void>} Never rejects: a failure is the operator's
         *   error line, and the draft and the edit state both survive it so
         *   Save is still there to try again.
         */
        async function save() {
            if (!renamer || !editing) return;
            const next = String(input.value || '').trim();
            if (!next) {
                // Keeps what was typed: an empty name is a thing to fix, not a
                // reason to throw the edit away.
                onError(EMPTY_NAME);
                return;
            }
            if (next === committed) {
                cancel();
                return;
            }
            try {
                await renamer(next);
            } catch (err) {
                console.error('[title-rename] the rename failed', err);
                onError((err && err.message) || 'Could not rename this thread.');
                return;
            }
            committed = next;
            input.value = next;
            sizeToValue();
            setEditing(false);
        }

        /**
         * @param {KeyboardEvent} event
         * @returns {void}
         */
        function onKeydown(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                if (editing) void save();
                else beginEdit();
                return;
            }
            if (event.key === 'Escape' && editing) {
                event.preventDefault();
                // The shell's global Escape backs out of the outermost surface;
                // while a rename is open it belongs to the rename.
                if (event.stopPropagation) event.stopPropagation();
                cancel();
            }
        }

        /**
         * Paint one chrome descriptor's title.
         *
         * Runs on every presence signal and every roster tick, so it moves no
         * node it does not have to and never overwrites what the operator is
         * halfway through typing.
         *
         * @param {{title: string, onRename?: Function}} view  Without
         *   `onRename` the title is plain text and not a tab stop, which is
         *   what an agent conversation and a sealed thread both are.
         * @returns {void}
         */
        function apply(view) {
            const next = (view && view.title) || '';
            renamer = (view && typeof view.onRename === 'function') ? view.onRename : null;
            committed = next;
            if (!renamer) {
                if (editing) setEditing(false);
                if (shape !== 'text') {
                    clear(element);
                    shape = 'text';
                }
                element.textContent = next;
                return;
            }
            if (shape !== 'input') {
                clear(element);
                element.append(input);
                shape = 'input';
            }
            if (editing) return;
            input.value = next;
            sizeToValue();
        }

        return { element, apply, isEditing: () => editing, save, cancel };
    }

    return { createEditableTitle };
})();
