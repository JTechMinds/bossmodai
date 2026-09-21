/**
 * BossMod AI — the Tasks place's data layer.
 *
 * Fetching, filtering, sorting, grouping, counting. No DOM, no element, no
 * global: `api` is a parameter and everything else is a pure function of its
 * arguments. company-tasks.js interleaved all of this with rendering, which is
 * why none of it could be checked without a browser.
 */
const BossModTasksData = (() => {
    const COLUMNS = BossModTasksColumns;

    /**
     * Activity event names that can move a task between columns.
     *
     * There is no `task_blocked` event: an agent's block, complete, and
     * delegate actions all broadcast the generic `status_changed`
     * (core/agent_loop/actions_lifecycle.py), retry exhaustion broadcasts
     * `task_stalled` (dispatcher.py, watchdog.py), and the operator's own task
     * routes broadcast the `task_*` names (api/routes/tasks.py). Listening for
     * `task_*` alone would miss every agent-initiated block — the exact rows
     * the "Needs you" column exists to show.
     *
     * Every name here is asserted to still be emitted somewhere under core/ or
     * api/ by test_ui_tasks.py, so an engine rename fails a test instead of
     * quietly freezing the list.
     */
    const TASK_ACTIVITY_EVENTS = Object.freeze([
        'status_changed',
        'task_stalled',
        'task_created',
        'task_reused',
        'task_cancelled',
        'task_clarify',
    ]);

    const DAY_MS = 24 * 60 * 60 * 1000;

    /**
     * How far back Done reaches, and how each reach reads in the summary and
     * the empty column. Toolbar state like the rest: not persisted.
     */
    const DONE_WINDOWS = Object.freeze([
        Object.freeze({ days: 3, label: '3d', phrase: 'in the last 3 days' }),
        Object.freeze({ days: 7, label: '7d', phrase: 'this week' }),
        Object.freeze({ days: 14, label: '14d', phrase: 'in the last 2 weeks' }),
    ]);

    const DEFAULT_WINDOW_DAYS = 7;

    /**
     * Fetch every task.
     *
     * @param {Function} api  Authenticated fetch helper.
     * @returns {Promise<object[]>}
     * @throws {Error} On a failed request or a non-array body. The caller turns
     *   this into the error state; company-tasks.js swallowed a failed silent
     *   refresh and left the operator looking at stale rows.
     */
    async function loadTasks(api) {
        const res = await api('/api/tasks', { cache: 'no-store' });
        if (!res.ok) throw new Error(`GET /api/tasks failed: HTTP ${res.status}`);
        const rows = await res.json();
        if (!Array.isArray(rows)) throw new TypeError('GET /api/tasks did not return a list');
        return rows;
    }

    /**
     * The assignees present in a task list, for the agent filter.
     *
     * @param {object[]} tasks
     * @returns {Array<{id: string, name: string}>} Sorted by name.
     */
    function uniqueAgents(tasks) {
        const seen = new Map();
        for (const task of tasks) {
            if (task.assigned_to && task.assigned_to_name && !seen.has(task.assigned_to)) {
                seen.set(task.assigned_to, task.assigned_to_name);
            }
        }
        return Array.from(seen.entries())
            .map(([id, name]) => ({ id, name }))
            .sort((a, b) => a.name.localeCompare(b.name));
    }

    /**
     * How far along each parent's subtasks are.
     *
     * @param {object[]} tasks
     * @returns {Map<string, {done: number, total: number}>} Keyed by parent id;
     *   `done` counts `complete` children only — a cancelled child is over but
     *   was not done, the same rule the Done column keeps.
     */
    function subtaskProgress(tasks) {
        const progress = new Map();
        for (const task of tasks) {
            if (!task.parent_task_id) continue;
            const entry = progress.get(task.parent_task_id) || { done: 0, total: 0 };
            entry.total += 1;
            if (task.status === 'complete') entry.done += 1;
            progress.set(task.parent_task_id, entry);
        }
        return progress;
    }

    /**
     * The Done window for a day count.
     *
     * @param {number} days
     * @returns {{days: number, label: string, phrase: string}}
     * @throws {Error} On a count DONE_WINDOWS does not offer — a window the
     *   summary has no phrase for would print "undefined".
     */
    function windowFor(days) {
        const found = DONE_WINDOWS.find((entry) => entry.days === days);
        if (!found) throw new Error(`[tasks-data] no Done window of ${days} days`);
        return found;
    }

    /**
     * Narrow a task list to what the operator asked for.
     *
     * @param {object[]} tasks
     * @param {object} filters
     * @param {string|null} [filters.agentId]  Assignee filter; null means all.
     * @param {string} [filters.query]  Case-insensitive substring of the title.
     * @param {boolean} [filters.showChildren=false]  Subtasks are hidden by
     *   default so a parent's work is one card, not five.
     * @returns {object[]} A new array; the input is never mutated.
     */
    function filterTasks(tasks, filters) {
        const { agentId = null, query = '', showChildren = false } = filters || {};
        const needle = query.trim().toLowerCase();
        return tasks.filter((task) => {
            if (!showChildren && task.parent_task_id) return false;
            if (agentId && task.assigned_to !== agentId) return false;
            if (needle && !(task.title || '').toLowerCase().includes(needle)) return false;
            return true;
        });
    }

    /** Finished work is ordered by when it finished; open work by when it last moved. */
    function recencyOf(task) {
        const at = COLUMNS.isTerminal(task.status) ? task.closed_at : task.last_activity;
        return at ? new Date(at).getTime() : 0;
    }

    /**
     * Order a task list by recency.
     *
     * A finished task sorts by `closed_at`, not `last_activity`: a heartbeat
     * that lands after the finish bumps the latter, and must not move a task
     * that ended days ago back to the top of Done.
     *
     * @param {object[]} tasks
     * @param {'desc'|'asc'} direction  Newest first, or oldest first.
     * @returns {object[]} A new array; the input is never mutated.
     * @throws {Error} On any other direction.
     */
    function sortTasks(tasks, direction) {
        if (direction !== 'asc' && direction !== 'desc') {
            throw new Error(`[tasks-data] unknown sort direction "${direction}"`);
        }
        const sign = direction === 'asc' ? 1 : -1;
        return tasks.slice().sort((a, b) => sign * (recencyOf(a) - recencyOf(b)));
    }

    /**
     * Place every task in its column, the archive, or a report list.
     *
     * Finished tasks — complete and closed-without-completing alike — go to
     * Done when `closed_at` falls inside the window and to `older` (the
     * Archive) when it does not. Done holds both kinds; counts() keeps them
     * apart.
     *
     * @param {object[]} tasks  Already filtered and sorted; order is preserved.
     * @param {object} options
     * @param {number} options.windowDays  One of DONE_WINDOWS.
     * @param {number} options.now  Epoch ms the window is measured back from.
     * @returns {{columns: {backlog: object[], working: object[], needs: object[],
     *   done: object[]}, older: object[], unplaced: object[], undated: object[]}}
     *   `unplaced` holds tasks whose status the map does not know; `undated`
     *   holds finished tasks with no `closed_at`. Both are empty while the
     *   engine keeps its promises, and exist so a payload that breaks one is
     *   surfaced rather than dropped on the floor.
     * @throws {Error} On a window DONE_WINDOWS does not offer.
     */
    function groupIntoColumns(tasks, { windowDays, now }) {
        const cutoff = now - windowFor(windowDays).days * DAY_MS;
        const columns = {};
        COLUMNS.COLUMNS.forEach((column) => { columns[column.id] = []; });
        const older = [];
        const unplaced = [];
        // A finished task with no finish time broke the closed_at invariant
        // (db.update_task stamps every finish, the migration backfilled the
        // rest). Guessing one would file it under the wrong day, so it is
        // reported instead.
        const undated = [];
        for (const task of tasks) {
            if (COLUMNS.isTerminal(task.status)) {
                if (!task.closed_at) undated.push(task);
                else if (new Date(task.closed_at).getTime() >= cutoff) columns.done.push(task);
                else older.push(task);
                continue;
            }
            const columnId = COLUMNS.columnFor(task.status);
            if (columnId) columns[columnId].push(task);
            else unplaced.push(task);
        }
        return { columns, older, unplaced, undated };
    }

    /**
     * Totals for the summary line and the Done heading.
     *
     * `done` is `complete` only. A task that ended without being completed
     * sits in the Done column, marked, and is counted as `closed` — never
     * folded into done, which would report a cancelled task as finished work.
     *
     * @param {{columns: object, older: object[]}} grouped  From groupIntoColumns.
     * @returns {{open: number, done: number, closed: number, older: number}}
     */
    function counts(grouped) {
        const totals = { open: 0, done: 0, closed: 0, older: grouped.older.length };
        COLUMNS.COLUMNS.filter((column) => column.id !== 'done')
            .forEach((column) => { totals.open += grouped.columns[column.id].length; });
        for (const task of grouped.columns.done) {
            if (COLUMNS.isClosedWithoutCompleting(task.status)) totals.closed += 1;
            else totals.done += 1;
        }
        return totals;
    }

    /**
     * Where "Open chat" leads for a task.
     *
     * A task that came from a thread goes back to that thread; anything else
     * goes to its assignee's DM, which is where that agent can be asked.
     *
     * @param {object} task
     * @returns {{id: string, kind: 'thread'|'agent'}|null} null when the task
     *   has neither — there is no conversation to open, so no button.
     */
    function chatTargetFor(task) {
        if (task.source_channel === 'channel' && task.notification_channel_id) {
            return { id: task.notification_channel_id, kind: 'thread' };
        }
        if (task.assigned_to) return { id: task.assigned_to, kind: 'agent' };
        return null;
    }

    return {
        TASK_ACTIVITY_EVENTS, DONE_WINDOWS, DEFAULT_WINDOW_DAYS,
        loadTasks, uniqueAgents, subtaskProgress, windowFor,
        filterTasks, sortTasks, groupIntoColumns, counts, chatTargetFor,
    };
})();
