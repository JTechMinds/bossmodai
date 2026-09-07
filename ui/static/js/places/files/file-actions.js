/**
 * BossMod AI — the row actions menu, the New menu, and what they do.
 *
 * The menu is reachable three ways: the ⋯ button on every row, the same
 * button's Enter/Space, and right-click. Arrow keys move within it, Escape
 * closes it, and focus returns to the button that opened it — a context menu
 * that only answers right-click has no keyboard equivalent, and Delete is
 * behind this one.
 *
 * Nothing destructive happens without a dialog from core/overlays.js, and no
 * failure is left in the console: every handler reports through `onError`.
 */
const BossModFileActions = (() => {
    const { h } = BossModDom;

    /** Menu order, ported from company-files.js. */
    const ITEMS = Object.freeze([
        { action: 'rename', label: 'Rename' },
        { action: 'delete', label: 'Delete', tone: 'danger' },
        { action: 'copy-path', label: 'Copy Path' },
        { action: 'open-explorer', label: 'Open in Explorer' },
        { action: 'move', label: 'Move to…' },
        { action: 'copy', label: 'Copy to…' },
    ]);

    /** Kept off the right edge of the viewport, as the original was. */
    const MENU_WIDTH = 200;
    const MENU_HEIGHT = 280;

    /**
     * Open the row menu.
     *
     * @param {object} deps
     * @param {object} deps.entry
     * @param {HTMLElement} deps.anchor  The ⋯ button; focus returns here.
     * @param {{x: number, y: number}|null} deps.at  Pointer position for a
     *   right-click; null anchors the menu to the button instead.
     * @param {(action: string, entry: object) => void} deps.onChoose
     * @returns {{ close: () => void, element: HTMLElement }}
     */
    function openMenu({ entry, anchor, at, onChoose }) {
        const items = [];
        const element = h('div', {
            class: 'file-menu',
            role: 'menu',
            'aria-label': `Actions for ${entry.name}`,
        });

        ITEMS.forEach((item, index) => {
            const button = h('button', {
                class: `file-menu-item ${item.tone || ''}`.trim(),
                type: 'button',
                role: 'menuitem',
                tabindex: index === 0 ? '0' : '-1',
                'data-action': item.action,
                onclick: () => {
                    close();
                    onChoose(item.action, entry);
                },
            }, item.label);
            items.push(button);
            element.append(button);
        });

        const point = at || anchorPoint(anchor);
        element.style.left = `${Math.min(point.x, window.innerWidth - MENU_WIDTH)}px`;
        element.style.top = `${Math.min(point.y, window.innerHeight - MENU_HEIGHT)}px`;

        function move(step) {
            const index = items.indexOf(document.activeElement);
            const next = (Math.max(index, 0) + step + items.length) % items.length;
            items.forEach((node, i) => node.setAttribute('tabindex', i === next ? '0' : '-1'));
            items[next].focus();
        }

        function onKeydown(event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                close();
                return;
            }
            if (event.key === 'ArrowDown') { event.preventDefault(); move(1); }
            else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); }
        }

        function onDocumentClick(event) {
            if (element.contains(event.target)) return;
            close();
        }

        let closed = false;
        function close() {
            if (closed) return;
            closed = true;
            document.removeEventListener('keydown', onKeydown);
            document.removeEventListener('click', onDocumentClick);
            element.remove();
            anchor.setAttribute('aria-expanded', 'false');
            if (anchor.focus) anchor.focus();
        }

        document.addEventListener('keydown', onKeydown);
        // Deferred: the click that opened the menu is still propagating, and
        // binding synchronously would close it before it was ever seen.
        setTimeout(() => document.addEventListener('click', onDocumentClick), 0);
        document.body.append(element);
        anchor.setAttribute('aria-expanded', 'true');
        items[0].focus();

        return { close, element };
    }

    function anchorPoint(anchor) {
        const box = anchor.getBoundingClientRect();
        return { x: box.left, y: box.bottom };
    }

    /**
     * Carry out one menu choice.
     *
     * @param {string} action
     * @param {object} entry
     * @param {object} deps
     * @param {Function} deps.api
     * @param {() => void} deps.onChanged  The listing needs reloading.
     * @param {(message: string) => void} deps.onError
     * @returns {void}
     */
    function run(action, entry, { api, onChanged, onError }) {
        const name = String(entry.name || '');
        if (action === 'rename') {
            BossModFileOps.showRenameDialog({ api, path: entry.path, name, onComplete: onChanged });
            return;
        }
        if (action === 'delete') {
            BossModFileOps.showDeleteDialog({
                api, path: entry.path, name, onComplete: onChanged, onError,
            });
            return;
        }
        if (action === 'copy-path') {
            void BossModFileOps.copyPath(entry.path).catch((err) => {
                console.error('[file-actions] copy path failed', err);
                onError(`Could not copy that path: ${(err && err.message) || 'the clipboard refused.'}`);
            });
            return;
        }
        if (action === 'open-explorer') {
            void BossModFolderOpener.openFolder({
                api,
                path: BossModFolderOpener.revealTarget(entry.path, entry.is_dir === true),
                onError,
            });
            return;
        }
        if (action === 'move' || action === 'copy') {
            BossModFileOps.showMoveOrCopyDialog({
                api, path: entry.path, name, action, onComplete: onChanged,
            });
            return;
        }
        // A menu item with no handler would look like a click that did nothing.
        throw new Error(`[file-actions] no handler for "${action}"`);
    }

    /**
     * The New file / New folder dropdown.
     *
     * It owns the one document-level click listener that closes it, bound when
     * the menu is built — once per mount — and removed by destroy(). The
     * dock-era version bound it at module load and never removed it.
     *
     * @param {object} deps
     * @param {(kind: 'file'|'folder') => void} deps.onCreate
     * @returns {{ element: HTMLElement, close: () => void, destroy: () => void }}
     */
    function createNewMenu({ onCreate }) {
        const list = h('div', { class: 'file-new-list', role: 'menu', hidden: true },
            h('button', {
                class: 'file-menu-item', type: 'button', role: 'menuitem',
                onclick: () => { close(); onCreate('file'); },
            }, 'New File'),
            h('button', {
                class: 'file-menu-item', type: 'button', role: 'menuitem',
                onclick: () => { close(); onCreate('folder'); },
            }, 'New Folder'));

        const toggle = h('button', {
            class: 'file-toolbar-btn', type: 'button',
            'aria-haspopup': 'menu', 'aria-expanded': 'false',
            onclick: () => { list.hidden ? open() : close(); },
        }, 'New');

        const element = h('div', { class: 'file-new' }, toggle, list);

        function open() {
            list.hidden = false;
            toggle.setAttribute('aria-expanded', 'true');
        }

        function close() {
            list.hidden = true;
            toggle.setAttribute('aria-expanded', 'false');
        }

        function onDocumentClick(event) {
            if (list.hidden || element.contains(event.target)) return;
            close();
        }

        function onKeydown(event) {
            if (event.key === 'Escape' && !list.hidden) {
                close();
                toggle.focus();
            }
        }

        document.addEventListener('click', onDocumentClick);
        document.addEventListener('keydown', onKeydown);

        return {
            element,
            close,
            /** @returns {void} */
            destroy() {
                document.removeEventListener('click', onDocumentClick);
                document.removeEventListener('keydown', onKeydown);
            },
        };
    }

    return { openMenu, run, createNewMenu, ITEMS };
})();
