/**
 * BossMod AI — the Extensions section of an agent's desk.
 *
 * One row per enabled extension that has per-agent settings (manifest
 * `agent_config`): its name, its state ("Not set up", or the one summary
 * value the extension names, e.g. the mailbox address), a Configure/Edit
 * action (BossModAgentConfigDialog) and, once configured, the extension's
 * view action (BossModAgentViewDialog, e.g. "Open inbox").
 *
 * A configured extension that wakes agents (manifest `wake`) adds one health
 * line: "Last checked 1:42 PM" (local, absolute, so it is never stale on
 * screen) with "· 2 new at 1:41 PM" when the last find was new, "Can't check
 * <setting group>: <sentence>" in the alert ink, or "Waiting for first check".
 * It is re-read when the desk opens and after the settings dialog closes;
 * there is no polling.
 *
 * Content only: the header is desk-panel.js's. The panel hides the whole
 * section while `isEmpty()` — no per-agent extension is enabled, or the
 * first read has not answered yet — so most desks show nothing new. A failed
 * read is NOT empty: it shows the error and a retry.
 */
const BossModDeskExtensions = (() => {
    const { h, clear } = BossModDom;
    const API = BossModExtensionsApi;

    const COPY = Object.freeze({
        loading: 'Loading extensions…',
        failed: 'Extensions could not be loaded.',
        retry: 'Try again',
        notSetUp: 'Not set up',
        configured: 'Set up',
        configure: 'Configure',
        edit: 'Edit',
        waiting: 'Waiting for first check',
        lastChecked: (time) => `Last checked ${time}`,
        newAt: (count, time) => ` · ${count} new at ${time}`,
        cantCheck: (label, sentence) => `Can’t check ${label}: ${sentence}`,
    });

    /**
     * The wake health line for a configured wake extension, or null.
     *
     * @param {object} item  An agentExtensions item whose extension wakes
     *   agents (`wakes`); its `wake` is null before the first check.
     * @returns {HTMLElement}
     */
    function wakeLine(item) {
        const wake = item.wake;
        if (!wake) {
            return h('span', { class: 'desk-ext-wake', role: 'status' }, COPY.waiting);
        }
        if (!wake.ok) {
            return h('span', { class: 'desk-ext-wake', 'data-tone': 'alert', role: 'status' },
                COPY.cantCheck(item.config_label, wake.error));
        }
        let text = COPY.lastChecked(BossModFormat.formatClockTime(wake.checked_at));
        if (wake.last_new_count > 0 && wake.last_new_at) {
            text += COPY.newAt(wake.last_new_count, BossModFormat.formatClockTime(wake.last_new_at));
        }
        return h('span', { class: 'desk-ext-wake', role: 'status' }, text);
    }

    /**
     * Build the section.
     *
     * @param {object} deps
     * @param {string} deps.agentId
     * @param {() => string} deps.agentName  The agent's current name (dialog titles).
     * @param {() => void} deps.onChange  Called after every render, so the
     *   panel can re-read `isEmpty()`.
     * @returns {{ element: HTMLElement, refresh: () => Promise<void>,
     *             destroy: () => void, isEmpty: () => boolean }}
     * @throws {Error} When any dependency is missing.
     */
    function createDeskExtensions(deps) {
        const { agentId, agentName, onChange } = deps || {};
        if (!agentId) throw new Error('[desk-extensions] deps.agentId is required');
        if (typeof agentName !== 'function') throw new Error('[desk-extensions] deps.agentName is required');
        if (typeof onChange !== 'function') throw new Error('[desk-extensions] deps.onChange is required');

        const load = BossModGates.createLoadGeneration();
        const element = h('div', { class: 'desk-extensions' });
        /** 'initial' until the first answer, then 'ready' or 'failed'. */
        let status = 'initial';
        let items = [];
        let destroyed = false;
        /** Open dialogs, closed with the desk. */
        const dialogs = new Set();

        function track(dialog) {
            dialogs.add(dialog);
            return dialog;
        }

        function openConfig(item) {
            track(BossModAgentConfigDialog.open({
                extensionId: item.id,
                agentId,
                agentName: agentName() || item.name,
                onSaved: () => { void refresh(); },
                onClosed: () => { if (!destroyed) void refresh(); },
            }));
        }

        function openView(item) {
            track(BossModAgentViewDialog.open({
                extensionId: item.id,
                agentId,
                title: `${agentName() || item.name} — ${item.view_label}`,
            }));
        }

        function row(item) {
            const nameId = `desk-ext-name-${item.id}`;
            // One row: the glyph, the name and state on the left, the actions
            // on the right.
            return h('li', { class: 'desk-ext', 'aria-labelledby': nameId },
                h('i', { 'data-lucide': 'puzzle', 'aria-hidden': 'true' }),
                h('div', { class: 'desk-ext-main' },
                    h('span', { class: 'desk-ext-name', id: nameId }, item.name),
                    h('span', { class: 'desk-ext-status' },
                        item.configured ? (item.summary || COPY.configured) : COPY.notSetUp),
                    item.configured && item.wakes ? wakeLine(item) : null),
                h('div', { class: 'desk-ext-actions' },
                    h('button', {
                        class: 'btn btn-sm', type: 'button', id: `desk-ext-config-${item.id}`,
                        'aria-describedby': nameId,
                        onclick: () => openConfig(item),
                    }, item.configured ? COPY.edit : COPY.configure),
                    item.configured && item.view_label
                        ? h('button', {
                            class: 'btn btn-sm', type: 'button', id: `desk-ext-view-${item.id}`,
                            'aria-describedby': nameId,
                            onclick: () => openView(item),
                        }, item.view_label)
                        : null));
        }

        function render(message) {
            clear(element);
            if (status === 'failed') {
                element.append(
                    h('p', { class: 'context-error', role: 'alert' }, message || COPY.failed),
                    h('button', {
                        class: 'btn btn-sm', type: 'button', id: 'desk-ext-retry',
                        onclick: () => { void refresh(); },
                    }, COPY.retry));
            } else if (status === 'ready' && items.length) {
                element.append(h('ul', { class: 'desk-ext-list' }, items.map(row)));
            } else if (status === 'initial') {
                element.append(h('p', { class: 'context-skeleton' }, COPY.loading));
            }
            // Rebuilt per read, so this section paints its own glyphs.
            BossModIcons.paint(element, 'desk-extensions');
            onChange();
        }

        /**
         * Re-read this agent's per-agent extensions.
         *
         * @returns {Promise<void>} Never rejects; every outcome is a state.
         */
        async function refresh() {
            const loadId = load.next();
            let next;
            try {
                next = await API.agentExtensions(agentId);
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[desk-extensions] could not read this agent’s extensions', err);
                status = 'failed';
                render(String((err && err.message) || COPY.failed));
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;
            items = next;
            status = 'ready';
            render();
        }

        render();
        void refresh();

        return {
            element,
            refresh,
            /** @returns {boolean} Whether there is nothing to show. */
            isEmpty() {
                return status === 'initial' || (status === 'ready' && !items.length);
            },
            /**
             * Stop painting and close any dialog this section opened.
             * @returns {void}
             */
            destroy() {
                destroyed = true;
                load.next();
                dialogs.forEach((dialog) => dialog.close());
                dialogs.clear();
            },
        };
    }

    return { createDeskExtensions };
})();
