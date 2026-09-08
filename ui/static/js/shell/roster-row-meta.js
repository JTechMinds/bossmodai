/**
 * BossMod AI — the right-hand column of a roster row.
 *
 * Both halves of the rail grew the same column at the same time: when the
 * conversation was last spoken in, over the need dot. Two copies of five lines
 * of markup is how the People list and the Threads list end up with right
 * edges that do not line up, which is the defect the shared avatar was
 * extracted to fix on the other side of the row.
 *
 * It is a column rather than a row because both things want the same edge. The
 * dot used to sit inside the name button, where it pushed the status line
 * around; putting the timestamp beside it would have pushed it further.
 *
 * The whole column is ABSENT when there is neither a timestamp nor a need, so
 * a rail of quiet rows keeps its rhythm instead of carrying a run of empty
 * boxes. A conversation nobody has spoken in shows nothing at all — a
 * fabricated date would be worse than a blank.
 */
const BossModRosterRowMeta = (() => {
    const { h } = BossModDom;

    /**
     * Build the column, or nothing.
     *
     * @param {string|null} isoTimestamp  When this conversation was last
     *   spoken in. Missing or unparseable renders no time (core/format.js).
     * @param {boolean} needsYou  Whether this row has an open need. Threads
     *   never do; the dot is a person-row affordance and this is where the two
     *   lists differ.
     * @returns {HTMLElement|null} null when the row has neither, so h() drops
     *   it rather than leaving an empty box in the flex row.
     */
    function rowMeta(isoTimestamp, needsYou) {
        const lastAt = BossModFormat.formatActivityTime(isoTimestamp);
        if (!lastAt && !needsYou) return null;
        return h('span', { class: 'roster-row-meta' },
            lastAt ? h('span', { class: 'roster-time' }, lastAt) : null,
            // Decorative: the status line under the name already says
            // "Needs you", so announcing it twice would be noise.
            needsYou ? h('span', { class: 'roster-need-dot', 'aria-hidden': 'true' }) : null);
    }

    return { rowMeta };
})();
