/**
 * BossMod AI — the Files toolbar.
 *
 * The named-path box, the search box, the New menu, and the three buttons. It
 * is built ONCE per mount and never replaced, which is the whole reason it is
 * its own module: the dock-era browser rebuilt its header on every repaint, so
 * it had to re-focus the search box and restore the caret afterwards. Nothing
 * here is taken away from the operator mid-keystroke, so there is nothing to
 * restore.
 *
 * It owns no data. Every control reports through a callback, the same contract
 * board-toolbar.js follows.
 */
const BossModFilesToolbar = (() => {
    const { h } = BossModDom;

    const SEARCH_DELAY_MS = 300;
    /** Below this the search filters the folder; at or above it, the workspace. */
    const GLOBAL_SEARCH_MIN = 3;
    const PATH_PLACEHOLDER = 'Open named path…';
    const PATH_TITLE = 'Paste an absolute path under a configured host root, '
        + 'or a company-relative path';

    /**
     * Build the toolbar.
     *
     * @param {object} deps
     * @param {(named: string) => void} deps.onOpenPath
     * @param {(query: string) => void} deps.onSearchAll   Query is >= 3 chars.
     * @param {(query: string) => void} deps.onFilterHere  Query is shorter, so
     *   the folder on screen is filtered rather than the workspace searched.
     * @param {(kind: 'file'|'folder') => void} deps.onCreate
     * @param {() => void} deps.onHostRoots
     * @param {() => void} deps.onOpenFolder
     * @param {() => void} deps.onRefresh
     * @returns {{ element: HTMLElement, clearSearch: () => void,
     *             destroy: () => void }}
     * @throws {Error} When a callback is missing.
     */
    function createToolbar(deps) {
        const {
            onOpenPath, onSearchAll, onFilterHere, onCreate,
            onHostRoots, onOpenFolder, onRefresh,
        } = deps || {};
        [['onOpenPath', onOpenPath], ['onSearchAll', onSearchAll],
            ['onFilterHere', onFilterHere], ['onCreate', onCreate],
            ['onHostRoots', onHostRoots], ['onOpenFolder', onOpenFolder],
            ['onRefresh', onRefresh]].forEach(([name, fn]) => {
            if (typeof fn !== 'function') throw new Error(`[files-toolbar] deps.${name} is required`);
        });

        let searchTimer = null;

        const pathInput = h('input', {
            class: 'files-path', type: 'text', placeholder: PATH_PLACEHOLDER,
            'aria-label': PATH_PLACEHOLDER, title: PATH_TITLE,
            onkeydown: (event) => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                const named = String(pathInput.value || '').trim();
                if (named) onOpenPath(named);
            },
        });

        const searchInput = h('input', {
            class: 'files-search', type: 'search', placeholder: 'Search files…',
            'aria-label': 'Search files',
            oninput: () => {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(() => {
                    const query = String(searchInput.value || '').trim();
                    if (query.length >= GLOBAL_SEARCH_MIN) onSearchAll(query);
                    else onFilterHere(query);
                }, SEARCH_DELAY_MS);
            },
        });

        const newMenu = BossModFileActions.createNewMenu({ onCreate });

        const element = h('div', { class: 'files-controls' },
            pathInput,
            searchInput,
            newMenu.element,
            h('button', {
                class: 'file-toolbar-btn', type: 'button',
                title: 'Add or edit allowlisted host folders', onclick: onHostRoots,
            }, 'Host folders'),
            h('button', {
                class: 'file-toolbar-btn', type: 'button',
                title: 'Open in file manager', onclick: onOpenFolder,
            }, 'Open'),
            h('button', {
                class: 'file-toolbar-btn', type: 'button',
                'aria-label': 'Refresh this folder', onclick: onRefresh,
            }, 'Refresh'));

        return {
            element,

            /**
             * Empty the search box and cancel any pending debounce, without
             * firing a callback: the caller is already navigating.
             * @returns {void}
             */
            clearSearch() {
                clearTimeout(searchTimer);
                searchInput.value = '';
            },

            /**
             * Drop the debounce and the New menu's document listeners.
             * @returns {void}
             */
            destroy() {
                clearTimeout(searchTimer);
                newMenu.destroy();
            },
        };
    }

    return { createToolbar, GLOBAL_SEARCH_MIN, SEARCH_DELAY_MS };
})();
