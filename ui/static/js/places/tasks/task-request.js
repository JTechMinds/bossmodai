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
 * message is markdown clamped the way the instructions are
 * (core/clamped-markdown.js), since a pasted log can run very long.
 */
const BossModTaskRequest = (() => {
    const { h } = BossModDom;

    /** The authors a saved message can have: TaskRequestMessage.author_type. */
    const AUTHOR_TYPES = new Set(['human', 'agent', 'system']);

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
     * Who wrote one message: "You" for the operator (the word the facts use),
     * an agent's chip and name, or a system author's name.
     *
     * @param {object} message
     * @param {(agentId: string) => (string|undefined)} colorOf
     * @returns {string|Array<Node|string>}
     */
    function authorOf(message, colorOf) {
        if (message.author_type === 'human') return 'You';
        if (message.author_type === 'system') return message.author_name;
        return [
            BossModAvatar.create({ name: message.author_name, color: colorOf(message.author_agent_id), size: 'chip' }),
            message.author_name,
        ];
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
     * @returns {{element: HTMLElement, measure: () => void}|null} null when
     *   neither the task nor its parent has a request. `measure` decides
     *   every message's clamp and must be called once `element` is on screen.
     * @throws {Error} When the list or colorOf is missing (a forwarded
     *   request could not be looked up, which is a different fact), or when
     *   a message has no content, no known author type, or a non-human author
     *   without a name: a made-up line would not be what the agent reads.
     */
    function section(task, deps) {
        const { tasks, colorOf } = deps || {};
        if (!Array.isArray(tasks)) throw new Error('[task-request] section needs the task list');
        if (typeof colorOf !== 'function') throw new Error('[task-request] section needs colorOf');
        const { messages, parent } = sourceOf(task, tasks);
        if (messages.length === 0) return null;
        const rows = messages.map((message, index) => {
            const named = message.author_type === 'human'
                || (typeof message.author_name === 'string' && message.author_name.trim() !== '');
            if (!AUTHOR_TYPES.has(message.author_type) || typeof message.content !== 'string'
                || !message.content.trim() || !named) {
                throw new Error(`[task-request] message ${index + 1} of task "${task.id}" is malformed`);
            }
            const clamp = BossModClampedMarkdown.create({
                text: message.content,
                className: 'task-detail-request-text',
                moreLabel: 'Show the full message',
            });
            return {
                clamp,
                element: h('div', { class: 'task-detail-request' },
                    h('p', { class: 'task-detail-request-author' }, authorOf(message, colorOf)),
                    clamp.element),
            };
        });
        const what = parent
            ? `From the parent task, “${parent.title || 'Untitled task'}”, word for word.`
            : 'What was asked, word for word.';
        return {
            element: h('section', { class: 'task-detail-section' },
                h('p', { class: 'task-detail-heading' }, 'Request'),
                h('p', { class: 'task-detail-meta' }, what),
                ...rows.map((row) => row.element)),
            measure: () => rows.forEach((row) => row.clamp.measure()),
        };
    }

    return { section };
})();
