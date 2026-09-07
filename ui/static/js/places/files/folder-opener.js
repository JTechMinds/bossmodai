/**
 * BossMod AI — "open this in the file manager", for the company workspace.
 *
 * The 409 → ask which file manager → save the setting → retry once policy is
 * NOT reimplemented here. context/desk-opener.js already owned it for the desk
 * browser, and two copies of a settings-writing prompt is precisely the
 * duplication this phase exists to delete; that module now takes the request by
 * injection and this file supplies the company-workspace one.
 *
 * What is genuinely local: the route, the parent-directory rule for a file
 * entry (you reveal the folder a file sits in, not the file), and the fact that
 * this surface shows the server's own refusal rather than one fixed sentence —
 * "outside the allowed workspace roots" is information the operator needs.
 */
const BossModFolderOpener = (() => {

    const FAILURE_COPY = 'Failed to open folder';

    /**
     * The directory an entry should be revealed in.
     *
     * @param {string} path
     * @param {boolean} isDir
     * @returns {string} `path` for a folder; its parent for a file. Ported
     *   verbatim from company-files.js's context-menu handler.
     */
    function revealTarget(path, isDir) {
        if (isDir) return path;
        return String(path || '').replace(/\/[^/]+$/, '') || '/';
    }

    /**
     * Ask the host to open a company-workspace folder.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper, from ctx.
     * @param {string} deps.path
     * @param {(message: string) => void} deps.onError  Where the failure is
     *   shown. Never only the console: company-files.js's original bug was an
     *   open that quietly did nothing.
     * @returns {Promise<boolean>} Whether the folder was opened.
     * @throws {Error} When api or onError is missing.
     */
    function openFolder({ api, path, onError }) {
        if (typeof api !== 'function') throw new Error('[folder-opener] deps.api is required');
        if (typeof onError !== 'function') throw new Error('[folder-opener] deps.onError is required');
        return BossModDeskOpener.reveal({
            api,
            request: () => api('/api/company/files/open-folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: path || '/' }),
            }),
            describeFailure: async (res) => (await BossModFileOps.readApiError(res)) || FAILURE_COPY,
            onError,
        });
    }

    return { openFolder, revealTarget, FAILURE_COPY };
})();
