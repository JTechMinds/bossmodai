/**
 * BossMod AI — opening a desk task row as a layer over the desk.
 *
 * A row used to switch to the Tasks place, which closed the desk with no way
 * back. It opens the task over the desk now, through the Tasks place's own
 * controller (places/tasks/task-layers.js), so the head reads
 * `Brian › Write TDD specs` and ‹ comes back to the desk.
 *
 * Split from context/desk-panel.js, which composes the desk and would
 * otherwise pass the line cap. The panel decides THAT a row opens a layer;
 * this is the wiring that makes one: the task list read at the click, the
 * actions a detail asks for, and the chat a detail leaves for. No endpoint of
 * its own — the list is BossModTasksData.loadTasks, the edit, complete and
 * cancel are BossModTaskActions, exactly the Tasks place's.
 */
const BossModDeskTaskOpener = (() => {
    /**
     * Build the opener for one desk.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store; the roster colours the
     *   detail's avatars and is who the edit form can assign.
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {{refresh: () => Promise<void>, showError: (message: string) => void}} deps.tasks
     *   The desk's Tasks section (context/desk-tasks.js): a failure here is
     *   said above its rows, and a cancel, completion or edit re-reads them.
     * @param {(id: string, kind: string) => void} deps.openConversation
     *   Leaves for a task's conversation. The desk dialog's closes the desk
     *   and everything over it first.
     * @returns {{ open: (taskId: string) => Promise<void>, destroy: () => void }}
     *   `open` reads the task list and opens that task as a layer. It never
     *   rejects: a failed read, or a task gone from the list, is said above the
     *   rows (and a failed read is logged). A second call while one is in
     *   flight is ignored, so a double click opens one task. `destroy` closes
     *   every task layer this opened; an `open` still reading opens nothing.
     * @throws {Error} When any dependency is missing.
     */
    function create(deps) {
        const { store, api, tasks, openConversation } = deps || {};
        if (!store) throw new Error('[desk-task-opener] deps.store is required');
        if (typeof api !== 'function') throw new Error('[desk-task-opener] deps.api is required');
        if (!tasks || typeof tasks.refresh !== 'function' || typeof tasks.showError !== 'function') {
            throw new Error('[desk-task-opener] deps.tasks must be the desk Tasks section');
        }
        if (typeof openConversation !== 'function') {
            throw new Error('[desk-task-opener] deps.openConversation is required');
        }

        /** The list the open layers resolve parents and children from: the
         *  read made for the row that was clicked. The desk's rows are two
         *  boards' top few, not the whole list a detail needs. */
        let loadedTasks = [];
        /** A row click in flight. */
        let opening = false;
        let destroyed = false;

        const actions = BossModTaskActions.create({
            api,
            store,
            // The changed task's layer is stale, and so is its row.
            onChanged: () => {
                taskLayers.closeAll();
                void tasks.refresh();
            },
            onError: (message) => tasks.showError(message),
        });
        const taskLayers = BossModTaskLayers.create({
            api,
            getTasks: () => loadedTasks,
            // The roster colour; undefined (not rostered) is the avatar's
            // neutral treatment — the Tasks place's own lookup.
            colorOf: (id) => {
                const who = store.getState().roster.find((item) => item && item.id === id);
                return who ? who.color : undefined;
            },
            actions,
            onOpenChat: (task) => {
                const target = BossModTasksData.chatTargetFor(task);
                // The detail offers chat only when there is somewhere to go.
                if (!target) throw new Error(`[desk-task-opener] no chat to open for task "${task.id}"`);
                openConversation(target.id, target.kind);
            },
        });

        /** See @returns. */
        async function open(taskId) {
            if (opening) return;
            opening = true;
            try {
                loadedTasks = await BossModTasksData.loadTasks(api);
            } catch (err) {
                console.error('[desk-task-opener] could not load the task list to open a task', err);
                if (!destroyed) tasks.showError('Could not open that task.');
                return;
            } finally {
                opening = false;
            }
            // The desk closed while the list loaded: nothing to open it over.
            if (destroyed) return;
            // The row came off a board read earlier and the task can have gone
            // since; the detail would throw on it, and a click that does
            // nothing on screen is the failure to avoid.
            if (!loadedTasks.some((item) => item && item.id === taskId)) {
                tasks.showError('That task is no longer in the list.');
                return;
            }
            taskLayers.open(taskId);
        }

        return {
            open,
            destroy() {
                destroyed = true;
                taskLayers.closeAll();
            },
        };
    }

    return { create };
})();
