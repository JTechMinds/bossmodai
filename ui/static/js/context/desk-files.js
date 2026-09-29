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

        /**
         * The folder path as crumbs. The root crumb is a house rather than the
         * API's `/` label, and the separators are chevrons — `/ / me` was the
         * root's slash and a typed separator in a row.
         *
         * @param {Array<{label: string, path: string}>} crumbs
         * @returns {HTMLElement}
         */
        function breadcrumbs(crumbs) {
            const list = Array.isArray(crumbs) ? crumbs : [];
            const row = h('nav', { class: 'desk-crumbs', 'aria-label': 'Folder path' });
            list.forEach((crumb, index) => {
                if (index > 0) {
                    row.append(h('i', {
                        class: 'desk-crumb-sep', 'data-lucide': 'chevron-right', 'aria-hidden': 'true',
                    }));
                }
                const isRoot = crumb.path === '/';
                const isLast = index === list.length - 1;
                row.append(h('button', {
                    class: 'desk-crumb',
                    type: 'button',
                    'data-path': crumb.path,
                    'aria-label': isRoot ? 'Workspace root' : null,
                    'aria-current': isLast ? 'page' : null,
                    onclick: () => { void open(crumb.path); },
                }, isRoot
                    ? h('i', { 'data-lucide': 'house', 'aria-hidden': 'true' })
                    : String(crumb.label)));
            });
            return row;
        }

        /** A row's glyph: a folder, a text document, or any other file. */
        function entryIcon(entry) {
            if (entry.is_dir === true) return 'folder';
            return /\.(md|txt)$/i.test(String(entry.name || '')) ? 'file-text' : 'file';
        }

        function entryList(entries) {
            if (!Array.isArray(entries) || entries.length === 0) {
                // Dashed, so an empty folder reads as an empty folder rather
                // than as a section that failed to render.
                return h('p', { class: 'context-empty empty-slot' }, EMPTY_COPY);
            }
            const list = h('div', { class: 'desk-entries' });
            entries.forEach((entry) => {
                const isDir = entry.is_dir === true;
                const name = String(entry.name);
                // One line: the glyph, the name, then the size and the time.
                // The full path is the crumbs' job; repeating it under every
                // name is what wrapped a narrow row into "outpu / t".
                list.append(h('button', {
                    class: 'desk-entry',
                    type: 'button',
                    'data-path': entry.path,
                    'data-is-dir': isDir ? '1' : '0',
                    onclick: () => { void open(entry.path); },
                },
                    h('i', { 'data-lucide': entryIcon(entry), 'aria-hidden': 'true' }),
                    h('span', { class: 'desk-entry-name', title: name }, name),
                    h('span', { class: 'desk-entry-meta' },
                        isDir ? '' : BossModFormat.formatFileSize(entry.size_bytes)),
                    h('span', { class: 'desk-entry-meta' },
                        BossModFormat.formatRelativeTime(entry.updated_at))));
            });
            return list;
        }

        function renderDirectory(payload) {
            const path = String(payload.path || ROOT_PATH);
            clear(element);
            element.append(
                h('div', { class: 'desk-files-bar' },
                    breadcrumbs(payload.breadcrumbs),
                    controls(path)),
                entryList(payload.entries));
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
                BossModFileViewer.open(requestedPath, { api, apiUrl: deskUrl(requestedPath) });
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
