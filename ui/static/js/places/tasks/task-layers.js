/**
 * BossMod AI — task details as modal layers, wherever a task is opened from.
 *
 * Extracted from places/tasks/tasks-place.js when the desk's task rows stopped
 * switching to the Tasks place and began opening the task over the desk. The
 * page held the only code that could open a task as a layer — with the push
 * into a parent or subtask, the task actions and chat — and it read the page's
 * own list and actions, so nothing else could. The second caller is the reason
 * this is a module: the page and the desk both hand it their list and their
 * actions (places/tasks/task-actions.js), and the stack of open task layers is
 * kept here, once.
 *
 * It owns only WHICH task layers are open. The detail itself is
 * places/tasks/task-detail.js; the frame, the trail and ‹ are
 * core/overlays.js's.
 */
const BossModTaskLayers = (() => {
    /**
     * Build a task-layer controller.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper, for the detail's
     *   own reads.
     * @param {() => object[]} deps.getTasks  The caller's current task list,
     *   read at every open so a layer resolves its parent and children from
     *   the latest load rather than the one this was built with.
     * @param {(agentId: string) => (string|undefined)} deps.colorOf  Roster
     *   colours for the detail's avatars.
     * @param {{cancel: Function, complete: Function, resume: Function,
     *   update: Function, roster: Function}} deps.actions  The caller's BossModTaskActions: it
     *   owns cancelling, completing, resuming and updating; a detail only asks.
     * @param {(task: object) => void} deps.onOpenChat  Leaves for the task's
     *   conversation. Closing these layers on the way out is the caller's.
     * @returns {{ open: (taskId: string) => void, push: (taskId: string) => void,
     *   closeAll: () => void }} `open` is a new errand: every task layer this
     *   opened closes first, then the task opens over whatever is on screen.
     *   `push` is one step deeper — a parent or subtask link inside a detail —
     *   so ‹ walks back to the task it came from. `closeAll` closes every task
     *   layer this opened, top first.
     *   `open` and `push` throw what BossModTaskDetail.openTaskDetail throws —
     *   among it, a task id that is not in `getTasks()`.
     * @throws {Error} When any dependency is missing: a task door that fails
     *   at the click is worse than one that fails where it is built.
     */
    function create(deps) {
        const { api, getTasks, colorOf, actions, onOpenChat } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-layers] deps.api is required');
        if (typeof getTasks !== 'function') throw new Error('[task-layers] deps.getTasks is required');
        if (typeof colorOf !== 'function') throw new Error('[task-layers] deps.colorOf is required');
        if (!actions || typeof actions.cancel !== 'function' || typeof actions.complete !== 'function'
            || typeof actions.resume !== 'function' || typeof actions.update !== 'function'
            || typeof actions.roster !== 'function') {
            throw new Error('[task-layers] deps.actions must be a BossModTaskActions');
        }
        if (typeof onOpenChat !== 'function') throw new Error('[task-layers] deps.onOpenChat is required');

        /** The open task layers, base first. */
        let details = [];

        /** Close every open task layer, top first. */
        function closeAll() {
            details.slice().reverse().forEach((handle) => handle.close());
            details = [];
        }

        /** One task as a layer over whatever is on screen. */
        function push(taskId) {
            const handle = BossModTaskDetail.openTaskDetail({
                api,
                taskId,
                tasks: getTasks(),
                colorOf,
                onNavigate: push,
                actions,
                onOpenChat,
                onClose: () => { details = details.filter((item) => item !== handle); },
            });
            details.push(handle);
        }

        /** A new errand, so any open task layers go first. */
        function open(taskId) {
            closeAll();
            push(taskId);
        }

        return { open, push, closeAll };
    }

    return { create };
})();
