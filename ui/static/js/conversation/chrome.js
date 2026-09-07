/**
 * BossMod AI — the conversation header: who you are talking to, and the one or
 * two actions that apply to them.
 *
 * A dumb view over the `{title, subtitle, actions}` descriptor a source
 * returns. It knows nothing about threads, agents, or archiving — only that an
 * action is a named, awaitable thing that can fail.
 *
 * Action buttons are reused by id across repaints. A chrome swap therefore
 * rebinds the handler on the node the operator is already pointing at rather
 * than replacing it mid-click, which is the bug the dock-era Threads pane kept
 * reintroducing.
 */
const BossModConversationChrome = (() => {
    const { h } = BossModDom;

    /**
     * Build the header.
     *
     * @param {object} deps
     * @param {(message: string) => void} deps.onError  Shown to the operator
     *   when an action rejects. An action that fails silently would leave them
     *   believing a thread was archived when it was not.
     * @returns {{ element: HTMLElement,
     *             apply: (chrome: object) => void,
     *             destroy: () => void }}
     * @throws {Error} When `onError` is missing.
     */
    function createChrome(deps) {
        const onError = deps && deps.onError;
        if (typeof onError !== 'function') throw new Error('[chrome] deps.onError is required');

        const gate = BossModGates.createInFlightGate();
        const actionNodes = new Map();

        const titleEl = h('h2', { class: 'conversation-title' });
        const subtitleEl = h('p', { class: 'conversation-subtitle' });
        const actionsEl = h('div', { class: 'conversation-actions' });
        const element = h('header', { class: 'conversation-chrome' },
            h('div', { class: 'conversation-identity' }, titleEl, subtitleEl),
            actionsEl);

        let latest = null;

        async function run(btn, action) {
            btn.disabled = true;
            try {
                await gate.run(() => action.onSelect());
            } catch (err) {
                console.error(`[chrome] the "${action.label}" action failed`, err);
                onError((err && err.message) || `${action.label} failed.`);
            } finally {
                // Re-reads the descriptor, so an aborted action re-enables the
                // same button and a completed one may swap it for another.
                if (latest) apply(latest);
            }
        }

        /**
         * Paint one chrome descriptor.
         *
         * @param {{title: string, subtitle: string, actions: object[]}} chrome
         *   Each action is `{id, label, onSelect}`; `onSelect` may return a
         *   promise and may reject.
         * @returns {void}
         */
        function apply(chrome) {
            latest = chrome;
            titleEl.textContent = chrome.title || '';
            subtitleEl.textContent = chrome.subtitle || '';
            const wanted = new Set();
            (chrome.actions || []).forEach((action) => {
                wanted.add(action.id);
                let btn = actionNodes.get(action.id);
                if (!btn) {
                    btn = h('button', { class: 'conversation-action', type: 'button', id: action.id });
                    actionNodes.set(action.id, btn);
                }
                btn.textContent = action.label;
                btn.onclick = () => { void run(btn, action); };
                btn.disabled = gate.busy();
                actionsEl.append(btn);
            });
            for (const [id, btn] of Array.from(actionNodes)) {
                if (wanted.has(id)) continue;
                btn.remove();
                actionNodes.delete(id);
            }
        }

        return {
            element,
            apply,
            /**
             * Drop every action node and its binding.
             * @returns {void}
             */
            destroy() {
                for (const btn of actionNodes.values()) {
                    btn.onclick = null;
                    btn.remove();
                }
                actionNodes.clear();
                latest = null;
            },
        };
    }

    return { createChrome };
})();
