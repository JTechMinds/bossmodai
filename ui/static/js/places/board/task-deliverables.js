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

    /** `/me` and everything under it is the assignee's own desk, not the host. */
    function isAgentDeskPath(virtualPath) {
        return virtualPath === '/me' || String(virtualPath || '').startsWith('/me/');
    }

    /**
     * Open one deliverable: a desk file, a host file, or a host folder.
     *
     * @param {Function} api  Authenticated fetch helper.
     * @param {string} path  The path exactly as the agent recorded it.
     * @param {string} agentId  The agent the path belongs to.
     * @returns {Promise<void>}
     * @throws {Error} When the path cannot be resolved. The caller marks the
     *   card failed and shows the reason; swallowing it would leave a card that
     *   silently does nothing when clicked.
     */
    async function openDeliverablePath(api, path, agentId) {
        const target = String(path || '').trim();
        if (!target) throw new Error('That deliverable has no path.');
        if (isAgentDeskPath(target) && agentId) {
            await CompanyFileViewer.open(target, {
                apiUrl: `/api/agents/${encodeURIComponent(agentId)}/desk?path=${encodeURIComponent(target)}`,
            });
            return;
        }
        const res = await api(`/api/company/files?path=${encodeURIComponent(target)}`, { cache: 'no-store' });
        if (!res.ok) throw new Error(await res.text() || 'Could not open that path');
        const payload = await res.json();
        if (payload.kind === 'file') {
            await CompanyFileViewer.open(payload.path || target);
            return;
        }
        await api('/api/company/files/open-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: payload.path || target }),
        });
    }

    /**
     * One deliverable card.
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
            h('span', { class: 'task-detail-file-name' }, fileName),
            deliverable.description ? h('span', {}, deliverable.description) : null,
            h('span', { class: 'task-detail-file-path' }, path));
        return card;
    }

    return { isAgentDeskPath, openDeliverablePath, renderDeliverable };
})();
