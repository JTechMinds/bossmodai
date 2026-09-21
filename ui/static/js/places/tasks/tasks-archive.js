/**
 * BossMod AI — Archive: finished work older than the Done window.
 *
 * A panel modal with its own search and the same day-grouped cards Done uses
 * (BossModTasksGrid.renderDayGroups), so a task reads the same on either side
 * of the window's edge. It holds the list it was last given and nothing else:
 * the place decides what is archived, under its own assignee and subtask
 * filters, and hands a fresh list to update() on every repaint.
 */
const BossModTasksArchive = (() => {
    const { h, clear } = BossModDom;

    /**
     * Open Archive.
     *
     * @param {object} deps
     * @param {object[]} deps.tasks  Finished tasks past the window, sorted.
     * @param {(task: object) => HTMLElement} deps.renderCard  The place's card,
     *   opening its task as a layer so ‹ comes back here.
     * @param {() => void} [deps.onClose]
     * @returns {{close: () => void, update: (tasks: object[]) => void}}
     * @throws {Error} When tasks is not an array or renderCard is missing.
     */
    function open(deps) {
        const { tasks, renderCard, onClose } = deps || {};
        if (!Array.isArray(tasks)) throw new Error('[tasks-archive] deps.tasks must be an array');
        if (typeof renderCard !== 'function') {
            throw new Error('[tasks-archive] deps.renderCard is required');
        }

        let current = tasks;

        const search = BossModSearchField.create({
            placeholder: 'Search the archive',
            label: 'Search archived tasks by title',
            className: 'tasks-archive-search',
            onInput: () => paintList(),
        });
        const list = h('div', { class: 'tasks-archive-list' });

        /** Repaint the list from the held tasks and the current query. */
        function paintList() {
            const query = search.input.value.trim();
            // The page's own matcher, so Archive and the page agree about what
            // a search hits; subtasks are shown because the page's filters
            // have already decided which of them belong here.
            const shown = BossModTasksData.filterTasks(current, { query, showChildren: true });
            clear(list);
            if (current.length === 0) {
                list.append(h('p', { class: 'empty-slot' }, 'Nothing older than the Done window.'));
            } else if (shown.length === 0) {
                list.append(h('p', { class: 'empty-slot' }, `No archived task matches "${query}".`));
            } else {
                list.append(BossModTasksGrid.renderDayGroups(shown, renderCard));
            }
            BossModIcons.paint(list, 'tasks-archive');
        }

        paintList();

        const modal = BossModOverlays.createModal({
            title: 'Archive',
            subtitle: `${current.length} finished`,
            size: 'panel',
            body: h('div', { class: 'tasks-archive' }, search.element, list),
            actions: [],
            // A search box is not a form; nothing typed there is lost work.
            closeOnBackdrop: true,
            onClose,
        });

        return {
            close: modal.close,

            /**
             * Swap in a fresh list, keeping whatever is typed in the search.
             *
             * @param {object[]} next
             * @returns {void}
             * @throws {Error} When next is not an array, or the head has lost
             *   its subtitle — a count that silently stopped updating would
             *   misreport what Archive holds.
             */
            update(next) {
                if (!Array.isArray(next)) throw new Error('[tasks-archive] update needs an array');
                current = next;
                const subtitle = modal.element.querySelector('.modal-subtitle');
                if (!subtitle) throw new Error('[tasks-archive] the modal head has no subtitle');
                subtitle.textContent = `${current.length} finished`;
                paintList();
            },
        };
    }

    return { open };
})();
