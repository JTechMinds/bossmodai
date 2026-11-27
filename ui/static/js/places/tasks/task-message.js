/**
 * BossMod AI — one saved conversation message, as the task detail draws it.
 *
 * A task keeps messages word for word with their authors in two places: its
 * request (task-request.js) and the conversation before it
 * (task-origin.js). Both draw each message the same way, so the row lives
 * here, extracted on its second use: the author ("You" for the operator, an
 * agent's chip and name, or a system author's name) over the message as
 * markdown, clamped the way the instructions are (core/clamped-markdown.js),
 * since a pasted log can run very long.
 *
 * A link to a workspace path in a message (`[verdict](/projects/x.md)`) opens
 * in the app through places/files/path-opener.js, as it does in the chat the
 * message came from. `/me` and `/projects` mean something only to one agent,
 * so the path is read as the message's agent author sees it, else as the
 * task's current assignee does: the chat rule, where the operator's line
 * falls back to the agent reading it. With neither, the opener reports that
 * no agent is recorded, on the link. Only an absolute path is a link: a saved
 * message records no working folder.
 */
const BossModTaskMessage = (() => {
    const { h } = BossModDom;

    /** The authors a saved message can have: TaskMessage.author_type (core/models/task.py). */
    const AUTHOR_TYPES = new Set(['human', 'agent', 'system']);

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
     * One message under its author.
     *
     * @param {object} message  A saved message: `{author_type,
     *   author_agent_id, author_name, content}`.
     * @param {object} options
     * @param {(agentId: string) => (string|undefined)} options.colorOf
     *   Roster colours for an agent author's chip.
     * @param {string} options.moreLabel  What the clamp's reveal link says.
     * @param {Function} options.api  Authenticated fetch helper; a path link
     *   opens through it.
     * @param {() => (string|null|undefined)} options.assigneeOf  The task's
     *   assignee, read when a link is clicked (so a reassignment is followed):
     *   whose `/me` and `/projects` a path means when the message has no
     *   agent author.
     * @returns {{element: HTMLElement, measure: () => void}} `measure`
     *   decides the clamp and must be called once `element` is on screen.
     * @throws {Error} When colorOf, moreLabel, api or assigneeOf is missing,
     *   or when the message has no content, no known author type, or a
     *   non-human author without a name: a made-up line would not be what the
     *   agent reads.
     */
    function row(message, options) {
        const { colorOf, moreLabel, api, assigneeOf } = options || {};
        if (typeof colorOf !== 'function') throw new Error('[task-message] row needs colorOf');
        if (typeof moreLabel !== 'string' || !moreLabel.trim()) throw new Error('[task-message] row needs moreLabel');
        if (typeof api !== 'function') throw new Error('[task-message] row needs api');
        if (typeof assigneeOf !== 'function') throw new Error('[task-message] row needs assigneeOf');
        const named = Boolean(message) && (message.author_type === 'human'
            || (typeof message.author_name === 'string' && message.author_name.trim() !== ''));
        if (!message || !AUTHOR_TYPES.has(message.author_type) || typeof message.content !== 'string'
            || !message.content.trim() || !named) {
            throw new Error('[task-message] the message is malformed');
        }
        const clamp = BossModClampedMarkdown.create({
            text: message.content,
            className: 'task-detail-message-text',
            moreLabel,
            links: {
                resolve: BossModMarkdownLinks.resolveAbsolutePath,
                open: (p) => BossModPathOpener.openPath(api, p, message.author_agent_id || assigneeOf() || ''),
            },
        });
        return {
            element: h('div', { class: 'task-detail-message' },
                h('p', { class: 'task-detail-message-author' }, authorOf(message, colorOf)),
                clamp.element),
            measure: clamp.measure,
        };
    }

    return { row };
})();
