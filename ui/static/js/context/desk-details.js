/**
 * BossMod AI — the desk's Details section: the agent's workspace and model,
 * and the line the desk head's `⋯` reports a failure into.
 *
 * It is also the desk's record of the agent — `GET /api/agents/{id}` — which
 * the head's agent menu (context/agent-menu.js) reads for its approval
 * switches and gives back the server's answer to after a flag save
 * (`adopt`). The menu's operations live there and throw; the desk's `⋯`
 * (context/desk-menu.js) catches them into `showError`, so the section is
 * where a desk-side failure is said.
 *
 * It renders NO BUTTONS. The desk used to end in four text links — Edit role,
 * Diagnostics, Reset runtime, Remove — at the bottom of a long scroll, two of
 * them red, in the reading flow. In every other modal those live in the head,
 * the destructive ones behind its `⋯`; what this renders is the quiet facts
 * and the error line.
 */
const BossModDeskDetails = (() => {
    const { h, clear } = BossModDom;

    /**
     * Build the Details section and the desk's record of the agent.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string}   deps.agentId
     * @returns {{ element: HTMLElement, refresh: () => Promise<void>,
     *   agent: () => (object|null), adopt: (agent: object) => void,
     *   showError: (message: string) => void, destroy: () => void }}
     *   `element` is the facts and the error line. `agent` is the detail the
     *   last read (or adopt) answered, or null until one has. `adopt` takes a
     *   newer answer — a flag save's — clears the error line and repaints.
     *   `showError` replaces the error line's text.
     * @throws {Error} When any dependency is missing.
     */
    function createDeskDetails(deps) {
        const { api, agentId } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-details] deps.api is required');
        if (!agentId) throw new Error('[desk-details] deps.agentId is required');

        const detailLoad = BossModGates.createLoadGeneration();
        const metaEl = h('div', { class: 'desk-details' });
        const errorEl = h('p', { class: 'context-error', role: 'alert' });
        let destroyed = false;
        /** From GET /api/agents/{id}: name, storage key, model and approval flags. */
        let detail = null;

        /**
         * Say one thing on the error line, replacing what it said.
         * @param {string} message
         * @returns {void}
         */
        function showError(message) {
            clear(errorEl);
            errorEl.append(message);
        }

        function renderMeta() {
            clear(metaEl);
            if (!detail) {
                metaEl.append(h('p', { class: 'context-skeleton' }, 'Loading workspace…'));
                return;
            }
            // The agent's one connection, as the server names it; an unlinked
            // agent (or one whose connection is gone) runs no turns, so it says so.
            const model = detail.connection
                ? `${detail.connection.name} (${detail.connection.model})`
                : 'No AI connection';
            // The shared fact list, the task detail's: two quiet facts about
            // the desk rather than two more sentences.
            metaEl.append(BossModFactList.create([
                { label: 'Workspace', value: String(detail.storage_key || 'unassigned') },
                { label: 'Model', value: String(model) },
            ]));
        }

        /**
         * Load the workspace and model lines.
         * @returns {Promise<void>} Never rejects; a failure becomes the error line.
         */
        async function refresh() {
            const loadId = detailLoad.next();
            try {
                const res = await api(`/api/agents/${agentId}`, { cache: 'no-store' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const payload = await res.json();
                if (destroyed || !detailLoad.isCurrent(loadId)) return;
                detail = payload;
            } catch (err) {
                if (destroyed || !detailLoad.isCurrent(loadId)) return;
                clear(metaEl);
                const message = 'Could not load this agent’s workspace and model.';
                console.error(`[desk-details] ${message}`, err);
                showError(message);
                return;
            }
            clear(errorEl);
            renderMeta();
        }

        /**
         * Take a newer answer for the agent than the last read.
         *
         * @param {object} agent  The agent payload a flag save answered with.
         * @returns {void}
         */
        function adopt(agent) {
            detail = agent;
            clear(errorEl);
            renderMeta();
        }

        const element = h('div', { class: 'desk-details-block' }, metaEl, errorEl);

        renderMeta();
        void refresh();

        return {
            element,
            refresh,
            agent: () => detail,
            adopt,
            showError,

            /**
             * Stop painting; an in-flight detail response is dropped.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                detailLoad.next();
            },
        };
    }

    return { createDeskDetails };
})();
