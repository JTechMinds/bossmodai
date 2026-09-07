/**
 * BossMod AI — the Board's column map.
 *
 * Exported as data, not as a switch: test_ui_board.py parses it and asserts it
 * is total in both directions against the TaskStatus literal in
 * core/models/task.py. That test is the point of this file. An earlier draft of
 * the spec listed ten of the eleven statuses; `delegated` mapped to no column,
 * so a delegated task would have sat in the database and rendered nowhere.
 * Adding a status to the engine now fails a test instead of dropping work off
 * the board.
 */
const BossModBoardColumns = (() => {

    /**
     * The four columns, left to right.
     *
     * `delegated` is Working: it is live work that has been handed to someone
     * else. The card carries a visible marker so the column does not imply the
     * owner is the one doing it. The engine's own constants do not settle this
     * — OPEN_TASK_STATUSES names six and TERMINAL_TASK_STATUSES four, and
     * `delegated` is in neither.
     */
    const COLUMNS = Object.freeze([
        { id: 'backlog', label: 'Backlog',   statuses: ['pending'] },
        { id: 'working', label: 'Working',   statuses: ['accepted', 'active', 'waiting', 'delegated'] },
        { id: 'needs',   label: 'Needs you', statuses: ['blocked', 'stalled'] },
        { id: 'done',    label: 'Done',      statuses: ['complete'] },
    ]);

    /**
     * NOT a column. Rendered under a <details> inside Done, because reporting a
     * cancelled task as done would misrepresent the outcome (spec 6.3) — the
     * same honesty rule ARCHIVE_HONESTY_COPY and doneClaimGuidance already
     * enforce elsewhere in this codebase.
     */
    const CLOSED_WITHOUT_COMPLETING = Object.freeze(['abandoned', 'declined', 'cancelled']);

    const CLOSED_LABEL = 'Closed without completing';

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
     * Derived from the Done column plus the disclosure rather than written out
     * again, so it cannot disagree with the map above. This is what
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
     * @returns {string|null} A column id, or null when the status belongs to
     *   the disclosure or is not a TaskStatus at all. Null is a real answer
     *   here — the caller must decide between the disclosure and an error, and
     *   this function does not have the context to.
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
        CLOSED_LABEL,
        TERMINAL_STATUSES,
        columnFor,
        isClosedWithoutCompleting,
        isTerminal,
    };
})();
