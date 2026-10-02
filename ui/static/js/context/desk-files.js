/**
 * BossMod AI — the Files section of an agent's desk.
 *
 * Ported from agent-context.js. Two things changed and both are deliberate:
 * the DOM is built with BossModDom.h rather than assembled from markup
 * strings, because entry names are file names off disk and must never be
 * interpolated; and the folder-opener prompt moved to context/desk-opener.js.
 *
 * Two guards, both load-bearing and both kept from the original: the load
 * generation drops a response for an agent the operator has left, and the
 * active-path check drops a response for a folder they have navigated out of.
 * Either one alone still paints a stale listing.
 */
const BossModDeskFiles = (() => {
    const { h, clear } = BossModDom;
    // The crumbs and the entry rows are core/file-listing.js's, shared with
    // the task detail's file picker; this module owns the fetching around them.
    const LISTING = BossModFileListing;

    const ROOT_PATH = '/me';
    const EMPTY_COPY = 'This folder is empty.';
    /** Roots the operator cannot go up out of. */
    const TOP_PATHS = Object.freeze(['/', '/me', '/projects']);

    /**
     * The parent of a desk path. Ported verbatim: the roots are their own
     * parents, so "Up" is never a way out of the desk.
     *
     * @param {string} path
     * @returns {string}
     */
    function parentDeskPath(path) {
        if (!path || path === '/') return ROOT_PATH;
        if (path === '/me') return '/me';
        if (path === '/projects') return '/projects';
        const parts = String(path).split('/').filter(Boolean);
        parts.pop();
        return parts.length ? `/${parts.join('/')}` : ROOT_PATH;
    }

    /**
     * Every path a write to `path` could have changed the listing of: the path
     * itself and each of its ancestors. Ported from agent-context.js, where it
     * decided which cached listings to drop; here it decides whether the folder
     * on screen is now out of date.
     *
     * @param {string} path
     * @returns {Set<string>}
     */
    function affectedPaths(path) {
        const normalized = String(path || '/');
        const paths = new Set(['/']);
        let current = '';
        normalized.split('/').filter(Boolean).forEach((part) => {
            current += `/${part}`;
            paths.add(current);
        });
        if (normalized !== '/') paths.add(normalized);
        return paths;
    }

    /**
     * Build the desk browser.
     *
     * @param {object} deps
     * @param {Function} deps.api      Authenticated fetch helper.
     * @param {object}   deps.bus      Topic bus. An agent writing a file emits
     *   `chat_message` with a `desk_path`; without it the operator would sit
     *   watching a folder that had already changed underneath them.
     * @param {string}   deps.agentId
     * @returns {{ element: HTMLElement,
     *             open: (path: string) => Promise<void>,
     *             destroy: () => void }}
     * @throws {Error} When api, bus, or agentId is missing.
     */
    function createDeskFiles(deps) {
        const { api, bus, agentId } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-files] deps.api is required');
        if (!bus) throw new Error('[desk-files] deps.bus is required');
        if (!agentId) throw new Error('[desk-files] deps.agentId is required');

        const load = BossModGates.createLoadGeneration();
        const disposers = [];
        const element = h('div', { class: 'desk-files' });
        let activePath = ROOT_PATH;
        let destroyed = false;

        function deskUrl(path) {
            return `/api/agents/${agentId}/desk?path=${encodeURIComponent(path)}`;
        }

        /**
         * Is this response still the one the operator is waiting for?
         *
         * @param {number} loadId
         * @param {string} requestedPath
         * @returns {boolean}
         */
        function isLive(loadId, requestedPath) {
            return !destroyed && load.isCurrent(loadId) && activePath === requestedPath;
        }

        /**
         * One icon-only control on the toolbar: the head's icon-button shape,
         * named twice — accessible name and tooltip — by one string.
         *
         * @param {string} id  Kept from the link era, so tests and focus can
         *   still address each control by name.
         * @param {string} icon  A lucide name.
         * @param {string} label
         * @param {() => void} onclick
         * @returns {HTMLElement}
         */
        function toolButton(id, icon, label, onclick) {
            return h('button', {
                class: 'header-icon-btn desk-files-tool',
                id,
                type: 'button',
                'aria-label': label,
                'data-tooltip': label,
                onclick,
            }, h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }));
        }

        /**
         * Where to go from the folder on screen, right of the crumbs.
         *
         * Icons, not the four blue words (`Projects / Open Folder / Refresh /
         * Up`) that used to sit on their own line: each is scoped to the path
         * on screen, so they share the crumbs' row. The root switch keeps a
         * word beside its glyph, because it names WHERE it goes.
         *
         * @param {string} path
         * @returns {HTMLElement}
         */
        function controls(path) {
            const goingToProjects = !String(path).startsWith('/projects');
            const rootTarget = goingToProjects ? '/projects' : ROOT_PATH;
            const row = h('div', { class: 'desk-files-tools' });
            if (TOP_PATHS.indexOf(path) === -1) {
                row.append(toolButton('desk-open-parent-btn', 'corner-left-up', 'Up one folder',
                    () => { void open(parentDeskPath(path)); }));
            }
            if (path && path !== '/') {
                row.append(toolButton('desk-open-folder-btn', 'folder-open', 'Open folder', () => {
                    void BossModDeskOpener.openFolder({
                        api,
                        agentId,
                        path,
                        onError: (message) => renderError(path, message),
                    });
                }));
            }
            row.append(toolButton('desk-refresh-btn', 'refresh-cw', 'Refresh',
                () => { void open(path); }));
            row.append(h('button', {
                class: 'btn btn-sm',
                id: 'desk-root-switch-btn',
                type: 'button',
                'data-path': rootTarget,
                onclick: () => { void open(rootTarget); },
            },
                h('i', { 'data-lucide': goingToProjects ? 'folder-kanban' : 'lamp-desk', 'aria-hidden': 'true' }),
                goingToProjects ? 'Projects' : 'My desk'));
            return row;
        }

        function renderDirectory(payload) {
            const path = String(payload.path || ROOT_PATH);
            clear(element);
            element.append(
                h('div', { class: 'desk-files-bar' },
                    LISTING.breadcrumbs(payload.breadcrumbs, { onCrumb: (crumbPath) => { void open(crumbPath); } }),
                    controls(path)),
                LISTING.entries(payload.entries, {
                    onEntry: (entry) => { void open(entry.path); },
                    emptyCopy: EMPTY_COPY,
                }));
            // Rebuilt per folder, so the glyphs are painted per folder. Scoped
            // to this browser, and the painter is idempotent.
            BossModIcons.paint(element, 'desk-files');
        }

        function renderError(failedPath, message) {
            const safePath = failedPath || ROOT_PATH;
            clear(element);
            element.append(
                h('p', { class: 'context-error', role: 'alert' },
                    `${message} Path: ${safePath}`),
                h('div', { class: 'desk-files-recovery' },
                    h('button', {
                        class: 'btn btn-sm',
                        id: 'desk-error-back-btn',
                        type: 'button',
                        onclick: () => { void open(parentDeskPath(safePath)); },
                    }, 'Back'),
                    h('button', {
                        class: 'btn btn-sm',
                        id: 'desk-error-refresh-btn',
                        type: 'button',
                        onclick: () => { void open(safePath); },
                    }, 'Refresh')));
            BossModIcons.paint(element, 'desk-files');
        }

        /**
         * Show one desk path.
         *
         * A file path opens the shared viewer and leaves the browser on the
         * containing folder, which is where the operator wants to be next.
         *
         * @param {string} path
         * @returns {Promise<void>} Never rejects; a failure becomes the error
         *   state, which keeps Back and Refresh so the operator is not stranded.
         */
        async function open(path) {
            const requestedPath = path || ROOT_PATH;
            activePath = requestedPath;
            const loadId = load.next();

            clear(element);
            element.append(h('p', { class: 'context-skeleton' }, 'Loading desk…'));

            let payload;
            try {
                const res = await api(deskUrl(requestedPath), { cache: 'no-store' });
                if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
                payload = await res.json();
            } catch (err) {
                if (!isLive(loadId, requestedPath)) return;
                console.error('[desk-files] desk load failed', err);
                renderError(requestedPath, 'Failed to load desk contents.');
                return;
            }
            if (!isLive(loadId, requestedPath)) return;

            if (payload.kind === 'file') {
                BossModFileViewer.open(requestedPath, {
                    api,
                    apiUrl: deskUrl(requestedPath),
                    saveUrl: `/api/agents/${agentId}/desk`,
                    rawUrl: `/api/agents/${agentId}/desk/raw`,
                });
                await open(parentDeskPath(requestedPath));
                return;
            }
            renderDirectory(payload);
        }

        // A live write repaints the folder on screen. `/projects` is shared, so
        // any agent's write there can change what this operator is looking at;
        // everywhere else, only this agent's own writes can.
        disposers.push(bus.subscribe('chat_message', (data) => {
            const changed = data && data.desk_path;
            if (!changed) return;
            const shared = changed === '/projects' || String(changed).startsWith('/projects/');
            if (!shared && data.agent_id !== agentId) return;
            if (!affectedPaths(changed).has(activePath)) return;
            void open(activePath);
        }));

        return {
            element,
            open,

            /**
             * Stop painting and drain. The generation already drops an
             * in-flight load for a different agent; this drops one for a
             * browser that is gone.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                load.next();
                disposers.splice(0).forEach((off) => off());
            },
        };
    }

    return { createDeskFiles, parentDeskPath };
})();
