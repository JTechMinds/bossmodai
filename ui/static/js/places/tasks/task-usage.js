/**
 * BossMod AI — what a task's work has used, as the detail draws it.
 *
 * Three facts for tuning the Context Window settings: Context (the latest
 * model call's prompt against the current Context window, and the largest
 * one so far), Tokens (in, out, and model calls over the task's work), and
 * Summarized (how often the saved work was folded into its step summary,
 * and how many folds failed). GET /api/tasks/{id}/usage serves them.
 *
 * Only the task's work turns count: the turns that resume its work, with
 * their folds. Chat turns about the task (the accept, a message mid-task, a
 * follow-up) run on chat-mode context, not on the task's working context,
 * so they are left out (core/agent_loop/task_usage.py); a subtask shows its
 * own. The numbers are as the provider reported them.
 *
 * Why it is a second fact list under the facts grid rather than rows inside
 * it: the facts change when the task does, these change with every work
 * turn, so they fetch on their own and re-read whenever the task's current
 * assignee finishes a turn (the `diagnostic` topic, which the server sends
 * after it writes the usage) and after a reconnect (`resync`). The same
 * component, sharing the facts grid's label column (task-detail.js wraps
 * both in one grid, places.css), makes them read as more facts. Like Before
 * this task (task-origin.js) it has its own load generation and four
 * states: loading, none (the element hides where it is placed, so the
 * task's first work turn shows it without a reload), an error with Retry,
 * and the facts.
 */
