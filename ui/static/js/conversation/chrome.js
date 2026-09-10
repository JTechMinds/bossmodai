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
 * confirm and cancel land — BESIDE the title, which is the thing they act on.
 * They sat at the far right for a while, seven hundred pixels from the field
 * whose edit they were confirming, and the operator had to cross the header to
 * answer a question the header was asking on the left. Both are icon-only, so
 * each carries its own accessible name: colour is not the only carrier
 * (SC 1.4.1), and a check and a cross differ in shape as well as in hue.
 *
 * THREE SLOTS, ONE FIELD. Every action is the same `{id, label, icon,
 * onSelect}` with the same reused node, the same in-flight gate and the same
 * error path; `slot` is the only thing that says where it lands, so nothing
 * about a control can drift from the vocabulary by sitting somewhere else.
 *
 *   (default)      the action row, at the right. Frequent and safe.
 *   slot: 'menu'   behind the `⋯`. Rare or irreversible — Archive and Reopen.
 *   slot: 'title'  beside the title. Confirming an edit to the title itself.
 *
 * The `⋯` is the header's OVERFLOW and it holds two kinds of thing. The first
 * is view options — "show system notifications" is a preference about the
 * transcript rather than an action on the person, and it was crowding out the
 * one real action with its 25-character label. The second is any `slot:
 * 'menu'` action: a destructive, once-a-month control does not earn a
 * permanent seat beside the conversation title, and a bordered `Archive`
 * sitting there every time you open a thread reads as a suggestion.
 *
 * The menu's actions are appended into a STABLE panel node this view owns
 * rather than handed to the menu at open time, which is what lets a repaint
 * that happens while the panel is open — Archive succeeding and becoming
 * Reopen — land inside the panel the operator is looking at.
 *
 * The SUBTITLE sits with the actions rather than with the title. `3
 * participants` is a fact about the room and the title is its name; putting
 * the fact directly after the name meant a long name shoved it, and the two
 * controls that confirm a rename had to go somewhere else because the space
 * beside the title was already spent.
 */
