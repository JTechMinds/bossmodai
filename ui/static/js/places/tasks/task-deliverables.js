/**
 * BossMod AI — a task's deliverable and reference cards.
 *
 * A card shows a path an agent wrote down and opens it with the path and the
 * agent id it was recorded against, through places/files/path-opener.js,
 * which owns why that pairing matters.
 */
const BossModTaskDeliverables = (() => {
    const { h } = BossModDom;

    /**
     * One path card: a file glyph, the file's name, what it is, and the full
     * path it was recorded under. Clicking opens it through `agentId`'s desk
     * (BossModPathOpener.openPath); a failure marks the card and puts the
     * reason in its tooltip. Deliverables and task references both render as
     * this.
     *
     * @param {object} entry  `{path, description}`.
     * @param {string} agentId  The agent the path resolves against; empty
     *   when none is recorded, which a click on an agent path reports.
     * @param {Function} api
     * @returns {HTMLElement}
     */
    function renderPathCard(entry, agentId, api) {
        const path = entry.path || '';
        const fileName = path.split('/').pop() || path;
        const card = h('button', {
            class: 'task-detail-file',
            type: 'button',
            'data-path': path,
            'data-agent-id': agentId,
            onclick: async () => {
                card.classList.remove('is-failed');
                card.removeAttribute('title');
                try {
                    await BossModPathOpener.openPath(api, path, agentId);
                } catch (err) {
                    card.classList.add('is-failed');
                    card.setAttribute('title', (err && err.message) || 'Could not open that path');
                }
            },
        },
            h('i', { 'data-lucide': 'file-text', 'aria-hidden': 'true' }),
            h('span', { class: 'task-detail-file-name' }, fileName),
            entry.description
                ? h('span', { class: 'task-detail-file-desc' }, entry.description)
                : null,
            h('span', { class: 'task-detail-file-path' }, path));
        return card;
    }

    /**
     * One deliverable row, opened as the task's assignee sees the path.
     *
     * @param {object} deliverable  `{path, description}`.
     * @param {object} task  The task the deliverable belongs to — its assignee
     *   is the agent id the path is resolved against.
     * @param {Function} api
     * @returns {HTMLElement}
     */
    function renderDeliverable(deliverable, task, api) {
        return renderPathCard(deliverable, task && task.assigned_to ? task.assigned_to : '', api);
    }

    return { renderPathCard, renderDeliverable };
})();
