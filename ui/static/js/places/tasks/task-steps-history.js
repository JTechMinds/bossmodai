/**
 * BossMod AI — a task's full step history, as a layer over its detail.
 *
 * Separate from task-steps.js because it owns a modal and paging rather than
 * a section. Opened from the detail's "See all steps", it is a layer in the
 * detail's frame: the trail reads "<task> › Steps" and ‹ goes back.
 *
 * Pages come from GET /api/tasks/{id}/steps at the server's page size; "Load
 * older steps" sends only the oldest loaded step's id as the cursor. Live
 * `task_step` rows for this task are upserted with no trim, and those that
 * land while the first page is in flight are held until it settles. A
 * `resync` (the socket came back) reloads the newest page and forgets older
 * ones, because the live steps sent while it was down are lost. Closing the
 * layer unsubscribes and drops any load still in flight.
 */
const BossModTaskStepsHistory = (() => {
    const { h } = BossModDom;

    const TITLE = 'Steps';
    const LOADING_COPY = 'Loading steps…';
    const EMPTY_COPY = 'No steps recorded yet.';
    const ERROR_COPY = 'Could not load steps.';
    const RETRY_LABEL = 'Try again';
    const OLDER_LABEL = 'Load older steps';

    /** A page's oldest step id, or `previous` when the page is empty. */
    function lastIdOf(page, previous) {
        return page.steps.length ? page.steps[page.steps.length - 1].id : previous;
    }

    /**
     * Open the step history for one task as a layer over whatever is on screen.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.bus  Event bus; `task_step` is read, and a `resync`
     *   reloads.
     * @param {{id: string}} deps.task  The task whose steps are listed.
     * @returns {{close: () => void}} Closes exactly this layer, which
     *   unsubscribes from both.
     * @throws {Error} When any dependency is missing.
     */
    function open(deps) {
        const { api, bus, task } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-steps-history] deps.api is required');
        if (!bus || typeof bus.subscribe !== 'function') {
            throw new Error('[task-steps-history] deps.bus is required');
        }
        if (!task || !task.id) throw new Error('[task-steps-history] deps.task is required');

        const STEPS = BossModTaskSteps;
        const url = `/api/tasks/${encodeURIComponent(task.id)}/steps`;
        const load = BossModGates.createLoadGeneration();
        const stateEl = h('div', { class: 'task-steps-state' });
        // Focus lands here when "Load older steps" goes away under the keyboard.
        const listEl = h('ol', { class: 'task-steps', tabindex: '-1' });
        const keyed = BossModDom.createKeyedList(listEl);
        // A failed older page keeps the rows; the button below retries it.
        const olderError = h('p', { class: 'place-error-detail', role: 'alert' });
        const olderBtn = h('button', {
            class: 'btn-link task-steps-older', type: 'button', onclick: () => { void loadOlder(); },
        }, OLDER_LABEL);
        const body = h('div', { class: 'task-detail' },
            h('div', { class: 'task-detail-column' }, stateEl, listEl, olderError, olderBtn));

        /** 'loading' | 'error' | 'ready' for the first page. */
        let status = 'loading';
        /** Every loaded and live step, newest first. */
        let steps = [];
        let hasMore = false;
        /** The oldest step a loaded page held: the next page's cursor. Not the
         *  list's last row, which a live upsert can place below it. */
        let cursor = null;
        let olderPending = false;
        let olderFailed = false;
        /** Live steps that landed while the first page was in flight. */
        let held = [];
        let closed = false;

        function render() {
            const listed = status === 'ready' && steps.length > 0;
            // Hidden rather than empty: the column's gap would still space an
            // empty slot.
            stateEl.hidden = listed;
            listEl.hidden = !listed;
            olderBtn.hidden = status !== 'ready' || !hasMore;
            olderBtn.disabled = olderPending;
            olderError.hidden = !olderFailed;
            olderError.textContent = olderFailed ? ERROR_COPY : '';
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
            STEPS.syncRows(keyed, steps);
        }

        /**
         * Load the newest page, forgetting older ones.
         * @returns {Promise<void>} Never rejects; a failure is the error state.
         */
        async function reload() {
            const loadId = load.next();
            status = 'loading';
            steps = [];
            hasMore = false;
            cursor = null;
            olderPending = false;
            olderFailed = false;
            held = [];
            render();
            let page;
            try {
                page = await STEPS.readPage(await api(url, { cache: 'no-store' }), { withLimit: false });
            } catch (err) {
                if (closed || !load.isCurrent(loadId)) return;
                console.error('[task-steps-history] could not load the steps', err);
                status = 'error';
                render();
                return;
            }
            if (closed || !load.isCurrent(loadId)) return;
            steps = page.steps.slice();
            hasMore = page.has_more;
            cursor = lastIdOf(page, cursor);
            status = 'ready';
            held.splice(0).forEach((step) => { steps = STEPS.upsert(steps, step); });
            render();
        }

        /**
         * Append the page before the oldest loaded step.
         * @returns {Promise<void>} Never rejects; a failure keeps the rows and
         *   says so above the button, which retries it.
         */
        async function loadOlder() {
            if (olderPending || status !== 'ready' || cursor === null) return;
            const loadId = load.next();
            const hadFocus = document.activeElement === olderBtn;
            const oldest = cursor;
            olderPending = true;
            render();
            let page;
            try {
                page = await STEPS.readPage(await api(`${url}?before=${encodeURIComponent(oldest)}`,
                    { cache: 'no-store' }), { withLimit: false });
            } catch (err) {
                if (closed || !load.isCurrent(loadId)) return;
                console.error('[task-steps-history] could not load older steps', err);
                olderPending = false;
                olderFailed = true;
                render();
                if (hadFocus) olderBtn.focus();
                return;
            }
            if (closed || !load.isCurrent(loadId)) return;
            page.steps.forEach((step) => { steps = STEPS.upsert(steps, step); });
            hasMore = page.has_more;
            cursor = lastIdOf(page, cursor);
            olderPending = false;
            olderFailed = false;
            render();
            // The button was disabled while the page loaded; give the keyboard
            // back to it, or to the list once there is nothing older.
            if (hadFocus) (hasMore ? olderBtn : listEl).focus();
        }

        const offStep = bus.subscribe('task_step', (step) => {
            STEPS.checkStep(step);
            if (step.task_id !== task.id) return;
            if (status === 'loading') {
                held.push(step);
                return;
            }
            if (status !== 'ready') return; // Try again reloads it.
            steps = STEPS.upsert(steps, step);
            render();
        });
        // The steps sent while the socket was down are gone; read page 1 again.
        const offResync = bus.subscribe('resync', () => { void reload(); });

        const panel = BossModOverlays.createModal({
            title: TITLE,
            body,
            size: 'panel',
            actions: [],
            onClose: () => {
                closed = true;
                load.next();
                offStep();
                offResync();
            },
        });

        void reload();

        return { close: panel.close };
    }

    return { open };
})();
