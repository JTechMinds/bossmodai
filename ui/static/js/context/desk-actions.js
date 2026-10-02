/**
 * BossMod AI — the desk's Details facts, and the two destructive actions on an
 * agent.
 *
 * Split out of desk-panel.js, which composes read-only sections; the two
 * runners here destroy work that cannot be recovered, so both confirm through
 * core/overlays.js before the API is touched. A bare click-through on either
 * would be the kind of accident this codebase's rules exist to prevent.
 *
 * It renders NO BUTTONS. The desk used to end in four text links — Edit role,
 * Diagnostics, Reset runtime, Remove — at the bottom of a long scroll, two of
 * them red, in the reading flow. In every other modal those live in the head,
 * and the destructive ones behind its `⋯` (places/tasks/task-detail.js): so
 * the desk panel's head `⋯` calls `confirmReset()` and `confirmRemove()` here,
 * and what this renders is the quiet facts — workspace and model — plus the
 * error line either runner reports into.
 */
const BossModDeskActions = (() => {
    const { h, clear } = BossModDom;

    const REMOVE_TITLE = 'Remove this agent?';
    // Said instead of the warning while GET /api/agents/{id} has not landed:
    // the warning names whose files go, so there is no dialog without the name.
    const REMOVE_NOT_READY = 'This agent’s details haven’t loaded yet, so Remove '
        + 'can’t say whose files it deletes. Try again in a moment.';

    const RESET_TITLE = 'Reset this agent’s runtime?';
    const RESET_BODY = 'This cancels active work, clears queued triggers, resets the agent to '
        + 'idle, and may block the active task. Completed work history is preserved.';

    /**
     * Build the Details facts and the runners the desk's `⋯` calls.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string}   deps.agentId
     * @param {() => void} deps.onRemoved  Called once the DELETE has landed:
     *   the desk that showed the agent cannot stay open, and closing it is the
     *   desk dialog's job, not this module's.
     * @returns {{ element: HTMLElement, refresh: () => Promise<void>,
     *   confirmReset: () => void, confirmRemove: () => void,
     *   destroy: () => void }} `element` is the facts and the error line.
     *   `confirmReset`/`confirmRemove` open the confirmation layer; nothing
     *   reaches the API until the operator confirms.
     * @throws {Error} When any dependency is missing.
     */
    function createDeskActions(deps) {
        const { api, agentId, onRemoved } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-actions] deps.api is required');
        if (!agentId) throw new Error('[desk-actions] deps.agentId is required');
        if (typeof onRemoved !== 'function') throw new Error('[desk-actions] deps.onRemoved is required');

        const detailLoad = BossModGates.createLoadGeneration();
        const metaEl = h('div', { class: 'desk-details' });
        const errorEl = h('p', { class: 'context-error', role: 'alert' });
        let destroyed = false;
        /** From GET /api/agents/{id}: name, storage key and model overrides. */
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
                reportError('Could not load this agent’s workspace and model.', err);
                return;
            }
            clear(errorEl);
            renderMeta();
        }

        /**
         * Run a destructive action behind a confirmation.
         *
         * Opened over the desk, it is a layer in the same frame
         * (core/overlays.js), so Cancel and Esc come back to the desk.
         *
         * @param {object} spec  `{title, body, confirm, run}`. Nothing is called
         *   until the operator confirms; Cancel is the focused default.
         * @returns {void}
         */
        function confirmThen(spec) {
            BossModOverlays.createModal({
                title: spec.title,
                closeOnBackdrop: true,
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

        /**
         * Open Reset runtime's confirmation.
         * @returns {void}
         */
        function confirmReset() {
            confirmThen({
                title: RESET_TITLE,
                body: RESET_BODY,
                confirm: 'Reset runtime',
                run: resetRuntime,
            });
        }

        /**
         * Open Remove's confirmation, which names the agent it deletes.
         *
         * The name is `detail.name`, so until the detail read lands there is
         * no dialog: the Details section's error line says why and the read is
         * retried, which also covers a read that failed rather than one still
         * in flight.
         * @returns {void}
         */
        function confirmRemove() {
            if (!detail) {
                clear(errorEl);
                errorEl.append(REMOVE_NOT_READY);
                void refresh();
                return;
            }
            confirmThen({
                title: REMOVE_TITLE,
                body: BossModAgentApi.agentDeleteWarning(detail.name),
                confirm: 'Remove agent',
                run: removeAgent,
            });
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
            // Unless it already closed while the DELETE was in flight: the
            // desk open now may be someone else's.
            if (destroyed) return;
            onRemoved();
        }

        const element = h('div', { class: 'desk-details-block' }, metaEl, errorEl);

        renderMeta();
        void refresh();

        return {
            element,
            refresh,
            confirmReset,
            confirmRemove,

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
