/**
 * BossMod AI — a task's deliverable cards, and opening what they point at.
 *
 * A deliverable is a path an agent wrote down. It is opened with the path and
 * the agent id it was recorded against, never with a path rewritten to look
 * like the operator's own desk: `/me` means "the agent whose task this is", and
 * remapping it would open the wrong file, or nothing, without saying so.
 * test_js_company_task_detail.py has guarded that since it was a real bug.
 */
const BossModTaskDeliverables = (() => {
    const { h } = BossModDom;

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
                await BossModFileViewer.open(payload.company_path, { api });
                return;
            }
            await BossModFileViewer.open(target, { api, apiUrl: deskUrl });
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
     * Open one deliverable: an agent file or folder, a host file, or a host folder.
     *
     * @param {Function} api  Authenticated fetch helper.
     * @param {string} path  The path exactly as the agent recorded it.
     * @param {string} agentId  The agent the path belongs to.
     * @returns {Promise<void>}
     * @throws {Error} When the path cannot be resolved, or when it is an
     *   agent-virtual path with no agent recorded. The caller marks the card
     *   failed and shows the reason; swallowing it would leave a card that
     *   silently does nothing when clicked.
     */
    async function openDeliverablePath(api, path, agentId) {
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
            await BossModFileViewer.open(payload.path || target, { api });
            return;
        }
        await requestOpenFolder(api, '/api/company/files/open-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: payload.path || target }),
        });
    }

    /**
     * One deliverable row: a file glyph, the file's name, what it is, and the
     * full path it was recorded under.
     *
     * @param {object} deliverable  `{path, description}`.
     * @param {object} task  The task the deliverable belongs to — its assignee
     *   is the agent id the path is resolved against.
     * @param {Function} api
     * @returns {HTMLElement}
     */
    function renderDeliverable(deliverable, task, api) {
        const path = deliverable.path || '';
        const fileName = path.split('/').pop() || path;
        const agentId = task && task.assigned_to ? task.assigned_to : '';
        const card = h('button', {
            class: 'task-detail-file',
            type: 'button',
            'data-path': path,
            'data-agent-id': agentId,
            onclick: async () => {
                card.classList.remove('is-failed');
                card.removeAttribute('title');
                try {
                    await openDeliverablePath(api, path, agentId);
                } catch (err) {
                    card.classList.add('is-failed');
                    card.setAttribute('title', (err && err.message) || 'Could not open that path');
                }
            },
        },
            h('i', { 'data-lucide': 'file-text', 'aria-hidden': 'true' }),
            h('span', { class: 'task-detail-file-name' }, fileName),
            deliverable.description
                ? h('span', { class: 'task-detail-file-desc' }, deliverable.description)
                : null,
            h('span', { class: 'task-detail-file-path' }, path));
        return card;
    }

    return { isAgentVirtualPath, openDeliverablePath, renderDeliverable };
})();
