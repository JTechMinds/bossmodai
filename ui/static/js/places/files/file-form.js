/**
 * BossMod AI — the form panel every Files dialog is built from.
 *
 * Create, Rename, Move/Copy and the host-folders allowlist are the same shape:
 * fields, an inline error line, Cancel, and a submit that disables itself while
 * the request is in flight and STAYS OPEN when it fails. That last part is why
 * they are slide-overs rather than modals — a modal action always closes, and a
 * rename that closed on failure would throw away the name the operator typed.
 *
 * Four copies of that shape is what company-file-ops.js had. This is one.
 */
const BossModFileForm = (() => {
    const { h } = BossModDom;

    /**
     * A labelled control.
     *
     * @param {string} text
     * @param {HTMLElement} control
     * @param {string} [id]  Ties the label to the control when the caller wants
     *   the association explicit.
     * @returns {HTMLElement}
     */
    function field(text, control, id) {
        return h('label', { class: 'file-form-field', for: id || null },
            h('span', { class: 'file-form-label' }, text), control);
    }

    /**
     * A hint or description line.
     * @param {string} text
     * @returns {HTMLElement}
     */
    function hint(text) {
        return h('p', { class: 'file-form-hint' }, text);
    }

    /**
     * Open a form panel.
     *
     * @param {object} spec
     * @param {string} spec.title
     * @param {string} spec.submitLabel
     * @param {string} spec.busyLabel
     * @param {Array<HTMLElement|null>} spec.fields
     * @param {Array<{label: string, onSelect: () => void}>} [spec.extraActions]
     *   Buttons left of Cancel, e.g. "Open in Settings".
     * @param {() => Promise<void>} spec.onSubmit  Rejecting shows its message
     *   and leaves the panel exactly as the operator left it.
     * @returns {{ close: () => void, error: (message: string) => void,
     *             element: HTMLElement }}
     */
    function openFormPanel({ title, submitLabel, busyLabel, fields, extraActions, onSubmit }) {
        const errorEl = h('p', { class: 'file-form-error', role: 'alert', hidden: true });
        const submit = h('button', { class: 'btn', type: 'submit' }, submitLabel);
        let busy = false;

        function error(message) {
            errorEl.textContent = message || '';
            errorEl.hidden = !message;
        }

        const actions = h('div', { class: 'file-form-actions' });
        (extraActions || []).forEach((action) => actions.append(h('button', {
            class: 'btn', type: 'button', onclick: action.onSelect,
        }, action.label)));
        actions.append(
            h('button', { class: 'btn', type: 'button', onclick: () => panel.close() }, 'Cancel'),
            submit);

        const form = h('form', {
            class: 'file-form',
            onsubmit: (event) => {
                event.preventDefault();
                if (busy) return;
                busy = true;
                submit.disabled = true;
                submit.textContent = busyLabel;
                error('');
                void Promise.resolve().then(onSubmit).catch((err) => {
                    console.error(`[file-form] ${title} failed`, err);
                    error((err && err.message) || 'The request failed.');
                }).then(() => {
                    busy = false;
                    submit.disabled = false;
                    submit.textContent = submitLabel;
                });
            },
        }, ...(fields || []).filter(Boolean), errorEl, actions);

        const panel = BossModOverlays.slideOver({ title, body: form });
        return { close: panel.close, error, element: panel.element };
    }

    return { openFormPanel, field, hint };
})();
