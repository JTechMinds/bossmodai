/**
 * BossMod AI — the directory listing.
 *
 * Rows, the four states' bodies, and the footer count. It makes no request and
 * holds no state: it takes already-filtered entries and two callbacks, which is
 * what lets the place repaint the list without rebuilding the search box.
 *
 * Every row carries TWO controls, not one. Opening is the row itself; the
 * actions menu is a real button beside it, because a context menu reachable
 * only by right-click has no keyboard equivalent (WCAG 2.2 SC 2.1.1) and the
 * actions behind it include Delete.
 */
const BossModFileGrid = (() => {
    const { h, clear } = BossModDom;

    const EMPTY_DIRECTORY = 'No files in this directory.';
    const EMPTY_SEARCH = 'No files match your search.';
    const EMPTY_GLOBAL = 'No files found.';
    const SKELETON_ROWS = 6;

    /**
     * The empty-state sentence for a listing with no rows.
     *
     * @param {string} query  '' when the operator is not searching.
     * @param {boolean} isGlobal
     * @returns {string}
     */
    function emptyMessage(query, isGlobal) {
        if (!query) return EMPTY_DIRECTORY;
        return isGlobal ? EMPTY_GLOBAL : EMPTY_SEARCH;
    }

    /**
     * One row.
     *
     * @param {object} entry  One company-files listing entry.
     * @param {object} deps
     * @param {boolean} deps.showPath  Global search results show where the hit
     *   lives; a directory listing does not need to repeat the folder.
     * @param {(entry: object) => void} deps.onOpen
     * @param {(entry: object, anchor: HTMLElement, at: object|null) => void} deps.onMenu
     * @returns {HTMLElement}
     */
    function renderEntry(entry, { showPath, onOpen, onMenu }) {
        const isDir = entry.is_dir === true;
        const name = String(entry.name || '');
        const size = isDir ? '' : BossModUtils.formatFileSize(entry.size_bytes);
        const when = BossModUtils.formatRelativeTime(entry.updated_at);

        const open = h('button', {
            class: 'file-entry',
            type: 'button',
            'data-path': entry.path,
            'data-is-dir': isDir ? '1' : '0',
            onclick: () => onOpen(entry),
        },
            h('span', { class: 'file-entry-kind', 'aria-hidden': 'true' }, isDir ? '▸' : '·'),
            h('span', { class: 'file-entry-name' }, isDir ? `${name}/` : name),
            showPath ? h('span', { class: 'file-entry-path' }, String(entry.path || '')) : null,
            entry.agent_name ? h('span', { class: 'file-entry-agent' }, String(entry.agent_name)) : null,
            size ? h('span', { class: 'file-entry-size' }, size) : null,
            when ? h('span', { class: 'file-entry-time' }, when) : null);

        const menu = h('button', {
            class: 'file-entry-menu',
            type: 'button',
            'aria-haspopup': 'menu',
            'aria-expanded': 'false',
            'aria-label': `Actions for ${name}`,
            onclick: (event) => {
                event.stopPropagation();
                onMenu(entry, menu, null);
            },
        }, '⋯');

        const row = h('div', { class: 'file-row' }, open, menu);
        // Right-click stays, because that is the gesture operators reach for.
        // It is the second way in, never the only one.
        row.addEventListener('contextmenu', (event) => {
            event.preventDefault();
            onMenu(entry, menu, { x: event.clientX, y: event.clientY });
        });
        return row;
    }

    /**
     * The listing body.
     *
     * @param {object[]} entries  Already filtered; folders are sorted first.
     * @param {object} deps  See renderEntry, plus `query` for the empty copy.
     * @returns {HTMLElement}
     */
    function renderList(entries, deps) {
        if (entries.length === 0) {
            return h('div', { class: 'place-empty' },
                h('p', { class: 'place-empty-title' }, 'Nothing here'),
                h('p', { class: 'place-empty-hint' }, emptyMessage(deps.query, deps.showPath)));
        }
        const list = h('div', { class: 'file-list' });
        entries.forEach((entry) => list.append(renderEntry(entry, deps)));
        return list;
    }

    /**
     * The loading state: the shape of the final list, not a spinner on empty
     * space (spec 8.3).
     * @returns {HTMLElement}
     */
    function renderSkeleton() {
        const list = h('div', { class: 'file-list is-skeleton', 'aria-hidden': 'true' });
        for (let i = 0; i < SKELETON_ROWS; i += 1) {
            list.append(h('div', { class: 'file-row is-skeleton' }, h('span', { class: 'file-skeleton-bar' })));
        }
        return list;
    }

    /**
     * The footer line.
     *
     * @param {object[]} entries  The rows the operator can see.
     * @returns {string}
     */
    function summarise(entries) {
        const dirs = entries.filter((entry) => entry.is_dir === true).length;
        const files = entries.length - dirs;
        const bytes = entries
            .filter((entry) => entry.is_dir !== true)
            .reduce((sum, entry) => sum + (entry.size_bytes || 0), 0);
        const counts = `${dirs} folder${dirs !== 1 ? 's' : ''}, ${files} file${files !== 1 ? 's' : ''}`;
        return bytes > 0 ? `${counts} · Total: ${BossModUtils.formatFileSize(bytes)}` : counts;
    }

    /**
     * Folders first, then files, each in the order the server gave them.
     *
     * @param {object[]} entries
     * @returns {object[]} A new array.
     */
    function foldersFirst(entries) {
        return entries.filter((entry) => entry.is_dir === true)
            .concat(entries.filter((entry) => entry.is_dir !== true));
    }

    /**
     * The rows a listing shows: folders first, and name-filtered when the
     * operator is narrowing THIS folder rather than searching the workspace.
     *
     * @param {object[]} entries
     * @param {string} query
     * @param {boolean} isGlobal  Workspace hits are already the search result.
     * @returns {object[]}
     */
    function visibleRows(entries, query, isGlobal) {
        if (!query || isGlobal) return foldersFirst(entries);
        const needle = query.toLowerCase();
        return foldersFirst(
            entries.filter((e) => String(e.name || '').toLowerCase().includes(needle)));
    }

    /**
     * The breadcrumb trail. The last crumb is where the operator already is, so
     * it is text rather than a button that goes nowhere.
     *
     * @param {HTMLElement} el
     * @param {object[]} crumbs
     * @param {(path: string) => void} onCrumb
     * @returns {void}
     */
    function paintCrumbs(el, crumbs, onCrumb) {
        clear(el);
        crumbs.forEach((crumb, index) => {
            if (index > 0) el.append(h('span', { class: 'file-crumb-sep' }, '/'));
            const label = String(crumb.label || crumb.name || '')
                + (crumb.agent_name ? ` (${crumb.agent_name})` : '');
            if (index === crumbs.length - 1) {
                el.append(h('span', { class: 'file-crumb is-current' }, label));
                return;
            }
            el.append(h('button', {
                class: 'file-crumb-btn', type: 'button', onclick: () => onCrumb(crumb.path),
            }, label));
        });
    }

    /**
     * The "searching everything" bar that replaces the crumbs during a global
     * search, so the operator is never shown a trail that does not describe
     * what they are looking at.
     *
     * @param {HTMLElement} el
     * @param {string} query
     * @param {() => void} onClear
     * @returns {void}
     */
    function paintSearchBar(el, query, onClear) {
        clear(el);
        el.append(
            h('span', {}, `Searching all files for “${query}”`),
            h('button', { class: 'file-crumb-btn', type: 'button', onclick: onClear }, 'Clear'));
    }

    /**
     * The workspace note and the host-folder allowlist.
     *
     * @param {HTMLElement} el
     * @param {object} state  `{note, roots, hidden}`.
     * @param {() => void} onManage
     * @returns {void}
     */
    function paintNotice(el, { note, roots, hidden }, onManage) {
        clear(el);
        el.hidden = hidden || !(note || roots.length);
        if (el.hidden) return;
        el.append(h('div', { class: 'files-notice-copy' },
            h('p', {}, note),
            roots.length
                ? h('p', { class: 'files-notice-roots' }, `Host folders: ${roots.join(', ')}`)
                : null));
        el.append(h('button', {
            class: 'file-toolbar-btn', type: 'button', onclick: onManage,
        }, BossModHostRoots.buttonLabel(roots)));
    }

    /**
     * The error state: what failed, where, and a way to try again.
     *
     * @param {string} message
     * @param {string} path
     * @param {() => void} onRetry
     * @returns {HTMLElement}
     */
    function renderError(message, path, onRetry) {
        return h('div', { class: 'place-error-panel', role: 'alert' },
            h('p', { class: 'place-error-title' }, 'Could not load files'),
            h('p', { class: 'place-error-detail' }, message),
            h('p', { class: 'place-error-detail' }, `Path: ${path}`),
            h('button', { class: 'btn', type: 'button', onclick: onRetry }, 'Try again'));
    }

    /**
     * The listing surface: heading, summary, crumbs bar, notice bar, error
     * line, and the body every state is painted into.
     *
     * Built once per mount. The place swaps only what changes, which is what
     * lets the toolbar above it keep the operator's caret.
     *
     * @param {HTMLElement} controls  The toolbar, which the place owns.
     * @returns {{element: HTMLElement, crumbs: HTMLElement, notice: HTMLElement,
     *   body: HTMLElement, summary: HTMLElement,
     *   setError: (message: string) => void,
     *   setBody: (node: HTMLElement) => void}}
     */
    function createFrame(controls) {
        const summary = h('p', { class: 'board-summary' }, '');
        const crumbs = h('div', { class: 'files-crumbs' });
        const notice = h('div', { class: 'files-notice', hidden: true });
        const error = h('p', { class: 'files-error', role: 'alert', hidden: true });
        const body = h('div', { class: 'files-body' });
        const element = h('div', { class: 'files-place' },
            h('header', { class: 'board-header' },
                h('div', { class: 'board-title' },
                    h('h1', { tabindex: '-1' }, 'Files'), summary),
                controls),
            crumbs, notice, error, body);
        return {
            element, crumbs, notice, body, summary,
            setError(message) {
                error.textContent = message || '';
                error.hidden = !message;
            },
            /** Replace the body with one node and blank the summary. */
            setBody(node) {
                summary.textContent = '';
                clear(body);
                body.append(node);
            },
        };
    }

    return {
        renderList, renderSkeleton, renderEntry, renderError, createFrame,
        summarise, foldersFirst, visibleRows, emptyMessage,
        paintCrumbs, paintSearchBar, paintNotice,
    };
})();
