/**
 * BossMod AI — a name you can rename in place.
 *
 * The second click-to-rename in the app (the conversation title,
 * conversation/title-rename.js, was the first), built as one component so
 * the next one does not become a third copy. The semantics are the title's:
 *
 * - At rest it is the name and nothing else: no border, no fill — at most a
 *   quiet visible label before it (`prefix`, e.g. "Floor:"), which is then
 *   the field's accessible name. Click it, or Tab to it and press Enter, to
 *   edit. A hairline appears
 *   under the row, with a ✓ and a ✕ beside the text; at rest neither exists.
 * - It is ONE control in two states, not a label that swaps for an input. A
 *   read-only text input already looks like text, is already a tab stop, and
 *   already carries a caret when it stops being read-only; a swap would lose
 *   the caret and the focus at the moment the operator commits to editing.
 * - ✓ or Enter saves; ✕ or Esc cancels and puts the saved name back. Esc is
 *   the rename's while it is open: it does not reach the dialog behind, whose
 *   Esc handler listens on `document` (core/overlay-focus.js).
 * - It is NOT optimistic. The name changes only once `onRename` resolves. A
 *   failure keeps the draft, keeps edit mode open, and says why on the
 *   component's own error line (role="alert"). An empty name is refused the
 *   same way: a thing to fix, not a reason to throw the edit away.
 *
 * The ✓ and ✕ wear the conversation title's pair: the same icons (`check`,
 * `x`), the same button classes, and the same --ok / --alert tones.
 */
const BossModInlineRename = (() => {
    const { h, clear } = BossModDom;

    /**
     * Build one renameable name.
     *
     * @param {object} opts
     * @param {string} [opts.label]  The field's accessible name when nothing
     *   visible names it. It reads as text, so its name has to say what it is.
     * @param {string} [opts.prefix]  A visible label before the name ("Floor:"),
     *   rendered as the input's `<label for>`, so it IS the accessible name.
     *   Give exactly one of `label` and `prefix`; `prefix` needs `id`.
     * @param {string} opts.placeholder  Shown when the name is empty.
     * @param {number} opts.maxLength  The server's cap.
     * @param {string} opts.emptyMessage  What an empty name is told.
     * @param {string} opts.saveLabel  The ✓'s accessible name and tooltip.
     * @param {string} opts.cancelLabel  The ✕'s accessible name and tooltip.
     * @param {string} opts.value  The name the server has now.
     * @param {(next: string) => Promise<void>} opts.onRename  Performs the
     *   rename; rejects with an Error whose message is shown on failure.
     * @param {string} [opts.id]  The input's id; required with `prefix`.
     * @returns {{element: HTMLElement, input: HTMLInputElement,
     *   isEditing: () => boolean, save: () => Promise<void>, cancel: () => void}}
     *   `save` never rejects: a failure is the error line's.
     * @throws {Error} When a required option is missing — a rename nobody can
     *   name, or that renames nothing, is a dead control.
     */
    function create(opts) {
        const {
            label, prefix, placeholder, maxLength, emptyMessage, saveLabel, cancelLabel, value, onRename, id,
        } = opts || {};
        for (const [key, given] of Object.entries({ placeholder, emptyMessage, saveLabel, cancelLabel })) {
            if (!given) throw new Error(`[inline-rename] opts.${key} is required`);
        }
        // One name, from one place: two would be announced twice or disagree.
        if (Boolean(label) === Boolean(prefix)) {
            throw new Error('[inline-rename] give exactly one of opts.label and opts.prefix');
        }
        if (prefix && !id) throw new Error('[inline-rename] opts.prefix needs opts.id for its <label for>');
        if (!Number.isInteger(maxLength) || maxLength < 1) {
            throw new Error('[inline-rename] opts.maxLength must be a positive integer');
        }
        if (typeof onRename !== 'function') throw new Error('[inline-rename] opts.onRename is required');

        /** The last name the SERVER confirmed. Cancel restores this. */
        let committed = String(value || '');
        let editing = false;
        let saving = false;

        const input = h('input', {
            class: 'inline-rename-input',
            type: 'text',
            id: id || null,
            'aria-label': prefix ? null : label,
            placeholder,
            maxlength: String(maxLength),
            autocomplete: 'off',
            readonly: true,
            onclick: () => beginEdit(),
            onkeydown: (event) => onKeydown(event),
        });
        input.value = committed;

        // A keyboard activation (click `detail` 0) puts focus back on the name,
        // because the button it was on is about to go. A mouse one does not:
        // a focused text field counts as :focus-visible in every browser, so
        // the at-rest focus hairline would show after every mouse save.
        const icon = (name, className, text, onSelect) => h('button', {
            class: `btn btn-sm conversation-action ${className}`,
            type: 'button',
            'aria-label': text,
            'data-tooltip': text,
            onclick: async (event) => {
                await onSelect();
                if (event && event.detail === 0 && !editing) input.focus();
            },
        }, h('i', { 'data-lucide': name, 'aria-hidden': 'true' }));
        const saveButton = icon('check', 'inline-rename-save', saveLabel, () => save());
        const cancelButton = icon('x', 'inline-rename-cancel', cancelLabel, () => cancel());
        // Mounted only while editing, so at rest the row is the name alone.
        const buttons = h('span', { class: 'inline-rename-actions' });
        const row = h('div', { class: 'inline-rename-row', 'data-editing': 'false' },
            prefix ? h('label', { class: 'inline-rename-prefix', for: id }, prefix) : null,
            input,
            buttons);
        const error = h('p', { class: 'context-error', role: 'alert' });
        const element = h('div', { class: 'inline-rename' }, row, error);

        function setEditing(on) {
            editing = on;
            input.readOnly = !on;
            row.setAttribute('data-editing', String(on));
            clear(buttons);
            if (on) {
                buttons.append(saveButton, cancelButton);
                BossModIcons.paint(buttons, 'inline-rename');
            }
        }

        function beginEdit() {
            if (editing) return;
            error.textContent = '';
            setEditing(true);
            input.focus();
            if (input.select) input.select();
        }

        /** Leave edit mode with the draft discarded and the confirmed name back. */
        function cancel() {
            if (!editing || saving) return;
            input.value = committed;
            error.textContent = '';
            setEditing(false);
        }

        async function save() {
            if (!editing || saving) return;
            const next = String(input.value || '').trim();
            if (!next) {
                error.textContent = emptyMessage;
                input.focus();
                return;
            }
            if (next === committed) {
                cancel();
                return;
            }
            saving = true;
            saveButton.disabled = true;
            error.textContent = '';
            try {
                await onRename(next);
            } catch (err) {
                console.error('[inline-rename] the rename failed', err);
                error.textContent = (err && err.message) || 'The name could not be saved.';
                return;
            } finally {
                saving = false;
                saveButton.disabled = false;
            }
            committed = next;
            input.value = next;
            setEditing(false);
        }

        function onKeydown(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                if (editing) void save();
                else beginEdit();
                return;
            }
            if (event.key === 'Escape' && editing) {
                event.preventDefault();
                // The dialog's Esc listens on `document`; while a rename is
                // open the key is the rename's, so it must not bubble there.
                event.stopPropagation();
                cancel();
            }
        }

        return { element, input, isEditing: () => editing, save, cancel };
    }

    return { create };
})();
