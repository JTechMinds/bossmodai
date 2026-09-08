/**
 * BossMod AI — a task's activity thread.
 *
 * Its own request, its own load generation, and its own four states. Split out
 * of the detail panel because it is the only part of that panel that fetches,
 * and because a failed event load must be visible as a failure rather than
 * showing as "no activity recorded yet" — those are different facts.
 */
const BossModTaskEvents = (() => {
    const { h, clear } = BossModDom;

    /** Event type -> the word shown on its badge. Unknown types show as-is. */
    const KNOWN_TYPES = Object.freeze([
        'comment', 'clarification', 'answer', 'status_update', 'blocker',
        'completion', 'assignment', 'reprioritized', 'system',
    ]);

    /**
     * Build the activity section for one task.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string} deps.taskId
     * @returns {{element: HTMLElement, destroy: () => void}}
     * @throws {Error} When api or taskId is missing.
     */
    function createTaskEvents(deps) {
        const { api, taskId } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-events] deps.api is required');
        if (!taskId) throw new Error('[task-events] deps.taskId is required');

        const load = BossModGates.createLoadGeneration();
        const body = h('div', { class: 'task-detail-events' });
        const element = h('section', { class: 'task-detail-section' },
            h('p', { class: 'task-detail-heading' }, 'Activity'), body);
        let destroyed = false;

        function row(event) {
            const type = String(event.event_type || 'event');
            return h('div', { class: 'task-detail-event' },
                h('div', { class: 'task-detail-event-head' },
                    h('span', {}, event.author_name || 'System'),
                    h('span', {
                        class: 'status-pill',
                        'data-event-type': KNOWN_TYPES.indexOf(type) === -1 ? 'other' : type,
                    }, type.replace(/_/g, ' ')),
                    event.created_at
                        ? h('span', {}, BossModFormat.formatRelativeTime(event.created_at))
                        : null),
                h('p', { class: 'task-detail-event-body' }, event.content || ''));
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

    return { createTaskEvents };
})();
