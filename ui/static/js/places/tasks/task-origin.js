/**
 * BossMod AI — the conversation before a task, as the detail draws it.
 *
 * When an agent took a task on from a DM or thread, the system saved the
 * conversation before the request (core/agent_loop/origin_conversation.py),
 * and every work turn of the task shows it. This section shows the operator
 * exactly those messages: GET /api/tasks/{id}/origin-conversation answers
 * with the same view the work turn's history is built from, fitted to the
 * current "Conversation before the task" share. A child task with no
 * conversation of its own shows its parent's, labelled as such.
 *
 * Read-only, and collapsed by default behind a "Show N messages" toggle: a
 * conversation can be long, and the request and the brief come first. Like
 * the Activity section (task-events.js) it is the only part of its kind that
 * fetches, so it has its own load generation and four states: loading, none
 * (the section removes itself, as References is absent), an error that says
 * so rather than reading as "nothing was said", and the messages, each drawn
 * by task-message.js and measured when first shown.
 */
const BossModTaskOrigin = (() => {
    const { h } = BossModDom;

    /** Each section's message list needs its own id for the toggle's aria-controls. */
    let serial = 0;

    /** The collapsed toggle's words: "Show 1 message", "Show 12 messages". */
    function showLabel(count) {
        return `Show ${count} ${count === 1 ? 'message' : 'messages'}`;
    }

    /**
     * The view in the endpoint's answer.
     *
     * @param {object} body  `{origin: view|null}`.
     * @returns {object|null} The view, or null when the task has none.
     * @throws {TypeError} When the answer is not that shape: a view needs
     *   `where` and at least one message, or there is nothing true to draw.
     */
    function originOf(body) {
        if (!body || typeof body !== 'object' || !('origin' in body)) {
            throw new TypeError('the origin endpoint answered without an origin');
        }
        const { origin } = body;
        if (origin === null) return null;
        if (!origin || typeof origin.where !== 'string' || !Array.isArray(origin.messages)
            || origin.messages.length === 0) {
            throw new TypeError('the origin endpoint answered with a malformed conversation');
        }
        return origin;
    }

    /** The server's reason for a refused request, or its status when it sent none. */
    async function refusal(res) {
        // A body that is not JSON has no reason in it; the status still says what happened.
        const payload = await res.json().catch(() => null);
        const detail = payload && payload.detail;
        return typeof detail === 'string' && detail.trim() ? detail : `The server answered ${res.status}.`;
    }

    /**
     * Build the "Before this task" section for one task.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string} deps.taskId
     * @param {(agentId: string) => (string|undefined)} deps.colorOf  Roster
     *   colours for an agent author's chip.
     * @param {() => (string|null|undefined)} deps.assigneeOf  The task's
     *   current assignee, read when a path link in a message is clicked:
     *   whose `/me` and `/projects` the path means when the message has no
     *   agent author (task-message.js).
     * @returns {{element: HTMLElement, isEmpty: () => boolean, destroy: () => void}}
     *   `element` shows the loading line at once; `isEmpty` turns true once
     *   the task proved to have no conversation, and the element has then
     *   removed itself, so a repaint leaves it out.
     * @throws {Error} When api, taskId, colorOf or assigneeOf is missing.
     */
    function section(deps) {
        const { api, taskId, colorOf, assigneeOf } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-origin] deps.api is required');
        if (!taskId) throw new Error('[task-origin] deps.taskId is required');
        if (typeof colorOf !== 'function') throw new Error('[task-origin] deps.colorOf is required');
        if (typeof assigneeOf !== 'function') throw new Error('[task-origin] deps.assigneeOf is required');

        const load = BossModGates.createLoadGeneration();
        const element = h('section', { class: 'task-detail-section' });
        let destroyed = false;
        let empty = false;
        serial += 1;
        const listId = `task-origin-messages-${serial}`;

        /**
         * The messages and their toggle, built before anything is painted,
         * so a malformed message becomes the error state, never half a list.
         * @param {object} origin
         * @returns {Node[]}
         * @throws {Error} When a message is malformed (task-message.js).
         */
        function content(origin) {
            const rows = origin.messages.map((message) => BossModTaskMessage.row(message, {
                colorOf,
                moreLabel: 'Show the full message',
                api,
                assigneeOf,
            }));
            const list = h('div', { class: 'task-detail-origin-messages', id: listId },
                ...rows.map((row) => row.element));
            list.hidden = true;
            let measured = false;
            const toggle = h('button', {
                class: 'btn-link task-detail-origin-toggle',
                type: 'button',
                'aria-expanded': 'false',
                'aria-controls': listId,
                onclick: () => {
                    const open = list.hidden;
                    list.hidden = !open;
                    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
                    toggle.textContent = open ? 'Hide messages' : showLabel(rows.length);
                    // A clamp needs a height, which a hidden list does not have.
                    if (open && !measured) {
                        measured = true;
                        rows.forEach((row) => row.measure());
                    }
                },
            }, showLabel(rows.length));
            // A forwarded conversation is the parent's: the view names its title.
            const what = typeof origin.parent_title === 'string'
                ? `From the parent task, “${origin.parent_title}”: the conversation it came from (${origin.where}), as the agent sees it.`
                : `The conversation this task came from (${origin.where}), as the agent sees it while it works.`;
            return [
                h('p', { class: 'task-detail-heading' }, 'Before this task'),
                h('p', { class: 'task-detail-meta' }, what),
                toggle,
                list,
            ];
        }

        /**
         * Fetch and paint the conversation.
         *
         * @returns {Promise<void>} Never rejects: a failure becomes the error
         *   state with a retry.
         */
        async function refresh() {
            const loadId = load.next();
            element.replaceChildren(
                h('p', { class: 'task-detail-meta' }, 'Loading the conversation before this task…'));
            let origin;
            let nodes = [];
            try {
                const res = await api(`/api/tasks/${encodeURIComponent(taskId)}/origin-conversation`,
                    { cache: 'no-store' });
                if (!res.ok) throw new Error(await refusal(res));
                origin = originOf(await res.json());
                if (origin) nodes = content(origin);
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[task-origin] could not load the conversation before the task', err);
                element.replaceChildren(
                    h('p', { class: 'place-error-detail', role: 'alert' },
                        `Couldn't load the conversation before this task: ${(err && err.message) || err}`),
                    h('button', { class: 'btn', type: 'button', onclick: () => { void refresh(); } }, 'Retry'));
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;
            if (origin === null) {
                empty = true;
                element.remove();
                return;
            }
            element.replaceChildren(...nodes);
        }

        void refresh();

        return {
            element,
            isEmpty: () => empty,

            /**
             * Abandon any in-flight load; a late response is dropped, not painted.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                load.next();
            },
        };
    }

    return { section };
})();
