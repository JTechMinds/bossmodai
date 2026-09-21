/**
 * BossMod AI — the form panel every Files dialog is built from.
 *
 * Create, Rename, Move/Copy and the host-folders allowlist are the same shape:
 * fields, an inline error line, Cancel, and a submit that disables itself while
 * the request is in flight and STAYS OPEN when it fails — a rename that closed
 * on failure would throw away the name the operator typed. They are form
 * modals: the fields are the body, and the submit is pinned in the footer band
 * through createModal's `form:` action, which submits without closing.
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

    /** The one form panel's form, and its pinned submit. One at a time: a
     *  modal blocks every control that could open a second. */
    const FORM_ID = 'file-form';
    const SUBMIT_ID = 'file-form-submit';

    /**
     * Open a form panel.
     *
     * @param {object} spec
     * @param {string} spec.title
     * @param {string} spec.submitLabel
     * @param {string} spec.busyLabel
     * @param {Array<HTMLElement|null>} spec.fields
     * @param {Array<{label: string, onSelect: () => void}>} [spec.extraActions]
     *   Buttons left of Cancel, e.g. "Open in Settings". The dialog closes
     *   after one runs, as every non-submit action does.
     * @param {() => Promise<void>} spec.onSubmit  Rejecting shows its message
     *   and leaves the panel exactly as the operator left it.
     * @returns {{ close: () => void, error: (message: string) => void,
     *             element: HTMLElement }}
     */
    function openFormPanel({ title, submitLabel, busyLabel, fields, extraActions, onSubmit }) {
        const errorEl = h('p', { class: 'file-form-error', role: 'alert', hidden: true });
        let busy = false;
        let modal = null;

        function error(message) {
            errorEl.textContent = message || '';
            errorEl.hidden = !message;
        }

        const form = h('form', {
            class: 'file-form',
            id: FORM_ID,
            onsubmit: (event) => {
                event.preventDefault();
                if (busy) return;
                // The submit lives in the footer band, outside this form; the
                // `form` attribute is what ties it back.
                const submit = modal.element.querySelector(`#${SUBMIT_ID}`);
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
        }, ...(fields || []).filter(Boolean), errorEl);

        modal = BossModOverlays.createModal({
            title,
            body: form,
            actions: [
                ...(extraActions || []).map((action) => ({
                    label: action.label, onSelect: action.onSelect,
                })),
                { label: 'Cancel' },
                { label: submitLabel, tone: 'primary', id: SUBMIT_ID, form: FORM_ID },
            ],
        });
        return { close: modal.close, error, element: modal.element };
    }

    return { openFormPanel, field, hint };
})();
