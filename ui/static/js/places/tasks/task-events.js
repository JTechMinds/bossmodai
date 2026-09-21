/**
 * BossMod AI — a task's activity thread.
 *
 * Its own request, its own load generation, and its own four states. Split out
 * of the detail panel because it is the only part of that panel that fetches,
 * and because a failed event load must be visible as a failure rather than
 * showing as "no activity recorded yet" — those are different facts.
 *
 * Each event reads as a sentence — "Jim marked it blocked · no progress" —
 * built by the pure describeEvent(), rather than as a type badge over the raw
 * text the engine wrote.
 */
const BossModTaskEvents = (() => {
    const { h, clear } = BossModDom;
    const COLUMNS = BossModTasksColumns;

    /** transition_task's own wording: `Status {from} → {to}` then `.` or `: {note}` (core/tasking/transitions.py). */
    const STATUS_LINE = /^Status (\w+) → (\w+)(?:[:.]\s*)?([\s\S]*)$/;

    /** Event types that read as a verb. Anything else shows its content as written. */
    const TYPE_VERBS = Object.freeze({
        comment: 'commented',
        clarification: 'asked for clarification',
        answer: 'answered',
        blocker: 'flagged a blocker',
        completion: 'reported it done',
        reprioritized: 'reprioritized it',
    });

    /**
     * One event as the parts of a sentence.
     *
     * A status change the engine wrote in its own format becomes the status's
     * verb and whatever note followed; any other shape — including a status
     * update written some other way — keeps its content as written, so
     * nothing the engine recorded is dropped by a parse that did not fit.
     *
     * @param {object} event  One row from GET /api/tasks/{id}/events.
     * @returns {{actor: string, verb: string, detail: string}} `verb` and
     *   `detail` may be empty; the actor never is.
     */
    function describeEvent(event) {
        const actor = String(event.author_name || 'System');
        const content = String(event.content || '').trim();
        if (event.event_type === 'status_update') {
            const match = STATUS_LINE.exec(content);
            if (match && COLUMNS.STATUS_VERBS[match[2]]) {
                return { actor, verb: COLUMNS.STATUS_VERBS[match[2]], detail: match[3].trim() };
            }
        }
        return { actor, verb: TYPE_VERBS[event.event_type] || '', detail: content };
    }

    /**
     * Build the activity section for one task.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string} deps.taskId
     * @param {(agentId: string) => (string|undefined)} deps.colorOf  An
     *   author's roster colour, for the avatar beside each sentence.
     * @returns {{element: HTMLElement, destroy: () => void}}
     * @throws {Error} When api, taskId or colorOf is missing.
     */
    function createTaskEvents(deps) {
        const { api, taskId, colorOf } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-events] deps.api is required');
        if (!taskId) throw new Error('[task-events] deps.taskId is required');
        if (typeof colorOf !== 'function') throw new Error('[task-events] deps.colorOf is required');

        const load = BossModGates.createLoadGeneration();
        const body = h('div', { class: 'task-detail-events' });
        const element = h('section', { class: 'task-detail-section' },
            h('p', { class: 'task-detail-heading' }, 'Activity'), body);
        let destroyed = false;

        function row(event) {
            const { actor, verb, detail } = describeEvent(event);
            return h('div', { class: 'task-detail-event' },
                h('span', { class: 'task-detail-event-time' },
                    BossModFormat.formatRelativeTime(event.created_at)),
                h('p', { class: 'task-detail-event-text' },
                    BossModAvatar.create({
                        name: event.author_name,
                        color: event.author_agent_id ? colorOf(event.author_agent_id) : undefined,
                        size: 'chip',
                    }),
                    h('strong', {}, actor),
                    verb ? ` ${verb}` : '',
                    detail ? ` · ${detail}` : ''));
        }

        /**
         * Fetch and paint the thread.
         *
         * @returns {Promise<void>} Never rejects: a failure becomes the error
         *   state with a retry, never an empty list that reads as "nothing
         *   happened".
         */
        async function refresh() {
            const loadId = load.next();
            clear(body);
            body.append(h('p', { class: 'task-detail-meta' }, 'Loading activity…'));
            let events;
            try {
                const res = await api(`/api/tasks/${encodeURIComponent(taskId)}/events`,
                    { cache: 'no-store' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                events = await res.json();
                if (!Array.isArray(events)) throw new TypeError('the events endpoint returned no list');
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[task-events] could not load the thread', err);
                clear(body);
                body.append(
                    h('p', { class: 'place-error-detail', role: 'alert' }, 'Failed to load activity.'),
                    h('button', { class: 'btn', type: 'button', onclick: () => { void refresh(); } },
                        'Retry'));
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;

            // Newest first, with the id as the tie-break so two events written
            // in the same second keep a stable order between repaints.
            events.sort((a, b) => {
                const aAt = Date.parse((a && a.created_at) || '') || 0;
                const bAt = Date.parse((b && b.created_at) || '') || 0;
                if (bAt !== aAt) return bAt - aAt;
                return String((b && b.id) || '').localeCompare(String((a && a.id) || ''));
            });

            clear(body);
            if (events.length === 0) {
                body.append(h('p', { class: 'task-detail-meta' }, 'No activity recorded yet.'));
                return;
            }
            events.forEach((event) => body.append(row(event)));
        }

        void refresh();

        return {
            element,

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

    return { createTaskEvents, describeEvent };
})();
