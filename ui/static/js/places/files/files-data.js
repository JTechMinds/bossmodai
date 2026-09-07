/**
 * BossMod AI — the Files place's reads.
 *
 * The two GETs the browser makes and the one path rule it needs, with no state
 * and no DOM. Separated the way board-data.js is: the place owns the load
 * generation and what gets painted, this owns what a request is and what a
 * failure says.
 *
 * Every function takes `api` — the authenticated helper from ctx — and throws
 * with the server's own message rather than a generic one, because "outside the
 * allowed workspace roots" is the sentence the operator needs to read.
 */
const BossModFilesData = (() => {

    /**
     * The folder a path sits in.
     *
     * Ported verbatim from company-files.js: opening a file leaves the browser
     * on its parent, which is where the operator is going next. Backslashes are
     * normalised because a host root on Windows can produce them.
     *
     * @param {string} path
     * @returns {string}
     */
    function parentVirtualPath(path) {
        const cleaned = String(path || '/').replace(/\\/g, '/');
        if (!cleaned || cleaned === '/') return '/';
        const trimmed = cleaned.endsWith('/') ? cleaned.slice(0, -1) : cleaned;
        const index = trimmed.lastIndexOf('/');
        return index <= 0 ? '/' : (trimmed.slice(0, index) || '/');
    }

    /**
     * @param {string} path
     * @returns {string}
     */
    function directoryUrl(path) {
        return `/api/company/files?path=${encodeURIComponent(path)}`;
    }

    /**
     * Read one path.
     *
     * @param {Function} api
     * @param {string} path
     * @returns {Promise<object>} The payload, whose `kind` says whether this
     *   was a directory or a file. The client never guesses from the name.
     * @throws {Error} Carrying the server's message.
     */
    async function loadPath(api, path) {
        const res = await api(directoryUrl(path), { cache: 'no-store' });
        if (!res.ok) throw new Error(await BossModFileOps.readApiError(res));
        return res.json();
    }

    /**
     * Search the whole workspace.
     *
     * @param {Function} api
     * @param {string} query
     * @returns {Promise<object[]>}
     * @throws {Error} Carrying the server's message, or when the response is
     *   not a list — a shape change must fail loudly, not paint nothing.
     */
    async function search(api, query) {
        const res = await api(
            `/api/company/files/search?q=${encodeURIComponent(query)}`, { cache: 'no-store' });
        if (!res.ok) throw new Error(await BossModFileOps.readApiError(res));
        const rows = await res.json();
        if (!Array.isArray(rows)) throw new Error('The search did not return a list.');
        return rows;
    }

    /**
     * Read a directory payload into the shape the place holds.
     *
     * @param {object} payload
     * @param {string} requestedPath
     * @returns {{entries: object[], crumbs: object[], note: string,
     *            roots: string[], path: string}}
     */
    function toListing(payload, requestedPath) {
        return {
            entries: Array.isArray(payload.entries) ? payload.entries : [],
            crumbs: Array.isArray(payload.breadcrumbs) ? payload.breadcrumbs : [],
            note: typeof payload.workspace_note === 'string' ? payload.workspace_note : '',
            roots: Array.isArray(payload.host_roots) ? payload.host_roots : [],
            path: payload.path || requestedPath || '/',
        };
    }

    return { parentVirtualPath, directoryUrl, loadPath, search, toListing };
})();
