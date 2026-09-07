/**
 * BossMod AI — the host-folders consent panel.
 *
 * Ported from company-files.js. The copy is byte-identical on purpose: this
 * panel grants agents read/write access to directories outside the company
 * workspace, so what it promises and what it refuses is the operator's only
 * statement of that boundary. It writes the same `workspace_host_roots`
 * allowlist as Settings → CLI Policy, and links there rather than owning a
 * second version of the setting.
 *
 * The original swallowed a failed read of the current value with a bare
 * `catch {}`, so a panel showing a stale list looked identical to one showing
 * the saved list — and Save would then overwrite the real allowlist with what
 * the operator could see. That failure is now on screen.
 */
const BossModHostRoots = (() => {
    const { h } = BossModDom;
    const FORM = BossModFileForm;

    const TITLE = 'Host folders';
    const DESCRIPTION = 'Optional extra directories a named absolute path may open, read, or '
        + 'edit. Writes the same allowlist as Settings → CLI Policy → Host workspace roots. '
        + 'This is not a full host mount. /, /etc, /proc, /sys, /dev, and /root are rejected.';
    const FIELD_LABEL = 'Allowlisted host folders';
    const PLACEHOLDER = '/home/you/src';
    const STALE_COPY = 'Could not read the saved allowlist; the folders below are the ones '
        + 'Files last loaded. Saving would replace whatever is stored.';

    /** The button label the workspace-note bar shows, by whether any exist. */
    const ADD_LABEL = 'Add host folder';
    const MANAGE_LABEL = 'Manage host folders';

    /**
     * @param {string[]} roots
     * @returns {string}
     */
    function buttonLabel(roots) {
        return roots.length ? MANAGE_LABEL : ADD_LABEL;
    }

    /**
     * Open the panel.
     *
     * @param {object} deps
     * @param {Function} deps.api    Authenticated fetch helper.
     * @param {string[]} deps.roots  What Files last loaded, shown until the
     *   saved value arrives.
     * @param {() => void} deps.onSaved  Called after a successful write.
     * @returns {{ close: () => void }}
     * @throws {Error} When api is missing.
     */
    function openHostRoots(deps) {
        const { api, roots, onSaved } = deps || {};
        if (typeof api !== 'function') throw new Error('[host-roots] deps.api is required');

        const input = h('textarea', {
            class: 'file-form-input file-form-textarea',
            id: 'host-roots-input',
            rows: '5',
            placeholder: PLACEHOLDER,
        });
        input.value = (roots || []).join('\n');

        const panel = FORM.openFormPanel({
            title: TITLE,
            submitLabel: 'Save',
            busyLabel: 'Saving…',
            fields: [FORM.hint(DESCRIPTION), FORM.field(FIELD_LABEL, input, 'host-roots-input')],
            extraActions: [{
                label: 'Open in Settings',
                onSelect: () => {
                    panel.close();
                    SettingsView.open('cli-policy', {
                        tab: 'settings', focusKey: 'workspace_host_roots',
                    });
                },
            }],
            onSubmit: async () => {
                const value = String(input.value || '');
                const res = await api(
                    `/api/settings/workspace_host_roots?value=${encodeURIComponent(value)}&category=cli_policy`,
                    { method: 'PUT' });
                if (!res.ok) throw new Error(await BossModFileOps.readApiError(res));
                panel.close();
                onSaved();
            },
        });

        /**
         * Replace the textarea with the value actually stored.
         *
         * @returns {Promise<void>} Never rejects. A failure leaves the last
         *   known list in place AND says so, because saving over an allowlist
         *   you could not read is how an operator revokes access by accident.
         */
        async function loadSaved() {
            try {
                const res = await api('/api/settings?category=cli_policy', { cache: 'no-store' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const rows = await res.json();
                const row = Array.isArray(rows)
                    ? rows.find((item) => item.key === 'workspace_host_roots')
                    : null;
                if (row && typeof row.value === 'string') input.value = row.value;
            } catch (err) {
                console.error('[host-roots] could not read the saved allowlist', err);
                panel.error(STALE_COPY);
            }
        }

        void loadSaved();
        input.focus();
        return { close: panel.close };
    }

    return { openHostRoots, buttonLabel, TITLE, DESCRIPTION, ADD_LABEL, MANAGE_LABEL };
})();
