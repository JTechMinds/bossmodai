/**
 * BossMod AI — the desk footer: workspace, model, and what the operator can do
 * to an agent.
 *
 * Split out of desk-panel.js, which composes read-only sections; everything
 * here mutates. Two of the four actions destroy work that cannot be recovered,
 * so both confirm through core/overlays.js before the API is touched. A bare
 * click-through on either would be the kind of accident this codebase's rules
 * exist to prevent.
 */
const BossModDeskActions = (() => {
    const { h, clear } = BossModDom;

    const REMOVE_TITLE = 'Remove this agent?';
    const REMOVE_BODY = 'Their open tasks stop, their desk is released, and this cannot be '
        + 'undone. Completed work, artifacts, and diagnostics are preserved.';
    const RESET_TITLE = 'Reset this agent’s runtime?';
    const RESET_BODY = 'This cancels active work, clears queued triggers, resets the agent to '
        + 'idle, and may block the active task. Completed work history is preserved.';

    /**
     * Build the desk footer.
     *
     * @param {object} deps
     * @param {object}   deps.store
     * @param {Function} deps.api
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @param {string}   deps.agentId
     * @param {() => void} deps.onEdit  Swaps the desk for the role form.
     * @returns {{ element: HTMLElement, refresh: () => void, destroy: () => void }}
     * @throws {Error} When any dependency is missing.
     */
    function createDeskActions(deps) {
        const { store, api, navigate, agentId, onEdit } = deps || {};
        if (!store) throw new Error('[desk-actions] deps.store is required');
        if (typeof api !== 'function') throw new Error('[desk-actions] deps.api is required');
        if (typeof navigate !== 'function') throw new Error('[desk-actions] deps.navigate is required');
        if (!agentId) throw new Error('[desk-actions] deps.agentId is required');
        if (typeof onEdit !== 'function') throw new Error('[desk-actions] deps.onEdit is required');

        const detailLoad = BossModGates.createLoadGeneration();
        const metaEl = h('div', { class: 'desk-meta-block' });
        const errorEl = h('p', { class: 'context-error', role: 'alert' });
        let destroyed = false;
        /** From GET /api/agents/{id}: storage key and model overrides. */
        let detail = null;

        function reportError(message, err) {
            console.error(`[desk-actions] ${message}`, err);
            clear(errorEl);
            errorEl.append(message);
        }

        function renderMeta() {
            clear(metaEl);
            if (!detail) {
                metaEl.append(h('p', { class: 'context-skeleton' }, 'Loading workspace…'));
                return;
            }
            // A null model override means the company default, which is a real
            // configuration rather than a missing value.
            const model = detail.model_work || detail.model_reasoning || 'Company default';
            metaEl.append(
                h('p', { class: 'desk-meta' }, `Workspace · ${detail.storage_key || 'unassigned'}`),
                h('p', { class: 'desk-meta' }, `Model · ${model}`));
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
                reportError('Could not load this agent’s workspace and model.', err);
                return;
            }
            clear(errorEl);
            renderMeta();
        }

        /**
         * Run a destructive action behind a confirmation.
         *
         * @param {object} spec  `{title, body, confirm, run}`. Nothing is called
         *   until the operator confirms; Cancel is the focused default.
         * @returns {void}
         */
        function confirmThen(spec) {
            BossModOverlays.createModal({
                title: spec.title,
                body: spec.body,
                actions: [
                    { label: spec.confirm, tone: 'danger', onSelect: () => { void spec.run(); } },
                    { label: 'Cancel', tone: 'quiet' },
                ],
            });
        }

        async function resetRuntime() {
            clear(errorEl);
            try {
                const res = await api(`/api/agents/${agentId}/reset-runtime`, { method: 'POST' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
            } catch (err) {
                reportError('Could not reset the runtime. Try again.', err);
            }
        }

        async function removeAgent() {
            clear(errorEl);
            try {
                const res = await api(`/api/agents/${agentId}`, { method: 'DELETE' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
            } catch (err) {
                reportError('Could not remove this agent. Try again.', err);
                return;
            }
            // The agent is gone; the desk that showed them cannot stay open.
            store.setState({ contextMode: 'office', deskAgentId: null, deskPath: null });
        }

        const element = h('div', { class: 'desk-footer' },
            metaEl,
            errorEl,
            h('div', { class: 'desk-actions' },
                h('button', {
                    class: 'desk-action',
                    type: 'button',
                    onclick: () => onEdit(),
                }, 'Edit role'),
                h('button', {
                    class: 'desk-action',
                    type: 'button',
                    onclick: () => navigate('log', { agentFilter: agentId }),
                }, 'Diagnostics'),
                h('button', {
                    class: 'desk-action danger',
                    type: 'button',
                    onclick: () => confirmThen({
                        title: RESET_TITLE,
                        body: RESET_BODY,
                        confirm: 'Reset runtime',
                        run: resetRuntime,
                    }),
                }, 'Reset runtime'),
                h('button', {
                    class: 'desk-action danger',
                    type: 'button',
                    onclick: () => confirmThen({
                        title: REMOVE_TITLE,
                        body: REMOVE_BODY,
                        confirm: 'Remove agent',
                        run: removeAgent,
                    }),
                }, 'Remove')));

        renderMeta();
        void refresh();

        return {
            element,
            refresh,

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

    return { createDeskActions };
})();
