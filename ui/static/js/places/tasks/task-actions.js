/**
 * BossMod AI — the operator's task actions, composed once.
 *
 * Cancel (tasks-cancel.js), Mark complete (tasks-complete.js) and Edit
 * (task-edit-form.js) are three modules with one pair of outcomes: the server
 * accepted, so the caller refreshes; or it did not, so the caller says why.
 * Two callers open task details — the Tasks place and the desk
 * (context/desk-task-opener.js) — and each wires this one object into
 * BossModTaskLayers rather than three.
 */
const BossModTaskActions = (() => {
    /**
     * Build the action set.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.store  Application store; the edit form reads the
     *   roster from it.
     * @param {(ids: string[]) => void} deps.onChanged  The server accepted a
     *   cancel, a completion or an edit of these tasks.
     * @param {(message: string) => void} deps.onError  A cancel or completion
     *   failed after its dialog closed. (An edit shows its failures in its
     *   own dialog, which stays open.)
     * @returns {{cancelOne: (task: object) => void,
     *   cancelMany: (tasks: object[]) => void,
     *   completeOne: (task: object) => void,
     *   edit: (task: object) => void}}
     * @throws {Error} When a dependency is missing.
     */
    function create(deps) {
        const { api, store, onChanged, onError } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-actions] deps.api is required');
        if (!store) throw new Error('[task-actions] deps.store is required');
        if (typeof onChanged !== 'function') throw new Error('[task-actions] deps.onChanged is required');
        if (typeof onError !== 'function') throw new Error('[task-actions] deps.onError is required');

        const canceller = BossModTasksCancel.createCanceller({ api, onCancelled: onChanged, onError });
        const completer = BossModTasksComplete.createCompleter({ api, onCompleted: onChanged, onError });

        return {
            cancelOne: canceller.cancelOne,
            cancelMany: canceller.cancelMany,
            completeOne: completer.completeOne,
            /**
             * Open the edit form for one open task.
             * @param {object} task
             * @returns {void}
             */
            edit(task) {
                BossModTaskEditForm.openEditForm({
                    api,
                    store,
                    task,
                    onSaved: (saved) => onChanged([saved.id]),
                });
            },
        };
    }

    return { create };
})();
