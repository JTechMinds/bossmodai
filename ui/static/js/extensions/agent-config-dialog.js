/**
 * BossMod AI — one agent's settings for one extension (manifest `agent_config`).
 *
 * A default-size modal from core/overlays.js, opened from the agent's desk.
 * The form is built from the fields the server describes, so this module
 * knows nothing about any one extension:
 *   - the extension's help text first, as text, one paragraph per blank line;
 *   - one labelled input per field: `secret` through BossModSecretField
 *     (never pre-filled; when one is stored the placeholder says a blank keeps
 *     it), `email` as type=email, `text` as text;
 *   - Save verifies on the server before anything is stored. While it runs
 *     the status line says "Verifying connection…"; a refusal shows the
 *     server's message verbatim (e.g. an AADSTS error) and keeps the typing;
 *     success shows the server's `verified` line and leaves a Done button.
 *   - Remove (only when configured) asks first, in a layer over the form.
 *
 * States: loading, load failed (with retry), form, verifying, refused, saved.
 */
const BossModAgentConfigDialog = (() => {
    const { h, clear } = BossModDom;
    const API = BossModExtensionsApi;
    const FORM_ID = 'ext-config-form';
    const SAVE_ID = 'ext-config-save';

    const COPY = Object.freeze({
        loading: 'Loading settings…',
        retry: 'Try again',
        keepSecret: 'Leave blank to keep the current secret',
        show: 'Show',
        save: 'Save',
        verifying: 'Verifying connection…',
        cancel: 'Cancel',
        done: 'Done',
        remove: 'Remove',
        removeBody: 'The stored settings, including the secret, are deleted. The agent loses access until someone sets them up again.',
        removeFailed: 'Couldn’t remove these settings.',
    });

    /**
     * Open the settings dialog.
     *
     * @param {object} options
     * @param {string} options.extensionId
     * @param {string} options.agentId
     * @param {string} options.agentName  Named in the title.
     * @param {() => void} options.onSaved  Called after a successful save or
     *   removal, so the desk can re-read.
     * @returns {{close: () => void}}
     * @throws {Error} When a required option is missing.
     */
    function open(options) {
        const { extensionId, agentId, agentName, onSaved } = options || {};
        if (!extensionId) throw new Error('[agent-config-dialog] extensionId is required');
        if (!agentId) throw new Error('[agent-config-dialog] agentId is required');
        if (!agentName) throw new Error('[agent-config-dialog] agentName is required');
        if (typeof onSaved !== 'function') throw new Error('[agent-config-dialog] onSaved is required');

        const body = h('div', { class: 'ext-config' });
        const statusEl = h('p', { class: 'field-hint ext-config-status', role: 'status', 'aria-live': 'polite' });
        const errorEl = h('div', { class: 'callout', 'data-tone': 'alert', role: 'alert' });
        errorEl.hidden = true;
        let config = null;
        let inputs = {};
        let busy = false;
        let closed = false;

        const modal = BossModOverlays.createModal({
            title: `Settings for ${agentName}`,
            body,
            actions: [],
            onClose: () => { closed = true; },
        });

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
                h('button', { class: 'btn btn-sm', id: 'ext-config-retry', type: 'button', onclick: () => { void load(); } }, COPY.retry));
            modal.setActions([{ label: COPY.cancel, tone: 'quiet' }]);
        }

        function fieldControl(field) {
            const id = `ext-config-${field.key}`;
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
                class: 'ext-config-form', id: FORM_ID, novalidate: true,
                onsubmit: (event) => { event.preventDefault(); void save(); },
            },
            h('div', { class: 'ext-config-help' }, help.map((para) => h('p', { class: 'field-hint' }, para))),
            config.fields.map(fieldControl),
            statusEl,
            errorEl);
            body.append(form);
            const actions = [];
            if (config.configured) {
                actions.push({ label: COPY.remove, tone: 'danger', id: 'ext-config-remove', keepOpen: true, onSelect: confirmRemove });
            }
            actions.push({ label: COPY.cancel, tone: 'quiet' });
            actions.push({ label: COPY.save, tone: 'primary', id: SAVE_ID, form: FORM_ID });
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
            const saveBtn = modal.element.querySelector(`#${SAVE_ID}`);
            if (saveBtn) saveBtn.disabled = true;
            showError('');
            statusEl.textContent = COPY.verifying;
            try {
                const result = await API.saveAgentConfig(extensionId, agentId, values());
                if (closed) return;
                statusEl.textContent = '';
                config = result;
                onSaved();
                renderSaved(result.verified);
            } catch (err) {
                if (closed) return;
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
                h('p', { class: 'callout-body' }, verified)));
            modal.setActions([{ label: COPY.done, tone: 'primary', id: 'ext-config-done' }]);
            const done = modal.element.querySelector('#ext-config-done');
            if (done) done.focus();
        }

        function confirmRemove() {
            BossModOverlays.createModal({
                title: `Remove ${config.label} for ${agentName}?`,
                body: COPY.removeBody,
                actions: [
                    { label: COPY.remove, tone: 'danger', id: 'ext-config-remove-confirm', onSelect: () => { void remove(); } },
                    { label: COPY.cancel, tone: 'quiet' },
                ],
            });
        }

        async function remove() {
            try {
                await API.deleteAgentConfig(extensionId, agentId);
            } catch (err) {
                if (!closed) showError(String((err && err.message) || COPY.removeFailed));
                return;
            }
            onSaved();
            if (!closed) modal.close();
        }

        async function load() {
            renderLoading();
            try {
                config = await API.getAgentConfig(extensionId, agentId);
            } catch (err) {
                if (!closed) renderLoadFailed(String((err && err.message) || err));
                return;
            }
            if (!closed) renderForm();
        }

        void load();
        return { close: () => modal.close() };
    }

    return { open };
})();
