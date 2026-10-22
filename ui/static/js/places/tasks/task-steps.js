/**
 * BossMod AI — a task's steps: one step's row, and the detail's Recent steps.
 *
 * A step is one thing the agent did while working the task: its own note,
 * the command it ran, and the one-line result (core/agent_loop/task_steps.py).
 * All three are shown verbatim, exactly as the agent and the runtime wrote
 * them; the action's own name is shown only when there is neither a note nor
 * a command. Nothing here rewrites or fills in what the agent said.
 *
 * The runtime writes a step when it starts and again when it ends, and sends
 * the `task_step` topic both times, in the same row shape the API returns. So
 * a row is upserted by id, a long command shows while it runs, and a step
 * that never ended (its turn died) honestly says so on its result line: "No
 * result yet" while it is the newest, "No result recorded" once anything came
 * after it.
 *
 * Recent steps holds the newest few (the server's `limit`, a setting). A live
 * step that pushes the list past it trims the oldest and offers "See all
 * steps", the full history (task-steps-history.js). Live steps that land
 * while the first load is in flight are held and applied once it settles. A
 * `resync` (the socket came back) reloads, because the live steps sent while
 * it was down are lost.
 */
const BossModTaskSteps = (() => {
    const { h } = BossModDom;

    const TITLE = 'Recent steps';
    const LOADING_COPY = 'Loading steps…';
    const EMPTY_COPY = 'No steps recorded yet.';
    const ERROR_COPY = 'Could not load steps.';
    const RETRY_LABEL = 'Try again';
    const SEE_ALL_LABEL = 'See all steps';

    /** A finished step's glyph, and the word a screen reader hears for it. */
    const OUTCOMES = Object.freeze({
        ok: Object.freeze({ icon: 'check-circle-2', word: 'Done' }),
        failed: Object.freeze({ icon: 'octagon-x', word: 'Failed' }),
        approval: Object.freeze({ icon: 'hand', word: 'Waiting for your approval' }),
        consent: Object.freeze({ icon: 'lock', word: 'Waiting for your permission' }),
    });
    /**
     * A step with no result: running, or its turn died. Nothing guesses which.
     * Its word is its visible result line rather than a hidden one.
     */
    const OPEN_NEWEST = Object.freeze({ icon: 'clock', word: 'No result yet' });
    const OPEN_OLDER = Object.freeze({ icon: 'circle-slash', word: 'No result recorded' });

    const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
    const isTime = (value) => typeof value === 'string' && !Number.isNaN(Date.parse(value));

    /**
     * Check one step row from the server or the `task_step` topic.
     *
     * @param {object} step
     * @returns {object} The same step.
     * @throws {TypeError} When a field the row is drawn from is missing or
     *   malformed: a made-up row would not be what the agent did.
     */
    function checkStep(step) {
        const ok = Boolean(step) && typeof step.id === 'string' && step.id !== ''
            && typeof step.task_id === 'string' && typeof step.agent_name === 'string'
            && typeof step.action === 'string' && isTime(step.started_at)
            && (step.ended_at === null || isTime(step.ended_at))
            && (step.outcome === null || hasOwn(OUTCOMES, step.outcome));
        if (!ok) throw new TypeError(`[task-steps] malformed step ${JSON.stringify(step && step.id)}`);
        return step;
    }

    /**
     * Read one page of steps off a response.
     *
     * @param {Response} res
     * @param {{withLimit: boolean}} opts  Recent steps' answer also carries
     *   its `limit`; the history's does not.
     * @returns {Promise<{steps: object[], has_more: boolean, limit?: number}>}
     * @throws {Error} (rejects) On a failed request or a malformed answer,
     *   which is said rather than painted as a task with no steps.
     */
    async function readPage(res, { withLimit }) {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = await res.json();
        if (!body || !Array.isArray(body.steps) || typeof body.has_more !== 'boolean'
            || (withLimit && !(Number.isInteger(body.limit) && body.limit >= 1))) {
            throw new TypeError('[task-steps] the steps answer is malformed');
        }
        body.steps.forEach(checkStep);
        return body;
    }

    /**
     * Newest first: by start time, then the full stamp (the server orders by
     * microseconds; Date.parse keeps milliseconds), then id — the server's
     * own ORDER BY started_at DESC, id DESC.
     */
    function newerFirst(a, b) {
        const byTime = Date.parse(b.started_at) - Date.parse(a.started_at);
        if (byTime !== 0) return byTime;
        if (a.started_at !== b.started_at) return a.started_at < b.started_at ? 1 : -1;
        if (a.id === b.id) return 0;
        return a.id < b.id ? 1 : -1;
    }

    /**
     * Put one step into a newest-first list: in place of the row with its id,
     * else where its start time puts it. Pure.
     *
     * @param {object[]} list  Newest first.
     * @param {object} step
     * @returns {object[]} A new list; `list` is not changed.
     */
    function upsert(list, step) {
        const next = list.filter((item) => item.id !== step.id);
        const at = next.findIndex((item) => newerFirst(step, item) < 0);
        next.splice(at === -1 ? next.length : at, 0, step);
        return next;
    }

    /** The glyph and word for one step; `newest` decides an open step's word. */
    function markOf(step, newest) {
        if (step.outcome === null) return { key: 'open', ...(newest ? OPEN_NEWEST : OPEN_OLDER) };
        return { key: step.outcome, ...OUTCOMES[step.outcome] };
    }

    /**
     * When a step started: the clock time today, the date and time on any
     * earlier day. formatActivityTime alone drops the time off an earlier
     * day, which leaves a history that spans days unreadable. "Today" is the
     * LOCAL calendar day, the rule BossModFormat keeps for every time on
     * screen.
     *
     * @param {string} iso  A checked `started_at`.
     * @returns {string}
     */
    function startedLabel(iso) {
        const startedToday = new Date(iso).toDateString() === new Date().toDateString();
        return startedToday ? BossModFormat.formatActivityTime(iso) : BossModFormat.formatDateTime(iso);
    }

    /**
     * One step as a list row: the outcome mark, when it started (and how long
     * it took once it ended), who ran it, then the note, the command and the
     * result as written. An open step's result line says it has none.
     *
     * @param {object} step  A checked step row.
     * @param {{newest: boolean}} opts  Whether this is the task's newest step,
     *   which is what an open step's word depends on.
     * @returns {HTMLElement} `li.task-step`, toned by `data-outcome`.
     */
    function row(step, { newest }) {
        const mark = markOf(step, newest);
        const open = step.outcome === null;
        const took = step.ended_at === null ? null
            : BossModFormat.formatDuration((Date.parse(step.ended_at) - Date.parse(step.started_at)) / 1000);
        const note = step.note || (step.command ? null : step.action);
        // An open step's word is already its visible result line, so its mark
        // carries no hidden copy: a screen reader reads it once.
        const result = open ? mark.word : step.result;
        return h('li', { class: 'task-step', 'data-outcome': mark.key },
            h('span', { class: 'task-step-mark' },
                h('i', { 'data-lucide': mark.icon, 'aria-hidden': 'true' }),
                open ? null : h('span', { class: 'visually-hidden' }, mark.word)),
            h('div', { class: 'task-step-body' },
                h('p', { class: 'task-step-meta' },
                    h('time', { datetime: step.started_at }, startedLabel(step.started_at)),
                    took ? ` · ${took}` : '',
                    ` · ${step.agent_name}`),
                note ? h('p', { class: 'task-step-note' }, note) : null,
                step.command ? h('code', { class: 'field-mono task-step-command' }, step.command) : null,
                result ? h('p', { class: 'task-step-result' }, result) : null));
    }

    /**
     * Patch a keyed list of rows to `steps`, the first being the newest. A
     * row is rebuilt only when what it shows changed, so a live step does not
     * redraw the rows (or drop a text selection) around it.
     *
     * @param {{sync: Function}} keyed  BossModDom.createKeyedList over the list.
     * @param {object[]} steps  Newest first.
     * @returns {void}
     */
    function syncRows(keyed, steps) {
        const built = keyed.sync(
            steps.map((step, index) => ({ step, newest: index === 0 })),
            (entry) => entry.step.id,
            ({ step, newest }) => [step.started_at, step.ended_at, step.outcome, step.result,
                step.note, step.command, step.agent_name, step.action, newest],
            (entry) => row(entry.step, { newest: entry.newest }));
        built.forEach((node) => BossModIcons.paint(node, 'task-steps'));
    }

    /**
     * Build the task detail's Recent steps section.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.bus  Event bus; `task_step` is read, and a `resync`
     *   reloads.
     * @param {string} deps.taskId
     * @param {() => void} deps.onSeeAll  Opens the task's full step history.
     * @returns {{element: HTMLElement, destroy: () => void}} `destroy`
     *   unsubscribes from both and drops a load still in flight.
     * @throws {Error} When any dependency is missing.
     */
    function createRecent(deps) {
        const { api, bus, taskId, onSeeAll } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-steps] deps.api is required');
        if (!bus || typeof bus.subscribe !== 'function') throw new Error('[task-steps] deps.bus is required');
        if (!taskId) throw new Error('[task-steps] deps.taskId is required');
        if (typeof onSeeAll !== 'function') throw new Error('[task-steps] deps.onSeeAll is required');

        const load = BossModGates.createLoadGeneration();
        const stateEl = h('div', { class: 'task-steps-state' });
        const listEl = h('ol', { class: 'task-steps' });
        const keyed = BossModDom.createKeyedList(listEl);
        const seeAll = h('button', {
            class: 'btn-link task-steps-more', type: 'button', onclick: () => onSeeAll(),
        }, SEE_ALL_LABEL);
        // "See all steps" follows the rows it extends, so the head stays the
        // one-line label every other section has.
        const element = h('section', { class: 'task-detail-section' },
            h('div', { class: 'task-detail-section-head' }, h('span', {}, TITLE)),
            stateEl, listEl, seeAll);

        /** 'loading' | 'error' | 'ready'. */
        let status = 'loading';
        /** Newest first, at most `limit` long. */
        let steps = [];
        /** The server's count; known once the first load lands. */
        let limit = null;
        let hasMore = false;
        /** Live steps that landed while the load was in flight. */
        let held = [];
        let destroyed = false;

        function render() {
            seeAll.hidden = status !== 'ready' || !hasMore;
            if (status === 'loading') {
                keyed.reset();
                stateEl.replaceChildren(h('p', { class: 'task-detail-meta' }, LOADING_COPY));
                return;
            }
            if (status === 'error') {
                keyed.reset();
                stateEl.replaceChildren(
                    h('p', { class: 'place-error-detail', role: 'alert' }, ERROR_COPY),
                    h('button', { class: 'btn', type: 'button', onclick: () => { void reload(); } }, RETRY_LABEL));
                return;
            }
            stateEl.replaceChildren(...(steps.length ? [] : [h('p', { class: 'task-detail-meta' }, EMPTY_COPY)]));
            syncRows(keyed, steps);
        }

        /** Upsert one live step; past the limit the oldest goes, one "See all" away. */
        function apply(step) {
            steps = upsert(steps, step);
            if (steps.length > limit) {
                steps = steps.slice(0, limit);
                hasMore = true;
            }
        }

        /**
         * Load the newest steps.
         * @returns {Promise<void>} Never rejects; a failure is the error state.
         */
        async function reload() {
            const loadId = load.next();
            status = 'loading';
            steps = [];
            hasMore = false;
            held = [];
            render();
            let page;
            try {
                page = await readPage(await api(`/api/tasks/${encodeURIComponent(taskId)}/steps/recent`,
                    { cache: 'no-store' }), { withLimit: true });
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[task-steps] could not load the recent steps', err);
                status = 'error';
                render();
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;
            steps = page.steps.slice();
            limit = page.limit;
            hasMore = page.has_more;
            status = 'ready';
            held.splice(0).forEach(apply);
            render();
        }

        const offStep = bus.subscribe('task_step', (step) => {
            checkStep(step);
            if (step.task_id !== taskId) return;
            if (status === 'loading') {
                held.push(step);
                return;
            }
            if (status !== 'ready') return; // Try again reloads it.
            apply(step);
            render();
        });
        // The steps sent while the socket was down are gone; read them again.
        const offResync = bus.subscribe('resync', () => { void reload(); });

        void reload();

        return {
            element,

            /**
             * Unsubscribe and drop any load in flight, so a late answer never
             * paints a closed detail.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                load.next();
                offStep();
                offResync();
            },
        };
    }

    return { row, upsert, readPage, checkStep, syncRows, createRecent };
})();
