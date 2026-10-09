/**
 * BossMod AI — the conversation header's `⋯`: its button and the panel behind it.
 *
 * THE SEAM. conversation/chrome.js decides WHAT goes behind the `⋯` — it
 * `place()`s the source's `slot: 'menu'` actions on every apply(), and decides
 * whether the button is shown at all. This module owns WHAT the panel is:
 * its sections, their headings, and painting glyphs inside a panel the
 * document sweep cannot reach. The `⋯` itself — its look, its expanded state,
 * opening and closing — is core/menu-button.js's, the one every `⋯` shares.
 * The chrome never touches the panel; this module never reads a descriptor.
 *
 * TWO SECTIONS, each a core/menu.js `createMenuSection`: `subject` — the agent
 * or the thread, titled by the source's word through `setTitle` — and `chat`,
 * this conversation's history and settings, titled by the surface's own word.
 * Inside each, actions paint above settings: the doing comes first.
 *
 * Every node is STABLE, owned here and built once, rather than handed to the
 * menu at open time, which is what lets a repaint that happens while the
 * panel is open — Archive succeeding and becoming Reopen — land inside the
 * panel the operator is looking at.
 */
const BossModChromeMenu = (() => {
    const { h } = BossModDom;

    /** The `⋯`'s accessible name and its tooltip: one string, never two. */
    const MENU_LABEL = 'More actions';
    /** The chat section's heading: the surface's word, not any source's. */
    const CHAT_LABEL = 'Chat';
    /**
     * The subject heading until the first apply() names it. Never seen: the
     * section shows only with an action in it, and chrome.js refuses a
     * subject action without the descriptor's `menuTitle`.
     */
    const SUBJECT_PLACEHOLDER = 'Conversation';

    /**
     * Build the `⋯` and the sectioned panel it opens.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.container  The positioned header the panel
     *   hangs off. The panel is placed relative to it, and a panel hung off an
     *   unpositioned ancestor lands below the viewport.
     * @param {HTMLElement[]} [deps.viewOptions]  The surface's preference
     *   controls, placed once, last in the chat section's settings.
     * @returns {{ button: HTMLElement,
     *             place: (section: 'subject'|'chat', kind: string, node: HTMLElement) => void,
     *             setTitle: (text: string) => void, settle: () => void,
     *             close: () => void, paint: () => void }}
     *   `button` is the `⋯` the chrome places in its row. `place` appends a
     *   node to a section — a `kind === 'switch'` to its settings, anything
     *   else to its actions — and throws on an unknown section. `setTitle`
     *   writes the subject heading. `settle` shows only the sections with
     *   something in them, and the divider only between two. `close` puts an
     *   open panel away and `paint` paints glyphs inside it (both no-ops when
     *   closed).
     * @throws {Error} When `container` is missing — the panel would have no
     *   positioned host to hang off.
     */
    function createChromeMenu(deps) {
        const container = deps && deps.container;
        if (!container) throw new Error('[chrome-menu] deps.container is required');
        const viewOptions = (deps && deps.viewOptions) || [];

        /**
         * Each section's two bodies, owned here and reused forever.
         *
         * Built once and never replaced, for the same reason the view options
         * are the caller's nodes rather than rebuilt on open: they may be
         * inside an open panel when apply() runs, and refilling a stable node
         * is what keeps a live chrome swap visible to whoever is looking at
         * it. While the panel is closed they are simply detached.
         */
        const bodies = {
            subject: { actions: h('div', { class: 'menu-actions' }), settings: h('div', { class: 'menu-actions' }) },
            chat: { actions: h('div', { class: 'menu-actions' }), settings: h('div', { class: 'menu-actions' }) },
        };
        const sections = {
            subject: BossModMenu.createMenuSection({
                id: 'conversation-menu-subject',
                label: SUBJECT_PLACEHOLDER,
                children: [bodies.subject.actions, bodies.subject.settings],
            }),
            chat: BossModMenu.createMenuSection({
                id: 'conversation-menu-chat',
                label: CHAT_LABEL,
                children: [bodies.chat.actions, bodies.chat.settings],
            }),
        };
        const subjectLabel = sections.subject.querySelector('.menu-label');
        const divider = h('hr', { class: 'menu-divider' });
        /** What the panel holds: the sections in view, settled after each apply. */
        const content = h('div', { class: 'menu-sections' });
        // The surface's preferences are the chat's settings, and they are the
        // caller's nodes: placed once, and kept last by place().
        bodies.chat.settings.append(...viewOptions);
        // The panel holds a role="switch", which is not a menuitem — hence
        // the trigger's `aria-haspopup="dialog"`.
        const menu = BossModMenuButton.create({
            id: 'conversation-view-options',
            label: MENU_LABEL,
            size: 'header',
            getContainer: () => container,
            // The sections, settled by the last apply(); see settle().
            getItems: () => [content],
            // Paint what was just attached; see paint().
            onOpen: () => paint(),
        });

        /**
         * Put one node into a section, after what is already there.
         *
         * A chat switch goes in ahead of the view options rather than after
         * them, so they stay last without being moved on every apply().
         *
         * @param {'subject'|'chat'} section
         * @param {string} kind  `'switch'` for a setting; anything else is an
         *   action.
         * @param {HTMLElement} node
         * @returns {void}
         * @throws {Error} On an unknown section — a control with nowhere to go.
         */
        function place(section, kind, node) {
            const body = Object.prototype.hasOwnProperty.call(bodies, section) ? bodies[section] : null;
            if (!body) throw new Error(`[chrome-menu] unknown menu section "${section}"`);
            if (kind !== 'switch') {
                body.actions.append(node);
                return;
            }
            const first = section === 'chat' && viewOptions.length ? viewOptions[0] : null;
            body.settings.insertBefore(node, first);
        }

        /**
         * Write the subject section's heading — the source's word for what
         * the conversation is with (`Agent`, `Thread`).
         * @param {string} text
         * @returns {void}
         */
        function setTitle(text) {
            subjectLabel.textContent = text;
        }

        /**
         * Show only the sections with something in them, and the divider
         * only between two — an empty heading is not a section. The panel is
         * left alone when nothing changed, so a repaint does not move nodes
         * under the operator's pointer.
         * @returns {void}
         */
        function settle() {
            const filled = (key) => bodies[key].actions.children.length > 0
                || bodies[key].settings.children.length > 0;
            const wanted = [];
            if (filled('subject')) wanted.push(sections.subject);
            if (filled('subject') && filled('chat')) wanted.push(divider);
            if (filled('chat')) wanted.push(sections.chat);
            const current = Array.from(content.children);
            const same = current.length === wanted.length
                && wanted.every((node, index) => current[index] === node);
            if (!same) content.replaceChildren(...wanted);
        }

        /**
         * THE PANEL IS THE ONE TREE THE SWEEP CANNOT REACH. The chrome's apply()
         * ends on BossModIcons.paintDocument, which walks document.body — and
         * while the menu is closed its sections hang off a detached node, so an
         * action built in there kept its bare `<i>` placeholder and rendered as
         * a bare word. Archive read as a stray heading in the panel because of
         * exactly this. The painter is idempotent, so painting an open panel
         * that has nothing left to paint costs nothing.
         *
         * @returns {void}
         */
        function paint() {
            const panel = menu.openElement();
            if (panel) BossModIcons.paint(panel, 'conversation-chrome.menu');
        }

        return { button: menu.button, place, setTitle, settle, close: menu.close, paint };
    }

    return { createChromeMenu };
})();