const BossModConversationChrome = (() => {
    const { h, clear } = BossModDom;

    /** The `⋯`'s accessible name and its tooltip: one string, never two. */
    const MENU_LABEL = 'More actions';

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
     *   descriptor field that would be rebuilt on each switch. With none, the
     *   `⋯` is rendered only for as long as some conversation puts a
     *   `slot: 'menu'` action behind it: a menu with nothing in it is not one.
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
        // Empty out of edit mode, and `:empty` collapses it, so the header at
        // rest is the name and nothing beside it.
        const titleActionsEl = h('div', { class: 'conversation-title-actions' });
        const actionsEl = h('div', { class: 'conversation-actions' });
        const element = h('header', { class: 'conversation-chrome' },
            avatarEl,
            h('div', { class: 'conversation-identity' }, title.element, titleActionsEl),
            // The subtitle leads the action group: a fact about the room, at
            // the end of the row, where a long name cannot shove it.
            actionsEl);
        actionsEl.append(subtitleEl);

        /** The open menu, or null. One at a time, and the `⋯` toggles it. */
        let menu = null;
        /**
         * The menu ACTIONS' parent, owned here and reused forever.
         *
         * Built once and never replaced, for the same reason the view options
         * are the caller's nodes rather than rebuilt on open: it may be inside
         * an open panel when apply() runs, and refilling a stable node is what
         * keeps a live chrome swap visible to whoever is looking at it. While
         * the panel is closed this is simply detached, holding its buttons.
         */
        const menuActionsEl = h('div', { class: 'menu-actions' });
        const menuButton = h('button', {
            class: 'btn btn-sm conversation-action conversation-view-options',
            type: 'button',
            id: 'conversation-view-options',
            'aria-label': MENU_LABEL,
            'data-tooltip': MENU_LABEL,
            // dialog, not menu: the panel holds a role="switch", which is
            // not a menuitem and must not be announced as one.
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggleMenu(),
        }, h('i', { 'data-lucide': 'ellipsis', 'aria-hidden': 'true' }));

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
                label: MENU_LABEL,
                // Actions first, then preferences: one is a thing to do and
                // the other is a thing to set, and the doing comes first.
                items: [menuActionsEl].concat(viewOptions),
                container: element,
                onClose: () => {
                    menu = null;
                    menuButton.setAttribute('aria-expanded', 'false');
                },
            });
            // THE PANEL IS THE ONE TREE THE SWEEP CANNOT REACH. apply() ends on
            // BossModIcons.paintDocument, which walks document.body — and while
            // the menu is closed `menuActionsEl` hangs off a detached node, so
            // an action built in there kept its bare `<i>` placeholder and
            // rendered as a bare word. Archive read as a stray heading in the
            // panel because of exactly this. Paint what was just attached.
            BossModIcons.paint(menu.element, 'conversation-chrome.menu');
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
         *   shown. Each action is `{id, label, icon?, iconOnly?, slot?,
         *   onSelect}`, where `icon` is a Lucide glyph NAME — the source names
         *   it, this builds it — `iconOnly` shows the glyph alone with `label`
         *   as the button's accessible name instead of its text (SC 4.1.2) and
         *   as its tooltip, and `slot` is `'menu'` to put it behind the `⋯`,
         *   `'title'` to put it beside the name, or absent for the action row.
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
                    slot: 'title',
                    onSelect: () => title.cancel(),
                }, {
                    id: 'conversation-title-save',
                    label: 'Save name',
                    icon: 'check',
                    iconOnly: true,
                    slot: 'title',
                    onSelect: () => title.save(),
                }].concat(chrome.actions || [])
                : (chrome.actions || []);
            let inMenu = 0;
            actions.forEach((action) => {
                wanted.add(action.id);
                const menuAction = action.slot === 'menu';
                let btn = actionNodes.get(action.id);
                if (!btn) {
                    btn = h('button', { type: 'button', id: action.id });
                    actionNodes.set(action.id, btn);
                }
                // Set on every pass rather than at construction: an action may
                // move between the row and the menu across a chrome swap, and
                // the node is deliberately reused when it does.
                btn.className = menuAction
                    ? 'menu-action'
                    : 'btn btn-sm conversation-action';
                clear(btn);
                if (action.icon) {
                    btn.append(h('i', { 'data-lucide': action.icon, 'aria-hidden': 'true' }));
                }
                // An icon-only action shows its glyph and NAMES itself, so the
                // label is still announced rather than lost with the text — and
                // the same string becomes the bubble a pointer gets on hover,
                // which is the only way a glyph alone says what it does. A menu
                // action always keeps its text: a panel of bare glyphs is a
                // puzzle, and there is room for words in there.
                if (action.iconOnly && !menuAction) {
                    btn.setAttribute('aria-label', action.label);
                    btn.setAttribute('data-tooltip', action.label);
                } else {
                    btn.removeAttribute('aria-label');
                    btn.removeAttribute('data-tooltip');
                    btn.append(action.label);
                }
                // A menu action puts the panel away before it runs, so focus is
                // back on the `⋯` before a confirm dialog captures it — and so
                // the panel is not left hanging over the answer.
                btn.onclick = menuAction
                    ? () => { closeMenu(); void run(btn, action); }
                    : () => { void run(btn, action); };
                btn.disabled = gate.busy();
                if (menuAction) {
                    menuActionsEl.append(btn);
                    inMenu += 1;
                } else if (action.slot === 'title') {
                    titleActionsEl.append(btn);
                } else {
                    actionsEl.append(btn);
                }
            });
            for (const [id, btn] of Array.from(actionNodes)) {
                if (wanted.has(id)) continue;
                btn.remove();
                actionNodes.delete(id);
            }
            // Appended last on every pass, so the `⋯` stays at the end of the
            // row however the actions before it churn — and only when there is
            // something behind it, because a menu with nothing in it is not a
            // menu. A conversation that loses its last menu action mid-open
            // takes the panel down with it rather than leaving an empty one.
            if (inMenu || viewOptions.length) actionsEl.append(menuButton);
            else {
                closeMenu();
                menuButton.remove();
            }
            BossModIcons.paintDocument('conversation-chrome');
            // The sweep above reaches the panel only while it is open, and an
            // apply() that runs then has just rebuilt the rows inside it —
            // Archive becoming Reopen is that case. Painting it explicitly
            // costs nothing when there is nothing left to paint (the painter is
            // idempotent) and is the difference between a glyph and a word.
            if (menu) BossModIcons.paint(menu.element, 'conversation-chrome.menu');
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
                // Same reason: the panel holds THIS conversation's actions, and
                // a menu left hanging over the next one is a control pointed at
                // something that is no longer on screen.
                closeMenu();
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
