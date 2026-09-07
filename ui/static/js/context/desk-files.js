/**
 * BossMod AI — the desk browser in the context column.
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

        function controlsRow(path) {
            const goingToProjects = !String(path).startsWith('/projects');
            const rootTarget = goingToProjects ? '/projects' : ROOT_PATH;
            const rootLabel = goingToProjects ? 'Projects' : 'My Desk';
            const row = h('div', { class: 'desk-files-controls' });

            row.append(h('button', {
                class: 'desk-files-btn',
                id: 'desk-root-switch-btn',
                type: 'button',
                'data-path': rootTarget,
                onclick: () => { void open(rootTarget); },
            }, rootLabel));

            if (TOP_PATHS.indexOf(path) === -1) {
                row.append(h('button', {
                    class: 'desk-files-btn',
                    id: 'desk-open-parent-btn',
                    type: 'button',
                    onclick: () => { void open(parentDeskPath(path)); },
                }, 'Up'));
            }
            if (path && path !== '/') {
                row.append(h('button', {
                    class: 'desk-files-btn',
                    id: 'desk-open-folder-btn',
                    type: 'button',
                    onclick: () => {
                        void BossModDeskOpener.openFolder({
                            api,
                            agentId,
                            path,
                            onError: (message) => renderError(path, message),
                        });
                    },
                }, 'Open Folder'));
            }
            row.append(h('button', {
                class: 'desk-files-btn',
                id: 'desk-refresh-btn',
                type: 'button',
                onclick: () => { void open(path); },
            }, 'Refresh'));
            return row;
        }

        function breadcrumbs(crumbs) {
            const row = h('div', { class: 'desk-crumbs' });
            (Array.isArray(crumbs) ? crumbs : []).forEach((crumb, index) => {
                if (index > 0) row.append(h('span', { class: 'desk-crumb-sep' }, '/'));
                row.append(h('button', {
                    class: 'desk-crumb',
                    type: 'button',
                    'data-path': crumb.path,
                    onclick: () => { void open(crumb.path); },
                }, String(crumb.label)));
            });
            return row;
        }

        function entryList(entries) {
            if (!Array.isArray(entries) || entries.length === 0) {
                return h('p', { class: 'context-empty' }, EMPTY_COPY);
            }
            const list = h('div', { class: 'desk-entries' });
            entries.forEach((entry) => {
                const isDir = entry.is_dir === true;
                list.append(h('button', {
                    class: 'desk-entry',
                    type: 'button',
                    'data-path': entry.path,
                    'data-is-dir': isDir ? '1' : '0',
                    onclick: () => { void open(entry.path); },
                },
                    h('span', { class: 'desk-entry-name' }, String(entry.name)),
                    h('span', { class: 'desk-entry-meta' },
                        entry.category ? `${entry.path} · ${entry.category}` : String(entry.path))));
            });
            return list;
        }

        function renderDirectory(payload) {
            const path = String(payload.path || ROOT_PATH);
            clear(element);
            element.append(
                h('p', { class: 'desk-section-title' }, String(payload.name || 'Desk')),
                breadcrumbs(payload.breadcrumbs),
                controlsRow(path),
                entryList(payload.entries));
        }

        function renderError(failedPath, message) {
            const safePath = failedPath || ROOT_PATH;
            clear(element);
            element.append(
                h('p', { class: 'context-error', role: 'alert' },
                    `${message} Path: ${safePath}`),
                h('div', { class: 'desk-files-controls' },
                    h('button', {
                        class: 'desk-files-btn',
                        id: 'desk-error-back-btn',
                        type: 'button',
                        onclick: () => { void open(parentDeskPath(safePath)); },
                    }, 'Back'),
                    h('button', {
                        class: 'desk-files-btn',
                        id: 'desk-error-refresh-btn',
                        type: 'button',
                        onclick: () => { void open(safePath); },
                    }, 'Refresh')));
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
                CompanyFileViewer.open(requestedPath, { apiUrl: deskUrl(requestedPath) });
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
