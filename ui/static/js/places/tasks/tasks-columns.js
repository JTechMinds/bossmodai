/**
 * BossMod AI — the Tasks place's column map.
 *
 * Exported as data, not as a switch: test_ui_tasks.py parses it and asserts it
 * is total in both directions against the TaskStatus literal in
 * core/models/task.py. That test is the point of this file. An earlier draft of
 * the spec listed ten of the eleven statuses; `delegated` mapped to no column,
 * so a delegated task would have sat in the database and rendered nowhere.
 * Adding a status to the engine now fails a test instead of dropping work off
 * the list.
 */
const BossModTasksColumns = (() => {

    /**
     * The four columns, left to right.
     *
     * `delegated` is Working: it is live work that has been handed to someone
     * else. The card carries a visible marker so the column does not imply the
     * owner is the one doing it. The engine's own constants do not settle this
     * — OPEN_TASK_STATUSES names six and TERMINAL_TASK_STATUSES four, and
     * `delegated` is in neither.
     *
     * `empty` is each column's own copy for when it holds nothing. Done's is
     * a stem: the grid appends the Done window's phrase, because "nothing
     * finished" is only true of the window the column is showing.
     */
    const COLUMNS = Object.freeze([
        { id: 'backlog', label: 'Backlog',   statuses: ['pending'],
            empty: 'Nothing in the backlog' },
        { id: 'working', label: 'Working',   statuses: ['accepted', 'active', 'waiting', 'delegated'],
            empty: 'Nobody is working right now' },
        { id: 'needs',   label: 'Needs you', statuses: ['blocked', 'stalled'],
            empty: 'Nothing needs you' },
        { id: 'done',    label: 'Done',      statuses: ['complete'],
            empty: 'Nothing finished' },
    ]);

    /**
     * NOT a column's status. These render inside Done, because they are
     * finished work, but marked with CLOSED_NOTE and never counted as done:
     * reporting a cancelled task as done would misrepresent the outcome (spec
     * 6.3) — the same honesty rule ARCHIVE_HONESTY_COPY and doneClaimGuidance
     * already enforce elsewhere in this codebase.
     */
    const CLOSED_WITHOUT_COMPLETING = Object.freeze(['abandoned', 'declined', 'cancelled']);

    /**
     * The words a closed-without-completing task is marked with, wherever it
     * is shown. It is never counted as done; this phrase is what says so on
     * the card itself.
     */
    const CLOSED_NOTE = 'closed without completing';

    /**
     * Every status, as a word an operator reads — on the Needs card's line and
     * the detail's pill. Total over TaskStatus: test_ui_tasks.py asserts the
     * key set equals the engine's, so a new status fails a test instead of
     * rendering as its raw id.
     */
    const STATUS_LABELS = Object.freeze({
        pending: 'Pending',
        accepted: 'Accepted',
        active: 'Active',
        waiting: 'Waiting',
        blocked: 'Blocked',
        complete: 'Complete',
        stalled: 'Stalled',
        abandoned: 'Abandoned',
        delegated: 'Delegated',
        declined: 'Declined',
        cancelled: 'Cancelled',
    });

    /**
     * What moving INTO each status reads as in the activity timeline — "Jim
     * marked it blocked". Total over TaskStatus for the same reason as the
     * labels: a status with no verb would render a sentence with a hole in it.
     */
    const STATUS_VERBS = Object.freeze({
        pending: 'moved it to the backlog',
        accepted: 'accepted it',
        active: 'started work',
        waiting: 'is waiting',
        blocked: 'marked it blocked',
        complete: 'completed it',
        stalled: 'marked it stalled',
        abandoned: 'abandoned it',
        delegated: 'delegated it',
        declined: 'declined it',
        cancelled: 'cancelled it',
    });

    /** The column a status renders in, by status. Built once, read everywhere. */
    const BY_STATUS = new Map();
    COLUMNS.forEach((column) => {
        Object.freeze(column.statuses);
        Object.freeze(column);
        column.statuses.forEach((status) => BY_STATUS.set(status, column.id));
    });

    /**
     * Every status that means the task is over, whatever the outcome.
     *
     * Derived from the Done column plus CLOSED_WITHOUT_COMPLETING rather than
     * written out again, so it cannot disagree with the map above. This is what
     * company-tasks.js called TERMINAL_STATUSES, and it is what decides whether
     * a task can still be cancelled.
     */
    const TERMINAL_STATUSES = Object.freeze(new Set([
        ...COLUMNS[COLUMNS.length - 1].statuses,
        ...CLOSED_WITHOUT_COMPLETING,
    ]));

    /**
     * Which column a task belongs in.
     *
     * @param {string} status
     * @returns {string|null} A column id, or null when the status is closed
     *   without completing or is not a TaskStatus at all. Null is a real
     *   answer here — the caller must decide between "finished" and an error,
     *   and this function does not have the context to.
     */
    function columnFor(status) {
        const found = BY_STATUS.get(status);
        return found === undefined ? null : found;
    }

    /**
     * @param {string} status
     * @returns {boolean} Whether the task ended without being completed.
     */
    function isClosedWithoutCompleting(status) {
        return CLOSED_WITHOUT_COMPLETING.indexOf(status) !== -1;
    }

    /**
     * @param {string} status
     * @returns {boolean} Whether the task is over and can no longer be cancelled.
     */
    function isTerminal(status) {
        return TERMINAL_STATUSES.has(status);
    }

    return {
        COLUMNS,
        CLOSED_WITHOUT_COMPLETING,
        CLOSED_NOTE,
        STATUS_LABELS,
        STATUS_VERBS,
        TERMINAL_STATUSES,
        columnFor,
        isClosedWithoutCompleting,
        isTerminal,
    };
})();
