/**
 * BossMod AI — one agent folder listing, as views.
 *
 * Pure builders over the listing an agent's desk route returns for a folder
 * (api/routes/_desk.py): its `breadcrumbs` and its `entries`. They fetch nothing and own no
 * state; a click is reported to the caller, which decides what it means.
 *
 * Extracted from context/desk-files.js when the task detail's file picker
 * (places/tasks/task-file-picker.js) became the second surface to list a
 * folder: one renderer keeps one look. The bodies moved verbatim; only the
 * click target and the optional selection are parameters now.
 */
const BossModFileListing = (() => {
    const { h } = BossModDom;

    /**
     * The folder path as crumbs. The root crumb is a house rather than the
     * API's `/` label, and the separators are chevrons — `/ / me` was the
     * root's slash and a typed separator in a row.
     *
     * @param {Array<{label: string, path: string}>} crumbs
     * @param {object} opts
     * @param {(path: string) => void} opts.onCrumb  A crumb was clicked.
     * @returns {HTMLElement}
     * @throws {Error} When onCrumb is missing.
     */
    function breadcrumbs(crumbs, opts) {
        const { onCrumb } = opts || {};
        if (typeof onCrumb !== 'function') throw new Error('[file-listing] opts.onCrumb is required');
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
                onclick: () => onCrumb(crumb.path),
            }, isRoot
                ? h('i', { 'data-lucide': 'house', 'aria-hidden': 'true' })
                : String(crumb.label)));
        });
        return row;
    }

    /**
     * A row's glyph: a folder, a text document, or any other file.
     * @param {{name?: string, is_dir?: boolean}} entry
     * @returns {string} A lucide icon name.
     */
    function entryIcon(entry) {
        if (entry.is_dir === true) return 'folder';
        return /\.(md|txt)$/i.test(String(entry.name || '')) ? 'file-text' : 'file';
    }

    /**
     * A folder's entries as one row each, or the dashed empty slot.
     *
     * @param {Array<{name: string, path: string, is_dir: boolean,
     *   size_bytes?: number, updated_at?: string}>} entries
     * @param {object} opts
     * @param {(entry: object) => void} opts.onEntry  A row was clicked.
     * @param {string} [opts.selectedPath]  Given (as a string, '' for none),
     *   the FILE rows become toggles: `aria-pressed` says which one is
     *   chosen. Folder rows navigate and never carry it. Omitted, no row does.
     * @param {string} opts.emptyCopy  What an empty folder says.
     * @returns {HTMLElement}
     * @throws {Error} When onEntry or emptyCopy is missing.
     */
    function entries(entries, opts) {
        const { onEntry, selectedPath, emptyCopy } = opts || {};
        if (typeof onEntry !== 'function') throw new Error('[file-listing] opts.onEntry is required');
        if (!emptyCopy) throw new Error('[file-listing] opts.emptyCopy is required');
        const selectable = typeof selectedPath === 'string';
        if (!Array.isArray(entries) || entries.length === 0) {
            // Dashed, so an empty folder reads as an empty folder rather
            // than as a section that failed to render.
            return h('p', { class: 'context-empty empty-slot' }, emptyCopy);
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
                'aria-pressed': selectable && !isDir ? String(entry.path === selectedPath) : null,
                onclick: () => onEntry(entry),
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

    return { breadcrumbs, entries, entryIcon };
})();
