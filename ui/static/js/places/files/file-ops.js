/**
 * BossMod AI — create, rename, delete, move, copy, and copy-path.
 *
 * Ported from company-file-ops.js. Every dialog is built with BossModDom.h and
 * opened through core/overlays.js, which splits along one line: a question with
 * no input is a `createModal` (Delete), and anything the operator types into
 * goes through file-form.js's slide-over, because a modal action always closes
 * and a failed rename must keep the name they typed.
 *
 * Every call takes the authenticated helper as `api`; this module names no
 * global of its own.
 */
const BossModFileOps = (() => {
    const { h, clear } = BossModDom;
    const FORM = BossModFileForm;

    /**
     * The message a failed response carries.
     *
     * Delegates to the one error formatter the application has,
     * `api-client.js`'s `formatError`, rather than growing a second opinion
     * about what a FastAPI `detail` means. It is reached through `window`
     * because it is a pure formatter published by that module, not an
     * injectable dependency: everything with behaviour still arrives as `api`.
     *
     * @param {Response} res
     * @returns {Promise<string>}
     */
    async function readApiError(res) {
        const payload = await res.json().catch(() => ({}));
        return window.BossModApi.formatError(payload, res.status);
    }

    /**
     * Send a mutating request and read the failure text.
     *
     * @param {Function} api
     * @param {string} url
     * @param {string} method
     * @param {object} body
     * @returns {Promise<object>} The parsed response body.
     * @throws {Error} On a non-OK response, carrying the server's own message
     *   so the dialog can show it rather than a generic failure.
     */
    async function send(api, url, method, body) {
        const res = await api(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await readApiError(res));
        return res.json();
    }

    // ─── Create ───

    /**
     * Ask for a name and create a file or a folder in `parentPath`.
     *
     * @param {object} deps
     * @param {Function} deps.api
     * @param {string} deps.parentPath
     * @param {'file'|'folder'} deps.kind
     * @param {() => void} deps.onComplete
     * @returns {void}
     */
    function showCreateDialog({ api, parentPath, kind, onComplete }) {
        const kindLabel = kind === 'folder' ? 'Folder' : 'File';
        const nameInput = h('input', {
            class: 'file-form-input', type: 'text', required: true,
            placeholder: `${kindLabel} name…`,
        });
        const panel = FORM.openFormPanel({
            title: `New ${kindLabel}`,
            submitLabel: 'Create',
            busyLabel: 'Creating…',
            fields: [FORM.hint(`Create in: ${parentPath}`), FORM.field(`${kindLabel} name`, nameInput)],
            onSubmit: async () => {
                const name = nameInput.value.trim();
                if (!name) throw new Error('A name is required.');
                await send(api, '/api/company/files/create', 'POST', { path: parentPath, name, kind });
                panel.close();
                onComplete();
            },
        });
        nameInput.focus();
    }

    // ─── Rename ───

    /**
     * @param {object} deps
     * @param {Function} deps.api
     * @param {string} deps.path
     * @param {string} deps.name  Current name; prefilled and selected.
     * @param {() => void} deps.onComplete
     * @returns {void}
     */
    function showRenameDialog({ api, path, name, onComplete }) {
        const input = h('input', { class: 'file-form-input', type: 'text', required: true });
        input.value = name;
        const panel = FORM.openFormPanel({
            title: 'Rename',
            submitLabel: 'Rename',
            busyLabel: 'Renaming…',
            fields: [FORM.hint(path), FORM.field('New name', input)],
            onSubmit: async () => {
                const next = input.value.trim();
                if (!next) throw new Error('A name is required.');
                if (next === name) throw new Error('That is already its name.');
                await send(api, '/api/company/files/rename', 'PATCH', { path, new_name: next });
                panel.close();
                onComplete();
            },
        });
        input.focus();
        if (input.select) input.select();
    }

    // ─── Delete ───

    /**
     * Confirm and delete. A question with no input, so it is a modal: Cancel
     * holds focus and Esc agrees with it.
     *
     * @param {object} deps
     * @param {Function} deps.api
     * @param {string} deps.path
     * @param {string} deps.name
     * @param {() => void} deps.onComplete
     * @param {(message: string) => void} deps.onError  Where a failed delete is
     *   reported once the dialog has gone — never only to the console.
     * @returns {void}
     */
    function showDeleteDialog({ api, path, name, onComplete, onError }) {
        BossModOverlays.createModal({
            title: 'Delete',
            body: h('div', {},
                h('p', {}, `Delete ${name}?`),
                FORM.hint('This cannot be undone.')),
            actions: [
                {
                    label: 'Delete',
                    tone: 'danger',
                    onSelect: () => {
                        void (async () => {
                            try {
                                await send(api, '/api/company/files', 'DELETE', { path });
                            } catch (err) {
                                console.error('[file-ops] delete failed', err);
                                onError(`Could not delete ${name}: `
                                    + `${(err && err.message) || 'the request failed.'}`);
                                return;
                            }
                            onComplete();
                        })();
                    },
                },
                { label: 'Cancel', tone: 'quiet' },
            ],
        });
    }

    // ─── Move / copy ───

    /**
     * Browse to a destination folder, then move or copy into it.
     *
     * @param {object} deps
     * @param {Function} deps.api
     * @param {string} deps.path    Source.
     * @param {string} deps.name    Source name, for the title.
     * @param {'move'|'copy'} deps.action
     * @param {() => void} deps.onComplete
     * @returns {void}
     */
    function showMoveOrCopyDialog({ api, path, name, action, onComplete }) {
        const label = action === 'move' ? 'Move' : 'Copy';
        const crumbs = h('div', { class: 'file-form-crumbs' });
        const list = h('div', { class: 'file-form-list' });
        let destination = '/';

        const panel = FORM.openFormPanel({
            title: `${label}: ${name}`,
            submitLabel: `${label} here`,
            busyLabel: `${label === 'Move' ? 'Moving' : 'Copying'}…`,
            fields: [FORM.hint('Navigate to the destination folder'), crumbs, list],
            onSubmit: async () => {
                const url = action === 'move'
                    ? '/api/company/files/move'
                    : '/api/company/files/copy';
                await send(api, url, 'POST', { source: path, destination });
                panel.close();
                onComplete();
            },
        });

        async function browse(target) {
            destination = target;
            clear(list);
            list.append(FORM.hint('Loading…'));
            let payload;
            try {
                const res = await api(
                    `/api/company/files?path=${encodeURIComponent(target)}`, { cache: 'no-store' });
                if (!res.ok) throw new Error(await readApiError(res));
                payload = await res.json();
            } catch (err) {
                console.error('[file-ops] could not list the destination', err);
                clear(list);
                panel.error(`Could not open ${target}: `
                    + `${(err && err.message) || 'the request failed.'}`);
                return;
            }
            panel.error('');
            clear(crumbs);
            (payload.breadcrumbs || []).forEach((crumb, index) => {
                if (index > 0) crumbs.append(h('span', { class: 'file-crumb-sep' }, '/'));
                crumbs.append(h('button', {
                    class: 'file-crumb-btn', type: 'button',
                    onclick: () => { void browse(crumb.path); },
                }, String(crumb.label)));
            });

            clear(list);
            const dirs = (payload.entries || []).filter((entry) => entry.is_dir);
            if (dirs.length === 0) {
                list.append(FORM.hint('No subfolders'));
                return;
            }
            dirs.forEach((dir) => list.append(h('button', {
                class: 'file-form-entry', type: 'button',
                onclick: () => { void browse(dir.path); },
            },
                h('span', { class: 'file-entry-name' }, `${dir.name}/`),
                dir.agent_name ? h('span', { class: 'file-entry-agent' }, dir.agent_name) : null)));
        }

        void browse('/');
    }

    // ─── Copy path ───

    /**
     * Put a path on the clipboard.
     *
     * The execCommand branch is not a fallback hiding a failure: the Clipboard
     * API is unavailable outside a secure context, and this is the documented
     * way to copy there. Ported from company-file-ops.js, which swallowed both
     * failures; here, if neither path copies, the caller is told.
     *
     * @param {string} path
     * @returns {Promise<void>}
     * @throws {Error} When neither path could copy.
     */
    async function copyPath(path) {
        try {
            await navigator.clipboard.writeText(path);
            return;
        } catch (err) {
            console.warn('[file-ops] the clipboard API refused; using the selection path', err);
        }
        const field = h('textarea', { class: 'file-copy-shim', 'aria-hidden': 'true' });
        field.value = path;
        document.body.append(field);
        field.select();
        const copied = document.execCommand('copy');
        field.remove();
        if (!copied) throw new Error('This browser would not copy to the clipboard.');
    }

    return {
        readApiError,
        showCreateDialog,
        showRenameDialog,
        showDeleteDialog,
        showMoveOrCopyDialog,
        copyPath,
    };
})();
