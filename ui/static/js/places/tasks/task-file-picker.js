/**
 * BossMod AI — choose a required file by browsing, never by pasting a path.
 *
 * A modal layer over the task detail's Edit mode (task-edit-files.js opens
 * it). It lists one agent's files exactly as that agent sees them —
 * `GET /api/agents/{id}/desk`, the path system deliverables resolve in — so
 * the path it hands back is one the server can normalize for that assignee.
 *
 * The crumbs and rows are core/file-listing.js's, the desk Files section's
 * own look. Below the list sits a "File name" field meaning "this name in the
 * folder on screen": picking an existing file fills it, and typing a name
 * that is not there yet names a NEW output the agent must produce — the usual
 * case for a required file. "Use this file" stays disabled until the name is
 * a single path segment and the folder is a real one (the `/` mount list is
 * not).
 *
 * A late listing for a folder the operator has already left is dropped by
 * the same load-generation guard the desk browser uses.
 */
const BossModTaskFilePicker = (() => {
    const { h } = BossModDom;
    const LISTING = BossModFileListing;

    const TITLE = 'Choose a file';
    const EMPTY_COPY = 'This folder is empty.';
    /** The mount list: browsable, but not a folder a file can live in. */
    const ROOT = '/';
    const FORM_ID = 'task-file-picker-form';
    const NAME_ID = 'task-file-picker-name';
    const USE_ID = 'task-file-picker-use';

    /**
     * Read one folder's listing.
     *
     * @param {Function} api
     * @param {string} agentId
     * @param {string} path
     * @returns {Promise<object>} The directory payload (`path`, `breadcrumbs`,
     *   `entries`).
     * @throws {Error} (rejects) With the server's `detail` as the message on a
     *   refusal, or when the path is not a folder.
     */
    async function readFolder(api, agentId, path) {
        const url = `/api/agents/${encodeURIComponent(agentId)}/desk?path=${encodeURIComponent(path)}`;
        const res = await api(url, { cache: 'no-store' });
        if (!res.ok) {
            let detail = null;
            try {
                const body = await res.json();
                detail = body && body.detail;
            } catch (err) {
                console.error('[task-file-picker] the refusal had no JSON body', err);
            }
            throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`);
        }
        const payload = await res.json();
        if (!payload || payload.kind !== 'directory') throw new Error('That path is not a folder.');
        return payload;
    }

    /**
     * Whether `name` can be joined onto `folder` as one file path.
     *
     * @param {string|null} folder  The folder on screen; null while loading
     *   or after a failed load.
     * @param {string} name  Trimmed.
     * @returns {boolean}
     */
    function usable(folder, name) {
        return Boolean(folder) && folder !== ROOT && Boolean(name)
            && !name.includes('/') && name !== '.' && name !== '..';
    }

    /**
     * Open the picker as a layer.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string} deps.agentId  Whose view of the files to list: the
     *   draft's assignee, whom the server normalizes the path against.
     * @param {string} deps.startPath  The folder to open on.
     * @param {(path: string) => void} deps.onPick  `<folder>/<name>`, once
     *   "Use this file" is pressed; the layer then closes.
     * @returns {{close: () => void}}
     * @throws {Error} When a dependency is missing.
     */
    function open(deps) {
        const { api, agentId, startPath, onPick } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-file-picker] deps.api is required');
        if (!agentId) throw new Error('[task-file-picker] deps.agentId is required');
        if (!startPath) throw new Error('[task-file-picker] deps.startPath is required');
        if (typeof onPick !== 'function') throw new Error('[task-file-picker] deps.onPick is required');

        const load = BossModGates.createLoadGeneration();
        /** The folder on screen; null while one loads or after a failure. */
        let folder = null;
        /** The path a load is in flight for, so a late answer is dropped. */
        let requested = null;
        /** The folder's entries on screen, to match a typed name against. */
        let listed = [];
        let closed = false;

        const crumbsSlot = h('div', { class: 'task-file-picker-crumbs' });
        const listSlot = h('div', { class: 'task-file-picker-list' });
        const nameInput = h('input', {
            class: 'field-input', id: NAME_ID, type: 'text', autocomplete: 'off', spellcheck: 'false',
            oninput: () => { markSelected(); sync(); },
        });
        const form = h('form', {
            class: 'task-file-picker', id: FORM_ID, novalidate: true,
            onsubmit: (event) => { event.preventDefault(); use(); },
        },
            crumbsSlot,
            listSlot,
            h('div', { class: 'task-file-picker-name' },
                h('label', { class: 'assign-field-label', for: NAME_ID }, 'File name'),
                nameInput));

        const modal = BossModOverlays.createModal({
            title: TITLE,
            body: form,
            size: 'panel',
            // It holds a typed name; a stray click must not lose it.
            closeOnBackdrop: false,
            actions: [
                { label: 'Cancel', tone: 'quiet' },
                { label: 'Use this file', tone: 'primary', id: USE_ID, form: FORM_ID },
            ],
            onClose: () => {
                closed = true;
                load.next();
            },
        });
        const useButton = modal.element.querySelector(`#${USE_ID}`);
        if (!useButton) throw new Error('[task-file-picker] the modal did not render its primary action');

        const typedName = () => nameInput.value.trim();

        /** "Use this file" follows the name and the folder. */
        function sync() {
            useButton.disabled = !usable(folder, typedName());
        }

        /**
         * The file row whose name is the typed name is the selected one; any
         * other name selects none, so a highlighted row never disagrees with
         * the field. Updated in place, so the clicked row keeps focus.
         */
        function markSelected() {
            const name = typedName();
            const chosen = listed.find((entry) => entry.is_dir !== true && String(entry.name) === name);
            listSlot.querySelectorAll('.desk-entry').forEach((row) => {
                if (row.getAttribute('data-is-dir') === '1') return;
                row.setAttribute('aria-pressed', String(Boolean(chosen) && row.getAttribute('data-path') === chosen.path));
            });
        }

        /** A file row selects; a folder row goes in. */
        function onEntry(entry) {
            if (entry.is_dir === true) {
                void go(entry.path);
                return;
            }
            nameInput.value = String(entry.name);
            markSelected();
            sync();
        }

        function renderFolder(payload) {
            folder = String(payload.path);
            listed = Array.isArray(payload.entries) ? payload.entries : [];
            crumbsSlot.replaceChildren(LISTING.breadcrumbs(payload.breadcrumbs, { onCrumb: (path) => { void go(path); } }));
            listSlot.replaceChildren(LISTING.entries(listed, { onEntry, selectedPath: '', emptyCopy: EMPTY_COPY }));
            markSelected();
            sync();
            BossModIcons.paint(form, 'task-file-picker');
        }

        /** The failure, with a way to try again and a way out to `/`. */
        function renderError(path, message) {
            listSlot.replaceChildren(
                h('p', { class: 'context-error', role: 'alert' }, `${message} Path: ${path}`),
                h('div', { class: 'desk-files-recovery' },
                    h('button', {
                        class: 'btn btn-sm', id: 'task-file-picker-retry', type: 'button',
                        onclick: () => { void go(path); },
                    }, 'Retry'),
                    h('button', {
                        class: 'btn btn-sm', id: 'task-file-picker-root', type: 'button',
                        onclick: () => { void go(ROOT); },
                    }, 'Go to /')));
        }

        /**
         * Show one folder.
         * @param {string} path
         * @returns {Promise<void>} Never rejects; a failure is the error state.
         */
        async function go(path) {
            const loadId = load.next();
            requested = path;
            folder = null;
            listed = [];
            crumbsSlot.replaceChildren();
            listSlot.replaceChildren(h('p', { class: 'context-skeleton' }, 'Loading…'));
            sync();
            const live = () => !closed && load.isCurrent(loadId) && requested === path;
            let payload;
            try {
                payload = await readFolder(api, agentId, path);
            } catch (err) {
                if (!live()) return;
                console.error('[task-file-picker] the folder did not load', err);
                renderError(path, (err && err.message) || 'The folder did not load.');
                return;
            }
            if (!live()) return;
            renderFolder(payload);
        }

        /** Hand back `<folder>/<name>` and close; refused while unusable. */
        function use() {
            const name = typedName();
            if (!usable(folder, name)) return;
            onPick(`${folder}/${name}`);
            modal.close();
        }

        void go(startPath);
        return { close: modal.close };
    }

    return { open };
})();
