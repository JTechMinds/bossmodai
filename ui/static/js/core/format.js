/**
 * BossMod AI — the shared formatters.
 *
 * One of the three modules utils.js became. utils.js was 418 lines exporting
 * 25 names across four unrelated concerns and was the last file in the tree
 * with no clear owner: a change to a date format and a change to the status
 * palette touched the same file for no reason other than history.
 *
 * These seven answer one question — how does a value read on screen. They have
 * no dependencies, no state, and no DOM beyond `escapeHtml`'s one scratch
 * node, which is why they load first among the three.
 */
const BossModFormat = (() => {

    /**
     * HTML-escape a string by round-tripping it through a text node.
     *
     * The settings modules build markup from template strings (spec 6.7) and
     * this is what keeps that safe; everywhere else builds nodes with the h()
     * helper in core/dom.js and never needs it.
     *
     * @param {string} text
     * @returns {string} '' for any falsy input.
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * How long ago, in the coarsest unit that still says something.
     *
     * @param {string} isoString
     * @returns {string} '' when the timestamp is missing or unparseable — the
     *   caller renders no time rather than a wrong one.
     */
    function formatRelativeTime(isoString) {
        if (!isoString) return '';
        const now = Date.now();
        const then = new Date(isoString).getTime();
        if (isNaN(then)) return '';
        const diffMs = now - then;
        if (diffMs < 0) return 'just now';
        const seconds = Math.floor(diffMs / 1000);
        if (seconds < 60) return 'just now';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        if (days < 30) return `${days}d ago`;
        const months = Math.floor(days / 30);
        if (months < 12) return `${months}mo ago`;
        return `${Math.floor(months / 12)}y ago`;
    }

    /** Short month names, so the output never depends on the host's locale data. */
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    /**
     * When something last happened, absolutely — `10:10 AM` today, `Sep 2`
     * earlier this year, `Sep 2, 2025` before that.
     *
     * Absolute rather than relative (the operator's decision): the rail is
     * scanned, and "3d ago" on eight rows is eight subtractions to do in your
     * head before you know which is the oldest. The year is carried on
     * anything older than this one, because without it last September and this
     * September render identically.
     *
     * Every comparison is on LOCAL calendar fields — getFullYear/getMonth/
     * getDate, not the UTC pair. A message at 23:30 tonight is today whatever
     * the offset; comparing UTC dates puts it on yesterday west of the
     * meridian and on tomorrow east of it, and the operator reads their own
     * clock.
     *
     * Formatted by hand rather than through toLocaleTimeString: the ICU data a
     * host ships decides whether the separator before AM is a space or U+202F,
     * and a rail that renders differently on two machines is not a format.
     *
     * @param {string|null} isoString
     * @returns {string} '' when the timestamp is missing or unparseable — the
     *   caller renders no time at all rather than a fabricated one.
     */
    function formatActivityTime(isoString) {
        if (!isoString) return '';
        const then = new Date(isoString);
        if (isNaN(then.getTime())) return '';
        const now = new Date();
        const sameYear = then.getFullYear() === now.getFullYear();
        if (sameYear
            && then.getMonth() === now.getMonth()
            && then.getDate() === now.getDate()) {
            const hour24 = then.getHours();
            const hour = hour24 % 12 === 0 ? 12 : hour24 % 12;
            const minute = String(then.getMinutes()).padStart(2, '0');
            return `${hour}:${minute} ${hour24 < 12 ? 'AM' : 'PM'}`;
        }
        const day = `${MONTHS[then.getMonth()]} ${then.getDate()}`;
        return sameYear ? day : `${day}, ${then.getFullYear()}`;
    }

    /**
     * A count, abbreviated past a thousand.
     *
     * @param {number|null} n
     * @returns {string} '0' when the value is missing or not a number.
     */
    function formatNumber(n) {
        if (n == null || isNaN(n)) return '0';
        const num = Number(n);
        if (num >= 1_000_000) return (num / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
        if (num >= 1_000) return (num / 1_000).toFixed(1).replace(/\.0$/, '') + 'K';
        return String(num);
    }

    /**
     * A duration in whole hours and minutes.
     *
     * Moved here verbatim from company-metrics.js in Phase 3B: Metrics'
     * uptime cell, the presence row's running turn, and any later duration
     * read the same rounding, rather than files disagreeing about what
     * "< 1m" means.
     *
     * @param {number|null} seconds
     * @returns {string} '--' when the value is missing or not a number.
     */
    function formatDuration(seconds) {
        if (seconds == null || isNaN(seconds) || seconds < 0) return '--';
        const s = Math.floor(Number(seconds));
        if (s < 60) return '< 1m';
        const hours = Math.floor(s / 3600);
        const minutes = Math.floor((s % 3600) / 60);
        if (hours > 0) return `${hours}h ${minutes}m`;
        return `${minutes}m`;
    }

    /**
     * A token count with its unit. Moved here verbatim from
     * company-metrics.js; note that a missing count is '0' with no unit,
     * which is the behaviour the Metrics bars have always shown.
     *
     * @param {number|null} n
     * @returns {string}
     */
    function formatTokenCount(n) {
        if (n == null || isNaN(n)) return '0';
        return formatNumber(n) + ' tokens';
    }

    /**
     * A byte count in the largest unit that keeps it readable.
     *
     * Moved here verbatim from company-files.js and company-file-viewer.js,
     * which carried one copy each: the browser list and the viewer header
     * showed the same file and had to agree about its size.
     *
     * @param {number|null} bytes
     * @returns {string} '' when the size is unknown, which callers render as
     *   no size column rather than as a zero.
     */
    function formatFileSize(bytes) {
        if (bytes == null || bytes < 0) return '';
        if (bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const exp = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        const val = bytes / Math.pow(1024, exp);
        return `${exp === 0 ? val : val.toFixed(1)} ${units[exp]}`;
    }

    return {
        escapeHtml,
        formatRelativeTime,
        formatActivityTime,
        formatNumber,
        formatDuration,
        formatTokenCount,
        formatFileSize,
    };
})();
