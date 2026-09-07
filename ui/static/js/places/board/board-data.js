/**
 * BossMod AI — the Board's data layer.
 *
 * Fetching, filtering, sorting, grouping, counting. No DOM, no element, no
 * global: `api` is a parameter and everything else is a pure function of its
 * arguments. company-tasks.js interleaved all of this with rendering, which is
 * why none of it could be checked without a browser.
 */
const BossModBoardData = (() => {
    const COLUMNS = BossModBoardColumns;

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
     * api/ by test_ui_board.py, so an engine rename fails a test instead of
     * quietly freezing the board.
     */
    const TASK_ACTIVITY_EVENTS = Object.freeze([
        'status_changed',
        'task_stalled',
        'task_created',
        'task_reused',
        'task_cancelled',
        'task_clarify',
    ]);

    /** Sort keys the board offers, in the order they appear in the control. */
    const SORT_KEYS = Object.freeze([
        { key: 'last_active', label: 'Updated' },
        { key: 'title', label: 'Title' },
        { key: 'assignee', label: 'Assignee' },
        { key: 'subtasks', label: 'Subtasks' },
    ]);

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
     * How many children each parent has.
     *
     * @param {object[]} tasks
     * @returns {Map<string, number>}
     */
    function subtaskCounts(tasks) {
        const counts = new Map();
        for (const task of tasks) {
            if (!task.parent_task_id) continue;
            counts.set(task.parent_task_id, (counts.get(task.parent_task_id) || 0) + 1);
        }
        return counts;
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

    /**
     * Order a task list.
     *
     * @param {object[]} tasks
     * @param {string} key  One of SORT_KEYS; anything else leaves the order be.
     * @param {string} direction  'asc' or 'desc'.
     * @param {Map<string, number>} [counts]  Subtask counts, required only for
     *   the 'subtasks' key.
     * @returns {object[]} A new array; the input is never mutated.
     */
    function sortTasks(tasks, key, direction, counts) {
        const rows = tasks.slice();
        const known = SORT_KEYS.some((entry) => entry.key === key);
        if (!known) return rows;
        const value = (task) => {
            if (key === 'title') return (task.title || '').toLowerCase();
            if (key === 'assignee') return (task.assigned_to_name || '').toLowerCase();
            if (key === 'subtasks') return (counts && counts.get(task.id)) || 0;
            return task.last_activity ? new Date(task.last_activity).getTime() : 0;
        };
        rows.sort((a, b) => {
            const va = value(a);
            const vb = value(b);
            const cmp = va < vb ? -1 : va > vb ? 1 : 0;
            return direction === 'asc' ? cmp : -cmp;
        });
        return rows;
    }

    /**
     * Place every task in its column, the disclosure, or the unplaced list.
     *
     * @param {object[]} tasks  Already filtered and sorted; order is preserved.
     * @returns {{columns: object, closed: object[], unplaced: object[]}}
     *   `unplaced` holds tasks whose status the map does not know. It is always
     *   empty while the totality test passes, and it exists so a payload that
     *   breaks that promise is surfaced rather than dropped on the floor.
     */
    function groupIntoColumns(tasks) {
        const columns = {};
        COLUMNS.COLUMNS.forEach((column) => { columns[column.id] = []; });
        const closed = [];
        const unplaced = [];
        for (const task of tasks) {
            const columnId = COLUMNS.columnFor(task.status);
            if (columnId) columns[columnId].push(task);
            else if (COLUMNS.isClosedWithoutCompleting(task.status)) closed.push(task);
            else unplaced.push(task);
        }
        return { columns, closed, unplaced };
    }

    /**
     * Outcome totals for the summary line.
     *
     * `done` is `complete` only. Tasks that ended without being completed are
     * counted separately, never folded into done — the same reason they render
     * under a disclosure rather than in the column.
     *
     * @param {object[]} tasks
     * @returns {{total: number, open: number, done: number, closed: number}}
     */
    function counts(tasks) {
        const totals = { total: tasks.length, open: 0, done: 0, closed: 0 };
        for (const task of tasks) {
            if (COLUMNS.isClosedWithoutCompleting(task.status)) totals.closed += 1;
            else if (COLUMNS.isTerminal(task.status)) totals.done += 1;
            else totals.open += 1;
        }
        return totals;
    }

    return {
        TASK_ACTIVITY_EVENTS, SORT_KEYS,
        loadTasks, uniqueAgents, subtaskCounts,
        filterTasks, sortTasks, groupIntoColumns, counts,
    };
})();
