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
 *
 * The TITLE may be renameable, and says so through the descriptor's optional
 * `onRename` rather than by this view learning what a thread is. The control
 * itself is conversation/title-rename.js; what belongs here is where its
 * confirm and cancel land — in the same action row Archive sits in, through
 * the same `{id, label, icon, onSelect}` descriptor, rather than as bespoke
 * buttons beside the title. Both are icon-only, so each carries its own
 * accessible name: colour is not the only carrier (SC 1.4.1), and a check and
 * a cross differ in shape as well as in hue.
 *
 * VIEW OPTIONS are a different kind of thing from actions and sit behind a `⋯`
 * rather than beside them. `Desk` is an action on the person; "show system
 * notifications" is a preference about the transcript, and the two were at the
 * same level with the preference's 25-character label crowding out the one
 * real action. The menu is where later view options go, which is what makes it
 * a place to put things rather than a place to hide one thing.
 */
const BossModConversationChrome = (() => {
    const { h, clear } = BossModDom;

    /**
     * Build the header.
     *
     * @param {object} deps
     * @param {(message: string) => void} deps.onError  Shown to the operator
     *   when an action rejects. An action that fails silently would leave them
     *   believing a thread was archived when it was not.
     * @param {HTMLElement[]} [deps.viewOptions]  Controls for the `⋯` menu,
     *   built once by the caller and moved into the panel each time it opens —
     *   so a preference keeps what it holds across every open, and across every
     *   conversation switch. They are the surface's, not any one
     *   conversation's, which is why they are a mount-time slot rather than a
     *   descriptor field that would be rebuilt on each switch. With none, no
     *   `⋯` is rendered: a menu with nothing in it is not a menu.
     * @returns {{ element: HTMLElement,
     *             apply: (chrome: object) => void,
     *             destroy: () => void }}
     * @throws {Error} When `onError` is missing.
     */
    function createChrome(deps) {
        const onError = deps && deps.onError;
        if (typeof onError !== 'function') throw new Error('[chrome] deps.onError is required');
        const viewOptions = (deps && deps.viewOptions) || [];

        const gate = BossModGates.createInFlightGate();
        const actionNodes = new Map();

        const avatarEl = h('div', { class: 'conversation-avatar' });
        // Renameable or not is the descriptor's call, made per conversation.
        const title = BossModTitleRename.createEditableTitle({
            onError,
            // Opening or closing the rename adds or drops Save, and the action
            // row is repainted from the descriptor that is already on screen.
            onEditingChange: () => { if (latest) apply(latest); },
        });
        const subtitleEl = h('p', { class: 'conversation-subtitle' });
        const actionsEl = h('div', { class: 'conversation-actions' });
        const element = h('header', { class: 'conversation-chrome' },
            avatarEl,
            h('div', { class: 'conversation-identity' }, title.element, subtitleEl),
            actionsEl);

        /** The open menu, or null. One at a time, and the `⋯` toggles it. */
        let menu = null;
        const menuButton = viewOptions.length
            ? h('button', {
                class: 'btn btn-sm conversation-action conversation-view-options',
                type: 'button',
                id: 'conversation-view-options',
                'aria-label': 'View options',
                // dialog, not menu: the panel holds a role="switch", which is
                // not a menuitem and must not be announced as one.
                'aria-haspopup': 'dialog',
                'aria-expanded': 'false',
                onclick: () => toggleMenu(),
            }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }))
            : null;

        /** @returns {void} */
        function closeMenu() {
            if (!menu) return;
            const open = menu;
            menu = null;
            open.close();
        }

        /**
         * Show the view options, or put them away again.
         *
         * The panel is core/overlays.js's — it already owns the focus trap, Esc,
         * and returning focus to the control that opened it. A second popover
         * implementation is exactly the duplication the primitives exist to
         * remove.
         *
         * @returns {void}
         */
        function toggleMenu() {
            if (menu) {
                closeMenu();
                return;
            }
            menu = BossModOverlays.createMenu({
                anchor: menuButton,
                label: 'View options',
                items: viewOptions,
                container: element,
                onClose: () => {
                    menu = null;
                    menuButton.setAttribute('aria-expanded', 'false');
                },
            });
            menuButton.setAttribute('aria-expanded', 'true');
        }

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
                : BossModAvatar.create({ group: true, size: 'md' }));
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
         * @param {{title: string, subtitle: string, avatar?: object,
         *   actions: object[], onRename?: (name: string) => Promise<void>}} chrome
         *   `avatar` is optional `{name, color}`; without it the group glyph is
         *   shown. Each action is `{id, label, icon?, iconOnly?, onSelect}`,
         *   where `icon` is a Lucide glyph NAME — the source names it, this
         *   builds it — and `iconOnly` shows the glyph alone with `label` as
         *   the button's accessible name instead of its text (SC 4.1.2).
         *   `onSelect` may return a promise and may reject. `onRename` is
         *   optional: with it the title is editable in place, without it the
         *   title is plain text.
         * @returns {void}
         */
        function apply(chrome) {
            latest = chrome;
            applyAvatar(chrome.avatar || null);
            title.apply({ title: chrome.title || '', onRename: chrome.onRename });
            subtitleEl.textContent = chrome.subtitle || '';
            const wanted = new Set();
            // The pair exists only while a rename is open, and both join the
            // row through the same descriptor every other action uses —
            // first, so the two ways out of the mode sit ahead of Archive
            // rather than after it. Cancel calls the SAME function Esc does,
            // so the keystroke and the control cannot drift apart.
            const actions = title.isEditing()
                ? [{
                    id: 'conversation-title-cancel',
                    label: 'Cancel rename',
                    icon: 'x',
                    iconOnly: true,
                    onSelect: () => title.cancel(),
                }, {
                    id: 'conversation-title-save',
                    label: 'Save name',
                    icon: 'check',
                    iconOnly: true,
                    onSelect: () => title.save(),
                }].concat(chrome.actions || [])
                : (chrome.actions || []);
            actions.forEach((action) => {
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
                // An icon-only action shows its glyph and NAMES itself, so the
                // label is still announced rather than lost with the text.
                if (action.iconOnly) btn.setAttribute('aria-label', action.label);
                else btn.append(action.label);
                btn.onclick = () => { void run(btn, action); };
                btn.disabled = gate.busy();
                actionsEl.append(btn);
            });
            for (const [id, btn] of Array.from(actionNodes)) {
                if (wanted.has(id)) continue;
                btn.remove();
                actionNodes.delete(id);
            }
            // Appended last on every pass, so the `⋯` stays at the end of the
            // row however the actions before it churn.
            if (menuButton) actionsEl.append(menuButton);
            lucide.createIcons();
        }

        return {
            element,
            apply,

            /**
             * Forget an edit in progress.
             *
             * The controller calls this when the OPEN CONVERSATION changes. The
             * descriptor carries no identity, so without it a rename half typed
             * for one thread would survive into the next one — apply() leaves
             * the field alone while it is being edited, which is exactly what
             * keeps a live repaint from stealing the operator's typing.
             *
             * @returns {void}
             */
            reset() {
                title.cancel();
            },

            /**
             * Drop every action node and its binding, and put the menu away —
             * a panel left open would outlive the header it hangs off.
             * @returns {void}
             */
            destroy() {
                closeMenu();
                // A rename left open would keep the last conversation's draft
                // on screen over the next one.
                title.cancel();
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
