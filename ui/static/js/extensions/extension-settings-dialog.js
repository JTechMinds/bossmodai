/**
 * BossMod AI — an extension's own settings (manifest `settings`).
 *
 * A default-size modal from core/overlays.js, opened by the Settings button on
 * an Extensions card as a layer over the Extensions dialog, whether the
 * extension is on or off (a value like a port can be picked before turning it
 * on). The form is BossModConfigForm driven by the extension-wide endpoints,
 * the same form the per-agent settings use, so this module knows nothing about
 * any one extension.
 *
 * A save made while the extension is on is verified by the extension first
 * (e.g. that a port is free) and then applied to the running extension. When
 * applying it fails the server has already changed the extension's state, so
 * the card is told to re-read (`onChanged`) before the refusal is shown.
 */
const BossModExtensionSettingsDialog = (() => {
    const { h } = BossModDom;
    const API = BossModExtensionsApi;
    const ID_PREFIX = 'ext-settings';
    // The server's code for a save whose values were stored but whose running
    // extension could not apply them.
    const APPLY_FAILED = 'SERVICE_START_FAILED';

    /**
     * Open the settings dialog.
     *
     * @param {object} options
     * @param {string} options.extensionId
     * @param {string} options.extensionName  Named in the title.
     * @param {() => void} options.onChanged  Called after a save (or a save that
     *   stored values but could not apply them), so the card re-reads.
     * @returns {{close: () => void}}
     * @throws {Error} When a required option is missing.
     */
    function open(options) {
        const { extensionId, extensionName, onChanged } = options || {};
        if (!extensionId) throw new Error('[extension-settings-dialog] extensionId is required');
        if (!extensionName) throw new Error('[extension-settings-dialog] extensionName is required');
        if (typeof onChanged !== 'function') throw new Error('[extension-settings-dialog] onChanged is required');

        const body = h('div', { class: 'ext-config' });
        let form = null;

        const modal = BossModOverlays.createModal({
            title: `${extensionName} settings`,
            body,
            actions: [],
            onClose: () => { if (form) form.dispose(); },
        });

        form = BossModConfigForm.create({
            modal,
            body,
            idPrefix: ID_PREFIX,
            adapter: {
                load: () => API.getSettings(extensionId),
                save: async (values) => {
                    try {
                        return await API.putSettings(extensionId, values);
                    } catch (err) {
                        if (err && err.code === APPLY_FAILED) onChanged();
                        throw err;
                    }
                },
            },
            onSaved: () => onChanged(),
        });

        void form.load();
        return { close: () => modal.close() };
    }

    return { open };
})();
