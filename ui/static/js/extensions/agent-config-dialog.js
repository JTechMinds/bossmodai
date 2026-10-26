/**
 * BossMod AI — one agent's settings for one extension (manifest `agent_config`).
 *
 * A default-size modal from core/overlays.js, opened from the agent's desk or
 * as a layer over the Extensions dialog's Configure agents table. The form
 * itself (fields, secrets, Save and its states) is BossModConfigForm, driven
 * here by the per-agent endpoints, so this module knows nothing about any one
 * extension. What it adds is Remove (only when configured), which asks first,
 * in a layer over the form.
 */
const BossModAgentConfigDialog = (() => {
    const { h } = BossModDom;
    const API = BossModExtensionsApi;
    const ID_PREFIX = 'ext-config';

    const COPY = Object.freeze({
        cancel: 'Cancel',
        remove: 'Remove',
        removeBody: 'The agent stops using this until someone sets it up again. Its settings are removed.',
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
     * @param {() => void} options.onClosed  Called once the dialog closes, however
     *   it closes, so the desk can re-read state that changed while it was open
     *   (e.g. the first new-mail check after a save).
     * @returns {{close: () => void}}
     * @throws {Error} When a required option is missing.
     */
    function open(options) {
        const { extensionId, agentId, agentName, onSaved, onClosed } = options || {};
        if (!extensionId) throw new Error('[agent-config-dialog] extensionId is required');
        if (!agentId) throw new Error('[agent-config-dialog] agentId is required');
        if (!agentName) throw new Error('[agent-config-dialog] agentName is required');
        if (typeof onSaved !== 'function') throw new Error('[agent-config-dialog] onSaved is required');
        if (typeof onClosed !== 'function') throw new Error('[agent-config-dialog] onClosed is required');

        const body = h('div', { class: 'ext-config' });
        let form = null;
        let closed = false;

        const modal = BossModOverlays.createModal({
            title: `Settings for ${agentName}`,
            body,
            actions: [],
            onClose: () => { closed = true; if (form) form.dispose(); onClosed(); },
        });

        form = BossModConfigForm.create({
            modal,
            body,
            idPrefix: ID_PREFIX,
            adapter: {
                load: () => API.getAgentConfig(extensionId, agentId),
                save: (values) => API.saveAgentConfig(extensionId, agentId, values),
            },
            onSaved: () => onSaved(),
            extraActions: (config) => (config.configured
                ? [{ label: COPY.remove, tone: 'danger', id: 'ext-config-remove', keepOpen: true, onSelect: confirmRemove }]
                : []),
        });

        function confirmRemove() {
            BossModOverlays.createModal({
                title: `Remove ${form.config().label} for ${agentName}?`,
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
                if (!closed) form.showError(String((err && err.message) || COPY.removeFailed));
                return;
            }
            onSaved();
            if (!closed) modal.close();
        }

        void form.load();
        return { close: () => modal.close() };
    }

    return { open };
})();
