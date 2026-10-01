/**
 * BossMod AI — the presentation of one schedule, for its layer
 * (context/schedule-layer.js): the facts block (Repeats, Next run, Last run
 * with a link to the task a run created, Notify), the callout the layer's
 * refusals use, the Notify choices its edit dropdown offers, the head's
 * `⋯` menu and delete confirmation the layer opens, and the Run now control.
 *
 * Pure DOM with no schedule state, every action a callback — except Run now,
 * which owns its one request (BossModScheduleApi.runNow) and its result
 * callout, so the layer stays under its line cap.
 */
const BossModScheduleView = (() => {
    const { h } = BossModDom;

    /** Notify choices; the first is the server's default (ScheduleCreate). */
    const NOTIFY = Object.freeze([
        { value: 'completion_blocked', label: 'Done & blocked' },
        { value: 'all', label: 'Every update' },
        { value: 'none', label: 'Don’t notify' },
    ]);

    /** A titled `.callout` in one of the shared tones. */
    function callout(tone, title, text) {
        return h('div', { class: 'callout', 'data-tone': tone },
            h('p', { class: 'callout-title' }, title),
            text ? h('p', { class: 'callout-body' }, text) : null);
    }

    /**
     * The Last run fact: what happened, when, and a link to the task a run created.
     * @param {object} schedule  A ScheduleView.
     * @param {(taskId: string) => void} onOpenTask
     * @returns {HTMLElement}
     * @throws {Error} On an outcome this module has no sentence for.
     */
    function lastRun(schedule, onOpenTask) {
        const when = BossModFormat.formatDateTime(schedule.last_occurrence_at);
        const lines = {
            fired: `Ran ${when}`,
            missed: `Missed ${when}: the computer was asleep or BossMod was not running`,
            skipped_open: `Skipped ${when}: the last run was still open`,
            skipped_vacation: `Skipped ${when}: the agent was on vacation`,
            failed: `Failed ${when}: ${schedule.last_outcome_detail || 'no detail was recorded'}`,
        };
        const text = schedule.last_outcome ? lines[schedule.last_outcome] : 'Not run yet';
        if (text === undefined) throw new Error(`[schedule-view] unknown outcome "${schedule.last_outcome}"`);
        const status = schedule.last_task_status;
        // A skip points at the run still holding the schedule up, so the
        // operator can reach it; last_task_status is null when that task is
        // gone from the board, and then there is nothing to open.
        return h('span', { class: 'schedule-last-run' }, text,
            schedule.last_task_id && status ? h('button', {
                class: 'btn-link schedule-open-task', type: 'button',
                onclick: () => onOpenTask(schedule.last_task_id),
            }, `Open task (${BossModTasksColumns.STATUS_LABELS[status] || status})`) : null);
    }

    /**
     * The facts block: Repeats (the server's summary), Next run (or Off),
     * Last run, and Notify.
     *
     * @param {object} schedule  A ScheduleView.
     * @param {{onOpenTask: (taskId: string) => void}} options
     * @returns {HTMLElement} `dl.fact-list`.
     * @throws {Error} When onOpenTask is missing.
     */
    function facts(schedule, options) {
        const { onOpenTask } = options || {};
        if (typeof onOpenTask !== 'function') throw new Error('[schedule-view] onOpenTask is required');
        const policy = NOTIFY.find((item) => item.value === schedule.notification_policy);
        return BossModFactList.create([
            { label: 'Repeats', value: String(schedule.summary) },
            { label: 'Next run', value: schedule.next_run_at ? BossModFormat.formatDateTime(schedule.next_run_at) : 'Off' },
            { label: 'Last run', value: lastRun(schedule, onOpenTask) },
            { label: 'Notify', value: policy ? policy.label : String(schedule.notification_policy) },
        ]);
    }

    /**
     * The head's `⋯` menu with its one row, Delete schedule…, marked danger.
     *
     * @param {{anchor: HTMLElement, label: string, onDelete: () => void,
     *   onClose: () => void}} options  `anchor` is the `⋯`; the menu hangs
     *   in its modal head. Picking the row closes the menu, then calls onDelete.
     * @returns {{close: () => void, element: HTMLElement}} core/menu.js's menu.
     */
    function optionsMenu(options) {
        const { anchor, label, onDelete, onClose } = options || {};
        if (!anchor || typeof onDelete !== 'function' || typeof onClose !== 'function') {
            throw new Error('[schedule-view] optionsMenu needs anchor, onDelete and onClose');
        }
        const menu = BossModMenu.createMenu({
            anchor, label, container: anchor.closest('.modal-head'),
            items: [h('div', { class: 'menu-actions' },
                h('button', {
                    class: 'menu-action', id: 'schedule-delete', type: 'button', 'data-tone': 'danger',
                    onclick: () => { menu.close(); onDelete(); },
                }, h('i', { 'data-lucide': 'trash-2', 'aria-hidden': 'true' }), 'Delete schedule…'))],
            onClose: () => { anchor.setAttribute('aria-expanded', 'false'); onClose(); },
        });
        anchor.setAttribute('aria-expanded', 'true');
        BossModIcons.paint(menu.element, 'schedule-view');
        return menu;
    }

    /**
     * The danger confirmation before a delete (context/desk-actions.js's
     * pattern): nothing is called until the operator confirms, and Cancel,
     * last, is the focused default.
     *
     * @param {{title: string, agentName: string, onConfirm: () => void}} options
     * @returns {void}
     */
    function confirmDelete(options) {
        const { title, agentName, onConfirm } = options || {};
        if (typeof onConfirm !== 'function') throw new Error('[schedule-view] confirmDelete needs onConfirm');
        BossModOverlays.createModal({
            title: 'Delete this schedule?',
            closeOnBackdrop: true,
            body: `“${title}” stops running${agentName ? ` for ${agentName}` : ''}. `
                + 'Tasks its past runs created stay on the board.',
            actions: [
                { label: 'Delete schedule', tone: 'danger', onSelect: onConfirm },
                { label: 'Cancel', tone: 'quiet' },
            ],
        });
    }

    /**
     * "Run now": run the saved schedule once, now, enabled or not.
     *
     * While the request is in flight the button is disabled and reads
     * "Starting…". Success shows "Run started" with Open task and hands the
     * stored schedule to `onRan`; a refusal (409) shows the server's sentence,
     * with Open task when the last run is still open; any other failure shows
     * its sentence. Built once per layer, so its result survives the facts
     * repainting.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {() => string} deps.scheduleId  The saved schedule's id, read at click time.
     * @param {(taskId: string) => void} deps.onOpenTask
     * @param {(schedule: object) => void} deps.onRan  The run started; the schedule as stored now.
     * @returns {{element: HTMLElement, destroy: () => void}} `destroy`
     *   drops an answer still in flight.
     * @throws {Error} When a dependency is missing.
     */
    function runNowControl(deps) {
        const { api, scheduleId, onOpenTask, onRan } = deps || {};
        if (typeof api !== 'function' || typeof scheduleId !== 'function'
            || typeof onOpenTask !== 'function' || typeof onRan !== 'function') {
            throw new Error('[schedule-view] runNowControl needs api, scheduleId, onOpenTask and onRan');
        }
        const LABEL = 'Run now';
        const text = h('span', {}, LABEL);
        const button = h('button', {
            class: 'btn btn-sm', id: 'schedule-run-now', type: 'button', onclick: () => { void run(); },
        }, h('i', { 'data-lucide': 'play', 'aria-hidden': 'true' }), text);
        const result = h('div', { class: 'schedule-run-result', role: 'status' });
        let destroyed = false;
        const openTask = (taskId) => h('div', { class: 'callout-actions' },
            h('button', { class: 'btn btn-sm schedule-run-open-task', type: 'button', onclick: () => onOpenTask(taskId) },
                'Open task'));

        async function run() {
            button.disabled = true;
            text.textContent = 'Starting…';
            result.replaceChildren();
            let answer;
            try {
                answer = await BossModScheduleApi.runNow(api, scheduleId());
            } catch (err) {
                console.error('[schedule-view] Run now failed', err);
                if (destroyed) return;
                const box = callout('alert', 'Could not run the schedule', (err && err.message) || 'The request failed.');
                if (err && err.reason === 'open' && err.taskId) box.append(openTask(err.taskId));
                result.append(box);
                return;
            } finally {
                if (!destroyed) {
                    button.disabled = false;
                    text.textContent = LABEL;
                }
            }
            if (destroyed) return;
            const box = callout('ok', 'Run started');
            box.append(openTask(answer.task.id));
            result.append(box);
            onRan(answer.schedule);
        }

        BossModIcons.paint(button, 'schedule-view');
        return {
            element: h('div', { class: 'schedule-run-now' }, button, result),
            destroy() { destroyed = true; },
        };
    }

    return { NOTIFY, callout, lastRun, facts, optionsMenu, confirmDelete, runNowControl };
})();
