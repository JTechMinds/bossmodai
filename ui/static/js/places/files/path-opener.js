/**
 * BossMod AI — opening a workspace path in the app, as the agent it belongs
 * to sees it.
 *
 * A path an agent wrote down (a deliverable, a reference, a link in a chat
 * message or in a file) is opened with the path and the agent id it was
 * recorded against, never with a path rewritten to look like the operator's
 * own desk: `/me` means "the agent whose task this is", and remapping it
 * would open the wrong file, or nothing, without saying so.
 * test_js_company_task_detail.py has guarded that since it was a real bug.
 *
 * The one caller of BossModFileViewer.open (openFile). Links inside a viewed
 * markdown file open through openPath too, so the viewer is handed a
 * `linksFor` factory rather than naming this module: it loads right after
 * file-viewer.js, which never calls back into it, and the two never form a
 * cycle.
 */
const BossModPathOpener = (() => {

    /**
     * True for a path in an agent's own namespace: `/me` (its desk) and
     * `/projects` (its floor's folder). Both mean something only relative to
     * the agent that wrote them, so they resolve through that agent's desk,
     * never against the company root.
     *
     * @param {string} virtualPath  The path as the agent recorded it.
     * @returns {boolean}
     */
    function isAgentVirtualPath(virtualPath) {
        const text = String(virtualPath || '');
        return text === '/me' || text.startsWith('/me/')
            || text === '/projects' || text.startsWith('/projects/');
    }

    /** POST an open-folder request and throw with the server's reason on failure. */
    async function requestOpenFolder(api, url, init) {
        const res = await api(url, init);
        if (!res.ok) throw new Error(await res.text() || 'Could not open that folder');
    }

    /**
     * Open an agent-virtual path through the agent's desk. The desk resolves
     * it in the agent's namespace and, for a `/projects` path, returns the
     * file's `company_path`, so the full company viewer opens it (image
     * preview and Save both use company paths). A `/me` entry has no company
     * path and opens through the desk endpoint itself.
     *
     * @param {Function} api  Authenticated fetch helper.
     * @param {string} target  An agent-virtual path.
     * @param {string} agentId  The agent the path belongs to.
     * @returns {Promise<void>}
     * @throws {Error} With the server's message when the desk cannot resolve it.
     */
    async function openAgentVirtualPath(api, target, agentId) {
        const deskUrl = `/api/agents/${encodeURIComponent(agentId)}/desk?path=${encodeURIComponent(target)}`;
        const res = await api(deskUrl, { cache: 'no-store' });
        if (!res.ok) throw new Error(await res.text() || 'Could not open that path');
        const payload = await res.json();
        if (payload.kind === 'file') {
            if (payload.company_path) {
                await openFile(api, payload.company_path, '');
                return;
            }
            await openFile(api, target, agentId);
            return;
        }
        if (payload.company_path) {
            await requestOpenFolder(api, '/api/company/files/open-folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: payload.company_path }),
            });
            return;
        }
        await requestOpenFolder(
            api,
            `/api/agents/${encodeURIComponent(agentId)}/desk/open-folder?path=${encodeURIComponent(target)}`,
            { method: 'POST' },
        );
    }

    /**
     * Open one path: an agent file or folder, a host file, or a host folder.
     *
     * @param {Function} api  Authenticated fetch helper.
     * @param {string} path  The path exactly as the agent recorded it.
     * @param {string} agentId  The agent the path belongs to.
     * @returns {Promise<void>}
     * @throws {Error} When the path cannot be resolved, or when it is an
     *   agent-virtual path with no agent recorded. The caller marks its card
     *   or link failed and shows the reason; swallowing it would leave a
     *   control that silently does nothing when clicked.
     */
    async function openPath(api, path, agentId) {
        const target = String(path || '').trim();
        if (!target) throw new Error('That deliverable has no path.');
        if (isAgentVirtualPath(target)) {
            if (!agentId) throw new Error('That path belongs to an agent, but no agent is recorded for it.');
            await openAgentVirtualPath(api, target, agentId);
            return;
        }
        const res = await api(`/api/company/files?path=${encodeURIComponent(target)}`, { cache: 'no-store' });
        if (!res.ok) throw new Error(await res.text() || 'Could not open that path');
        const payload = await res.json();
        if (payload.kind === 'file') {
            await openFile(api, payload.path || target, '');
            return;
        }
        await requestOpenFolder(api, '/api/company/files/open-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: payload.path || target }),
        });
    }

    /**
     * The path a link inside a viewed file points at, or null to show it as
     * plain text. Pure.
     *
     * A relative link is joined to the viewed file's folder and normalised
     * the way the agent's CLI normalises a path (core/bm_cli/virtual_fs.py
     * normalize_cli_path): `.` is dropped, and `..` pops a segment but never
     * climbs above `/`. An absolute link is kept as written, except that in
     * the company namespace `/me` and `/projects` name nobody's desk — no
     * agent owns a company file — so those stay text. The server stays the
     * jail: whatever this returns is resolved and refused there, and a
     * refusal shows on the link.
     *
     * @param {string} filePath  The viewed file's path, as the server read it.
     * @param {string} linkPath  A decoded href path (BossModMarkdownLinks.linkTarget).
     * @param {boolean} inDesk  True when the file was read from an agent's
     *   desk namespace, where `/me` and `/projects` are that agent's.
     * @returns {string|null}
     */
    function linkPathFor(filePath, linkPath, inDesk) {
        const link = String(linkPath || '');
        if (link.startsWith('/')) {
            if (!inDesk && isAgentVirtualPath(link)) return null;
            return link;
        }
        const file = String(filePath || '');
        const folder = file.slice(0, file.lastIndexOf('/') + 1);
        const parts = [];
        for (const segment of `${folder}${link}`.split('/')) {
            if (segment === '' || segment === '.') continue;
            if (segment === '..') {
                parts.pop();
                continue;
            }
            parts.push(segment);
        }
        return `/${parts.join('/')}`;
    }

    /**
     * How links inside one viewed file open: placed beside it (linkPathFor)
     * and opened as the same agent sees them, so a `/me` file's links stay
     * in that agent's desk.
     *
     * @param {Function} api
     * @param {string} filePath  The viewed file's path, as the server read it.
     * @param {string} agentId  The desk namespace the file was read from, or
     *   '' for the company workspace.
     * @returns {MarkdownLinks}
     */
    function linksFor(api, filePath, agentId) {
        return {
            resolve: (p) => linkPathFor(filePath, p, Boolean(agentId)),
            open: (p) => openPath(api, p, agentId),
        };
    }

    /**
     * Show one file in the viewer: the single way any surface does.
     *
     * @param {Function} api  Authenticated fetch helper.
     * @param {string} path  The file's path in the namespace `agentId` names.
     * @param {string} agentId  An agent's desk namespace, or '' for the
     *   company workspace (BossModFileViewer.open).
     * @returns {Promise<void>}
     * @throws {Error} When the viewer cannot read the file; the caller says so.
     */
    async function openFile(api, path, agentId) {
        await BossModFileViewer.open(path, {
            api,
            agentId,
            linksFor: (filePath) => linksFor(api, filePath, agentId),
        });
    }

    return { isAgentVirtualPath, linkPathFor, openPath, openFile };
})();
