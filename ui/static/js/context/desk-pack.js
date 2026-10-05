/**
 * BossMod AI — the desk's pack line: which pack an agent was hired from, and
 * the per-agent update when its installed template has moved on.
 *
 * `◆ Code Auditor · 8a0d68a` under the agent's status, in the About section.
 * When the installed template is newer than the contract this agent was last
 * given, the line adds `Update available 8a0d68a → 9d2352e` and an Update
 * button. The update replaces the agent's description, done bar and
 * communication only, behind the standard confirm layer (core/overlays.js),
 * which also warns when the agent's contract was edited and those edits would
 * be overwritten.
 *
 * A LOCAL READ: `GET /api/agents/{id}/pack-status` compares the agent's link
 * with the installed template and never fetches the catalog — moving the
 * catalog is the marketplace's job. The update sends back the template hash
 * that was on screen, so a template re-installed in between is a 409 and a
 * re-read, never a silent write of something the operator did not see.
 *
 * An agent not hired from a pack has no line: the element stays hidden, the
 * same way the desk hides an empty Extensions section.
 */
const BossModDeskPack = (() => {
    const { h, clear } = BossModDom;

    const COPY = Object.freeze({
        mark: '◆',
        notInstalled: 'pack not installed',
        available: 'Update available',
        update: 'Update',
        updating: 'Updating…',
        cancel: 'Cancel',
        confirmTitle: 'Update this agent from its pack?',
        kept: 'Name, specialty, colour, AI connection and desk are kept.',
        edited: 'This agent’s contract was edited after the pack last wrote it. '
            + 'Those edits will be overwritten.',
        loadFailed: 'Couldn’t read which pack this agent came from.',
        updateFailed: 'Couldn’t update this agent from its pack.',
    });

    /**
     * The error text a failed response carries, or `fallback`.
     * @param {Response} res
     * @param {string} fallback
     * @returns {Promise<string>}
     */
    async function failureText(res, fallback) {
        const data = await res.json().catch(() => ({}));
        const detail = data && data.detail;
        return (detail && detail.message) || (typeof detail === 'string' ? detail : '') || fallback;
    }

    /**
     * Build one agent's pack line.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string}   deps.agentId
     * @returns {{element: HTMLElement, refresh: () => Promise<void>, destroy: () => void}}
     *   `element` is hidden until a read says the agent is linked; `refresh`
     *   re-reads the status and never rejects — a failure is the line's error
     *   text. `destroy` drops an in-flight read and closes an open confirm.
     * @throws {Error} When a dependency is missing.
     */
    function createDeskPack(deps) {
        const { api, agentId } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-pack] deps.api is required');
        if (!agentId) throw new Error('[desk-pack] deps.agentId is required');

        const element = h('div', { class: 'desk-pack', hidden: true });
        const loads = BossModGates.createLoadGeneration();
        let status = null;
        let error = '';
        let busy = false;
        let layer = null;
        let destroyed = false;

        function render() {
            clear(element);
            element.hidden = !error && !(status && status.linked);
            if (busy) element.setAttribute('aria-busy', 'true');
            else element.removeAttribute('aria-busy');
            if (status && status.linked) {
                const parts = [status.template_title || status.pack_id];
                if (status.current_short) parts.push(status.current_short);
                if (!status.installed) parts.push(COPY.notInstalled);
                element.append(h('p', { class: 'desk-pack-line' },
                    h('span', { class: 'desk-pack-mark', 'aria-hidden': 'true' }, COPY.mark),
                    h('span', {}, parts.join(' · '))));
                if (status.update_available) {
                    element.append(h('p', { class: 'desk-pack-update' },
                        h('span', {}, `${COPY.available} ${status.current_short || ''} → ${status.available_short}`),
                        h('button', {
                            class: 'btn btn-sm', id: 'desk-pack-update', type: 'button',
                            disabled: busy, onclick: () => confirmUpdate(),
                        }, busy ? COPY.updating : COPY.update)));
                }
            }
            if (error) element.append(h('p', { class: 'context-error', role: 'alert' }, error));
        }

        /**
         * Re-read the agent's pack status.
         * @returns {Promise<void>} Never rejects.
         */
        async function refresh() {
            const loadId = loads.next();
            let next;
            try {
                const res = await api(`/api/agents/${agentId}/pack-status`, { cache: 'no-store' });
                if (!res.ok) throw new Error(await failureText(res, COPY.loadFailed));
                next = await res.json();
            } catch (err) {
                if (destroyed || !loads.isCurrent(loadId)) return;
                console.error('[desk-pack] the pack status could not be read', err);
                error = COPY.loadFailed;
                render();
                return;
            }
            if (destroyed || !loads.isCurrent(loadId)) return;
            status = next;
            error = '';
            render();
        }

        async function runUpdate(expected) {
            busy = true;
            error = '';
            render();
            try {
                const res = await api(`/api/agents/${agentId}/pack-update`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ expected_content_hash: expected }),
                });
                if (!res.ok) throw new Error(await failureText(res, COPY.updateFailed));
            } catch (err) {
                console.error('[desk-pack] the agent could not be updated', err);
                busy = false;
                if (destroyed) return;
                error = (err && err.message) || COPY.updateFailed;
                render();
                // A 409 means what is installed moved; show what is there now.
                void refresh();
                return;
            }
            busy = false;
            if (!destroyed) await refresh();
        }

        function confirmUpdate() {
            if (layer || busy || !status || !status.update_available) return;
            const expected = status.available_content_hash;
            const title = status.template_title || status.pack_id;
            layer = BossModOverlays.createModal({
                title: COPY.confirmTitle,
                closeOnBackdrop: true,
                body: h('div', { class: 'desk-pack-confirm' },
                    h('p', {}, `Replaces this agent’s description, done bar and communication with `
                        + `${title} at ${status.available_short}. ${COPY.kept}`),
                    status.edited ? h('div', { class: 'callout', 'data-tone': 'warn' },
                        h('p', { class: 'callout-body' }, COPY.edited)) : null),
                actions: [
                    { label: COPY.update, tone: 'primary', id: 'desk-pack-confirm',
                        onSelect: () => { void runUpdate(expected); } },
                    { label: COPY.cancel, tone: 'quiet' },
                ],
                onClose: () => { layer = null; },
            });
        }

        render();
        void refresh();
        return {
            element,
            refresh,
            destroy() {
                destroyed = true;
                loads.next();
                if (layer) layer.close();
            },
        };
    }

    return { COPY, createDeskPack };
})();
