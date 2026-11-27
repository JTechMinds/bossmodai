/**
 * BossMod AI — the request behind a task, as the detail draws it.
 *
 * A task's request is what was asked, word for word, with who asked it
 * (core/tasking/requests.py). The system saves it when an agent turns a
 * message into a task; the agent's own brief is the description, which the
 * detail shows under "Task", below this. A child task with no request of
 * its own shows its parent's, labelled as such: the same rule the agent's
 * prompt follows, so the operator sees what the assignee is working from.
 *
 * Read-only in view and in Edit mode alike: nobody edits a request. Each
 * message is drawn by task-message.js, the row the conversation before the
 * task (task-origin.js) uses too.
 */
const BossModTaskRequest = (() => {
    const { h } = BossModDom;

    /**
     * The messages to show: the task's own, else its parent's.
     *
     * @param {object} task
     * @param {object[]} tasks  The page's list, to find the parent.
     * @returns {{messages: object[], parent: object|null}} `parent` is set
     *   only when the messages are the parent's.
     */
    function sourceOf(task, tasks) {
        const own = task.request_messages || [];
        if (own.length > 0) return { messages: own, parent: null };
        const parent = task.parent_task_id ? tasks.find((item) => item.id === task.parent_task_id) : null;
        const inherited = (parent && parent.request_messages) || [];
        return inherited.length > 0 ? { messages: inherited, parent } : { messages: [], parent: null };
    }

    /**
     * The Request section: a heading, what the messages are, then each
     * message under its author, oldest first.
     *
     * @param {object} task
     * @param {object} deps
     * @param {object[]} deps.tasks  The page's list, to find the parent.
     * @param {(agentId: string) => (string|undefined)} deps.colorOf  Roster
     *   colours for an agent author's chip.
     * @param {Function} deps.api  Authenticated fetch helper: a path link in
     *   a message opens through it, as `task`'s assignee sees the path when
     *   the message has no agent author — for a parent's request too, since
     *   this task's assignee is the one working from it (task-message.js).
     * @returns {{element: HTMLElement, measure: () => void}|null} null when
     *   neither the task nor its parent has a request. `measure` decides
     *   every message's clamp and must be called once `element` is on screen.
     * @throws {Error} When the list, colorOf or api is missing (a forwarded
     *   request could not be looked up, which is a different fact), or when
     *   a message has no content, no known author type, or a non-human author
     *   without a name: a made-up line would not be what the agent reads.
     */
    function section(task, deps) {
        const { tasks, colorOf, api } = deps || {};
        if (!Array.isArray(tasks)) throw new Error('[task-request] section needs the task list');
        if (typeof colorOf !== 'function') throw new Error('[task-request] section needs colorOf');
        if (typeof api !== 'function') throw new Error('[task-request] section needs api');
        const { messages, parent } = sourceOf(task, tasks);
        if (messages.length === 0) return null;
        const rows = messages.map((message) => BossModTaskMessage.row(message, {
            colorOf,
            moreLabel: 'Show the full message',
            api,
            assigneeOf: () => task.assigned_to,
        }));
        const what = parent
            ? `From the parent task, “${parent.title || 'Untitled task'}”, word for word.`
            : 'What was asked, word for word.';
        return {
            element: h('section', { class: 'task-detail-section' },
                h('p', { class: 'task-detail-heading' }, 'Request'),
                h('p', { class: 'task-detail-meta' }, what),
                ...rows.map((row) => row.element)),
            measure: () => rows.forEach((row) => row.measure()),
        };
    }

    return { section };
})();
