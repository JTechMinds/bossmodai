/**
 * BossMod AI — the conversation header: who you are talking to, and the one or
 * two actions that apply to them.
 *
 * A dumb view over the `{title, subtitle, avatar?, actions}` descriptor a
 * source returns. It knows nothing about threads, agents, or archiving — only
 * that an action is a named, awaitable thing that can fail.
 *
 * The descriptor GREW rather than the view reaching for the roster. That is the
 * whole reason the conversation surface never subscribes to `world_update`, and
 * adding an avatar here would have been the obvious way to cross it: `avatar`
 * is `{name, color}` data the source already holds, and `icon` is a glyph NAME
 * the source can hand over without building an element.
 *
 * Action buttons are reused by id across repaints. A chrome swap therefore
 * rebinds the handler on the node the operator is already pointing at rather
 * than replacing it mid-click, which is the bug the dock-era Threads pane kept
 * reintroducing.
 */
const BossModConversationChrome = (() => {
    const { h, clear } = BossModDom;

    /** What a conversation with no single face shows instead of one. */
    const GROUP_GLYPH = '\u22EF';

    /**
     * Build the header.
     *
     * @param {object} deps
     * @param {(message: string) => void} deps.onError  Shown to the operator
     *   when an action rejects. An action that fails silently would leave them
     *   believing a thread was archived when it was not.
     * @param {HTMLElement} [deps.trailing]  A node pinned after the actions for
     *   the life of the view. The receipts preference lives here rather than in
     *   the descriptor: it belongs to the surface, not to any one conversation,
     *   and a descriptor field would rebuild it on every switch.
     * @returns {{ element: HTMLElement,
     *             apply: (chrome: object) => void,
     *             destroy: () => void }}
     * @throws {Error} When `onError` is missing.
     */
    function createChrome(deps) {
        const onError = deps && deps.onError;
        if (typeof onError !== 'function') throw new Error('[chrome] deps.onError is required');
        const trailing = (deps && deps.trailing) || null;

        const gate = BossModGates.createInFlightGate();
        const actionNodes = new Map();

        const avatarEl = h('div', { class: 'conversation-avatar' });
        const titleEl = h('h2', { class: 'conversation-title' });
        const subtitleEl = h('p', { class: 'conversation-subtitle' });
        const actionsEl = h('div', { class: 'conversation-actions' });
        const element = h('header', { class: 'conversation-chrome' },
            avatarEl,
            h('div', { class: 'conversation-identity' }, titleEl, subtitleEl),
            actionsEl);

        let latest = null;
        /** `name|color`, or 'group'. Compared so a presence repaint does not churn the node. */
        let avatarKey = null;

        /**
         * Paint the identity, but only when it actually changed.
         *
         * apply() runs on every presence signal and every roster tick, and
         * rebuilding the avatar each time would swap the node under a pointer
         * that is already on it.
         *
         * @param {{name: string, color: string|null}|null} avatar
         */
        function applyAvatar(avatar) {
            const key = avatar ? `${avatar.name}|${avatar.color}` : 'group';
            if (key === avatarKey) return;
            avatarKey = key;
            clear(avatarEl);
            avatarEl.append(avatar
                ? BossModAvatar.create({ name: avatar.name, color: avatar.color, size: 'md' })
                : h('span', {
                    class: 'avatar avatar-md avatar-group',
                    'aria-hidden': 'true',
                }, GROUP_GLYPH));
        }

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
         * @param {{title: string, subtitle: string, avatar?: object, actions: object[]}} chrome
         *   `avatar` is optional `{name, color}`; without it the group glyph is
         *   shown. Each action is `{id, label, icon?, onSelect}`, where `icon`
         *   is a Lucide glyph NAME — the source names it, this builds it.
         *   `onSelect` may return a promise and may reject.
         * @returns {void}
         */
        function apply(chrome) {
            latest = chrome;
            applyAvatar(chrome.avatar || null);
            titleEl.textContent = chrome.title || '';
            subtitleEl.textContent = chrome.subtitle || '';
            const wanted = new Set();
            (chrome.actions || []).forEach((action) => {
                wanted.add(action.id);
                let btn = actionNodes.get(action.id);
                if (!btn) {
                    btn = h('button', { class: 'btn btn-sm conversation-action', type: 'button', id: action.id });
                    actionNodes.set(action.id, btn);
                }
                clear(btn);
                if (action.icon) {
                    btn.append(h('i', { 'data-lucide': action.icon, 'aria-hidden': 'true' }));
                }
                btn.append(action.label);
                btn.onclick = () => { void run(btn, action); };
                btn.disabled = gate.busy();
                actionsEl.append(btn);
            });
            for (const [id, btn] of Array.from(actionNodes)) {
                if (wanted.has(id)) continue;
                btn.remove();
                actionNodes.delete(id);
            }
            // Appended last on every pass, so the preference stays at the end of
            // the row however the actions around it churn.
            if (trailing) actionsEl.append(trailing);
            lucide.createIcons();
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
