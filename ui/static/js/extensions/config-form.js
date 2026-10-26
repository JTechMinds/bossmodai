/**
 * BossMod AI — the form for a manifest-declared set of settings.
 *
 * Shared by the two dialogs that edit one: an agent's settings for an
 * extension (agent-config-dialog.js, `agent_config`) and the extension's own
 * (extension-settings-dialog.js, `settings`). The caller owns the modal and
 * supplies a `{load, save}` adapter for its endpoints; this owns everything
 * about the form:
 *   - the extension's help text first, as text, one paragraph per blank line;
 *   - one labelled input per field the server describes: `secret` through
 *     BossModSecretField (never pre-filled; when one is stored the placeholder
 *     says a blank keeps it), `email` as type=email, `number` as type=number
 *     with the server's min/max and its default as placeholder (and value
 *     until one is saved), `text` as text;
 *   - Save verifies on the server before anything is stored. While it runs
 *     the status line says "Checking…"; a refusal shows the server's message
 *     verbatim and keeps the typing; success shows the extension's own
 *     `verified` text (what it checked, in its words) — or "Saved." when the
 *     server verified nothing — and leaves a Done button.
 *
 * States: loading, load failed (with retry), form, verifying, refused, saved.
 */
const BossModConfigForm = (() => {
    const { h, clear } = BossModDom;

    const COPY = Object.freeze({
        loading: 'Loading settings…',
        retry: 'Try again',
        keepSecret: 'Leave blank to keep the current secret',
        show: 'Show',
        save: 'Save',
        verifying: 'Checking…',
        cancel: 'Cancel',
        done: 'Done',
        saved: 'Saved.',
    });

    /**
     * Build the form inside a caller-owned modal.
     *
     * @param {object} options
     * @param {{setActions: Function, element: Element}} options.modal  The modal
     *   from BossModOverlays.createModal; the form sets its actions.
     * @param {Element} options.body  The modal body the form renders into.
     * @param {string} options.idPrefix  Prefix of every element id (`ext-config`
     *   gives `ext-config-form`, `ext-config-save`, `ext-config-<key>`, …).
     * @param {{load: () => Promise<object>, save: (values: Object<string, string>) => Promise<object>}} options.adapter
     *   `load` resolves the server's `{label, help, configured, fields}`;
     *   `save` resolves the same plus `verified` (string or null) and rejects
     *   with an Error whose message is the server's.
     * @param {(result: object) => void} options.onSaved  Called after a
     *   successful save.
     * @param {(config: object) => object[]} [options.extraActions]  Modal actions
     *   placed before Cancel and Save, given the loaded config (e.g. Remove).
     * @returns {{load: () => Promise<void>, dispose: () => void,
     *   config: () => (object|null), showError: (message: string) => void}}
     * @throws {Error} When a required option is missing.
     */
    function create(options) {
        const { modal, body, idPrefix, adapter, onSaved, extraActions } = options || {};
        if (!modal) throw new Error('[config-form] modal is required');
        if (!body) throw new Error('[config-form] body is required');
        if (!idPrefix) throw new Error('[config-form] idPrefix is required');
        if (!adapter || typeof adapter.load !== 'function' || typeof adapter.save !== 'function') {
            throw new Error('[config-form] adapter.load and adapter.save are required');
        }
        if (typeof onSaved !== 'function') throw new Error('[config-form] onSaved is required');

        const formId = `${idPrefix}-form`;
        const saveId = `${idPrefix}-save`;
        const statusEl = h('p', { class: 'field-hint ext-config-status', role: 'status', 'aria-live': 'polite' });
        const errorEl = h('div', { class: 'callout', 'data-tone': 'alert', role: 'alert' });
        errorEl.hidden = true;
        let config = null;
        let inputs = {};
        let busy = false;
        let disposed = false;

        function showError(message) {
            clear(errorEl);
            errorEl.append(h('p', { class: 'callout-body' }, message));
            errorEl.hidden = !message;
            // The form can be taller than the dialog; keep the refusal in view.
            if (message && typeof errorEl.scrollIntoView === 'function') errorEl.scrollIntoView({ block: 'nearest' });
        }

        function renderLoading() {
            clear(body);
            body.append(h('p', { class: 'field-hint', role: 'status' }, COPY.loading));
            modal.setActions([{ label: COPY.cancel, tone: 'quiet' }]);
        }

        function renderLoadFailed(message) {
            clear(body);
            body.append(
                h('p', { class: 'field-hint', role: 'alert' }, message),
                h('button', { class: 'btn btn-sm', id: `${idPrefix}-retry`, type: 'button', onclick: () => { void load(); } }, COPY.retry));
            modal.setActions([{ label: COPY.cancel, tone: 'quiet' }]);
        }

        function fieldControl(field) {
            const id = `${idPrefix}-${field.key}`;
            const label = h('label', { class: 'field-label', for: id }, field.label);
            if (field.kind === 'secret') {
                const input = h('input', {
                    class: 'field-input bm-secret-masked', id, type: 'text', autocomplete: 'off', spellcheck: 'false',
                    autocapitalize: 'off', 'data-kind': 'secret',
                    placeholder: field.set ? COPY.keepSecret : null,
                    required: field.required && !field.set ? true : null,
                });
                BossModSecretField.bind(input);
                const reveal = h('button', {
                    class: 'btn btn-sm', type: 'button', 'aria-pressed': 'false',
                    'aria-controls': id,
                    onclick: (event) => BossModSecretField.toggle(input, event.currentTarget),
                }, COPY.show);
                inputs[field.key] = { field, input };
                return h('div', { class: 'field' }, label, h('div', { class: 'ext-config-secret' }, input, reveal));
            }
            if (field.kind === 'number') {
                const input = h('input', {
                    class: 'field-input', id, type: 'number', step: '1', inputmode: 'numeric', autocomplete: 'off',
                    min: field.min === null || field.min === undefined ? null : String(field.min),
                    max: field.max === null || field.max === undefined ? null : String(field.max),
                    placeholder: field.default, required: field.required ? true : null,
                });
                input.value = field.value || '';
                inputs[field.key] = { field, input };
                return h('div', { class: 'field' }, label, input);
            }
            const input = h('input', {
                class: 'field-input', id, type: field.kind === 'email' ? 'email' : 'text',
                autocomplete: 'off', spellcheck: 'false', required: field.required ? true : null,
            });
            input.value = field.value || '';
            inputs[field.key] = { field, input };
            return h('div', { class: 'field' }, label, input);
        }

        function renderForm() {
            clear(body);
            inputs = {};
            const help = String(config.help || '').split(/\n\s*\n/).map((para) => para.trim()).filter(Boolean);
            const form = h('form', {
                class: 'ext-config-form', id: formId, novalidate: true,
                onsubmit: (event) => { event.preventDefault(); void save(); },
            },
            h('div', { class: 'ext-config-help' }, help.map((para) => h('p', { class: 'field-hint' }, para))),
            config.fields.map(fieldControl),
            statusEl,
            errorEl);
            body.append(form);
            const actions = extraActions ? extraActions(config) : [];
            actions.push({ label: COPY.cancel, tone: 'quiet' });
            actions.push({ label: COPY.save, tone: 'primary', id: saveId, form: formId });
            modal.setActions(actions);
        }

        function values() {
            const out = {};
            Object.keys(inputs).forEach((key) => {
                const { field, input } = inputs[key];
                // A blank secret is sent as "": the server keeps the stored one.
                out[key] = field.kind === 'secret' ? BossModSecretField.read(input) : String(input.value || '').trim();
            });
            return out;
        }

        async function save() {
            if (busy) return;
            busy = true;
            const saveBtn = modal.element.querySelector(`#${saveId}`);
            if (saveBtn) saveBtn.disabled = true;
            showError('');
            statusEl.textContent = COPY.verifying;
            try {
                const result = await adapter.save(values());
                if (disposed) return;
                statusEl.textContent = '';
                config = result;
                onSaved(result);
                renderSaved(result.verified);
            } catch (err) {
                if (disposed) return;
                statusEl.textContent = '';
                showError(String((err && err.message) || err));
                if (saveBtn) saveBtn.disabled = false;
            } finally {
                busy = false;
            }
        }

        function renderSaved(verified) {
            clear(body);
            body.append(h('div', { class: 'callout', 'data-tone': 'ok', role: 'status' },
                h('p', { class: 'callout-body' }, verified || COPY.saved)));
            modal.setActions([{ label: COPY.done, tone: 'primary', id: `${idPrefix}-done` }]);
            const done = modal.element.querySelector(`#${idPrefix}-done`);
            if (done) done.focus();
        }

        async function load() {
            renderLoading();
            try {
                config = await adapter.load();
            } catch (err) {
                if (!disposed) renderLoadFailed(String((err && err.message) || err));
                return;
            }
            if (!disposed) renderForm();
        }

        return {
            load,
            dispose: () => { disposed = true; },
            config: () => config,
            showError,
        };
    }

    return { create };
})();