const BossModTaskUsage = (() => {
    const { h } = BossModDom;
    const FORMAT = BossModFormat;

    /** Counts the answer must carry, each a whole number of at least 0. */
    const COUNTS = Object.freeze([
        'work_turns', 'model_calls', 'prompt_tokens', 'completion_tokens',
        'summaries', 'summarized_steps', 'failed_summaries', 'window_tokens',
    ]);

    const isCount = (value) => Number.isInteger(value) && value >= 0;

    /** `1 call`, `564 calls`: the count as formatNumber writes it, and the noun it takes. */
    function counted(count, one, many) {
        return `${FORMAT.formatNumber(count)} ${count === 1 ? one : many}`;
    }

    /**
     * The usage in the endpoint's answer.
     *
     * @param {object} body  `{usage: row|null}`.
     * @returns {object|null} The row, or null when the task has had no work.
     * @throws {TypeError} When the answer is not that shape: a count that is
     *   not a whole number, a context size without its peak, or folds
     *   without a time to say when the last one was. A made-up number would
     *   read as a measurement.
     */
    function usageOf(body) {
        if (!body || typeof body !== 'object' || !('usage' in body)) {
            throw new TypeError('the usage endpoint answered without usage');
        }
        const { usage } = body;
        if (usage === null) return null;
        if (!usage || typeof usage !== 'object' || !COUNTS.every((key) => isCount(usage[key]))) {
            throw new TypeError('the usage endpoint answered with a malformed count');
        }
        const latest = usage.latest_prompt_tokens;
        if (latest !== null && !(isCount(latest) && isCount(usage.peak_prompt_tokens))) {
            throw new TypeError('the usage endpoint answered with a malformed context size');
        }
        if (usage.summaries > 0 && (typeof usage.last_summary_at !== 'string'
            || Number.isNaN(Date.parse(usage.last_summary_at)))) {
            throw new TypeError('the usage endpoint answered folds without the time of the last one');
        }
        return usage;
    }

    /**
     * The three facts, in BossModFactList's shape.
     * @param {object} usage  A row usageOf() accepted.
     * @returns {Array<{label: string, value: string}>}
     */
    function facts(usage) {
        const context = usage.latest_prompt_tokens === null
            ? 'No work yet'
            : `${FORMAT.formatNumber(usage.latest_prompt_tokens)} of ${FORMAT.formatNumber(usage.window_tokens)} now`
                + ` · peak ${FORMAT.formatNumber(usage.peak_prompt_tokens)}`;
        const tokens = `${FORMAT.formatNumber(usage.prompt_tokens)} in · `
            + `${FORMAT.formatNumber(usage.completion_tokens)} out · ${counted(usage.model_calls, 'call', 'calls')}`;
        const summarized = usage.summaries === 0 ? ['Not yet'] : [
            counted(usage.summaries, 'time', 'times'),
            counted(usage.summarized_steps, 'step', 'steps'),
            `last ${FORMAT.formatRelativeTime(usage.last_summary_at)}`,
        ];
        // A failure is news only when there was one.
        if (usage.failed_summaries > 0) summarized.push(`${FORMAT.formatNumber(usage.failed_summaries)} failed`);
        return [
            { label: 'Context', value: context },
            { label: 'Tokens', value: tokens },
            { label: 'Summarized', value: summarized.join(' · ') },
        ];
    }

    /** The server's reason for a refused request, or its status when it sent none. */
    async function refusal(res) {
        // A body that is not JSON has no reason in it; the status still says what happened.
        const payload = await res.json().catch(() => null);
        const detail = payload && payload.detail;
        return typeof detail === 'string' && detail.trim() ? detail : `The server answered ${res.status}.`;
    }

    /**
     * Build the usage facts for one task.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.bus  Event bus; `diagnostic` and `resync` are
     *   followed until destroy().
     * @param {string} deps.taskId  The task whose usage is shown.
     * @param {() => (string|null)} deps.assigneeOf  The task's assignee id
     *   now, or null when it has none. Asked on every `diagnostic`, so a
     *   reassignment is followed: only that agent's turns re-read the usage.
     * @returns {{element: HTMLElement, destroy: () => void}} `element` shows
     *   the loading line at once and stays where it is placed: hidden while
     *   the task has had no work, shown once a re-read finds some.
     * @throws {Error} When api, bus, taskId or assigneeOf is missing.
     */
    function section(deps) {
        const { api, bus, taskId, assigneeOf } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-usage] deps.api is required');
        if (!bus || typeof bus.subscribe !== 'function') throw new Error('[task-usage] deps.bus is required');
        if (!taskId) throw new Error('[task-usage] deps.taskId is required');
        if (typeof assigneeOf !== 'function') throw new Error('[task-usage] deps.assigneeOf is required');

        const load = BossModGates.createLoadGeneration();
        const element = h('div', { class: 'task-detail-usage' });
        let destroyed = false;
        /** The facts are on screen: a re-read after a turn leaves them until the new ones land. */
        let showing = false;

        /**
         * Fetch and paint the usage.
         *
         * @returns {Promise<void>} Never rejects: a failure becomes the error
         *   state with a retry.
         */
        async function refresh() {
            const loadId = load.next();
            // Hidden (no work yet) stays hidden until an answer says otherwise.
            if (!showing && !element.hidden) {
                element.replaceChildren(
                    h('p', { class: 'task-detail-meta' }, 'Loading what this task has used…'));
            }
            let usage;
            let list = null;
            try {
                const res = await api(`/api/tasks/${encodeURIComponent(taskId)}/usage`, { cache: 'no-store' });
                if (!res.ok) throw new Error(await refusal(res));
                usage = usageOf(await res.json());
                if (usage) list = BossModFactList.create(facts(usage), { pairsPerRow: 2 });
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[task-usage] could not load what the task has used', err);
                showing = false;
                element.hidden = false;
                element.replaceChildren(
                    h('p', { class: 'place-error-detail', role: 'alert' },
                        `Couldn't load what this task has used: ${(err && err.message) || err}`),
                    h('button', { class: 'btn', type: 'button', onclick: () => { void refresh(); } }, 'Retry'));
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;
            if (usage === null) {
                // Hidden, not removed: it stays in place, so a later re-read can show it.
                showing = false;
                element.hidden = true;
                element.replaceChildren();
                return;
            }
            showing = true;
            element.hidden = false;
            element.replaceChildren(list);
        }

        const disposers = [
            // The server sends a turn's diagnostic after it writes the turn's usage.
            bus.subscribe('diagnostic', (summary) => {
                const assignee = assigneeOf();
                if (assignee !== null && typeof assignee !== 'string') {
                    throw new TypeError('[task-usage] assigneeOf() must return an agent id or null');
                }
                if (assignee !== null && summary && summary.agent_id === assignee) void refresh();
            }),
            bus.subscribe('resync', () => { void refresh(); }),
        ];
        void refresh();

        return {
            element,

            /**
             * Stop following the bus and abandon any in-flight load; a late
             * response is dropped, not painted.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                load.next();
                disposers.forEach((dispose) => dispose());
            },
        };
    }

    return { section };
})();
