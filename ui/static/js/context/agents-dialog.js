/**
 * BossMod AI — the Agents dialog: Add agent and the Marketplace, as two tabs.
 *
 * They were two modals that looked almost identical — the same pack cards,
 * the same filter rail — and the only way between them was to close one and
 * open the other. `Browse marketplace` closed Add agent and opened the
 * marketplace takeover, whose `✕` then reopened Add agent behind it: the one
 * `✕` in the app that meant "back" rather than "close everything", and a
 * marketplace opened from the rail menu had no road to Add agent at all.
 *
 * ONE DIALOG, two panes, and a tab group in its head — the Office header's
 * quiet Map | Org, which is core/tabs.js now — where the frame keeps its tools,
 * right of the title and before the `✕`. Both of the rail menu's doors open
 * this same dialog on their own tab. The PANES are
 * context/agent-add-pane.js (the picker, then the form) and
 * marketplace/marketplace.js's pane; neither has a modal of its own, and this
 * owns only the frame, the tabs, which pane is up, and the two wires between
 * them: "Add agent from this" in the marketplace switches to Add agent and
 * starts the form from that template, and a library write on either side — an
 * install, an uninstall, or a form saved as a template — re-reads the other.
 *
 * A pane that is away is HIDDEN, never destroyed: the picker's filter, the
 * marketplace's scroll and open pack, and a half-typed form all survive a tab
 * switch. The Add agent pane can finish work while it is away — a build, a
 * failure, a save — which is why its footer suspends rather than being torn
 * down (context/agent-dialog-footer.js).
 *
 * ONE AGENT FORM AT A TIME: this and the Edit role dialog both host
 * `#agent-form`, so both take context/agent-dialog-slot.js before they open.
 */
const BossModAgentsDialog = (() => {
    const { h } = BossModDom;
    const SLOT = BossModAgentDialogSlot;

    const TITLE = 'Agents';

    /**
     * The mark each of the dialog's two errands wears, wherever it is offered:
     * the rail menu's door into it (shell/add-agent-menu.js) and its tab here.
     * One definition, so a door and the tab it opens can never come to wear
     * different icons.
     *
     * `blocks`, never `building`, for the Marketplace: shell/places.js already
     * spends that mark on the Office, and two places in one shell wearing the
     * same icon is an icon that identifies neither. A drawn `plus` for Add
     * agent, not a typed `+`: a character has the metrics of whatever font
     * renders it while an icon has the icon's, and labels beside the two have
     * to share one left edge.
     */
    const ICONS = Object.freeze({ add: 'plus', marketplace: 'blocks' });

    /** Left to right in the head. Each panel is named by its tab. */
    const TABS = Object.freeze([
        Object.freeze({
            id: 'add', label: 'Add agent', panelId: 'agents-panel-add', icon: ICONS.add,
        }),
        Object.freeze({
            id: 'marketplace', label: 'Marketplace', panelId: 'agents-panel-marketplace',
            icon: ICONS.marketplace,
        }),
    ]);

    /**
     * Every handle this module has handed out. The slot holds whichever agent
     * dialog is open, and only one of those two kinds can be asked to switch
     * tabs; a WeakSet answers which kind without a second record of "the open
     * one" that could come to disagree with the slot.
     */
    const OWN = new WeakSet();

    /**
     * Open the Agents dialog on one of its two tabs.
     *
     * Opened while it is already open, it switches that one to `tab` and hands
     * it back. Opened while the Edit role dialog is up, it hands THAT back and
     * opens nothing: one agent form at a time.
     *
     * @param {object} deps
     * @param {object} deps.store  Application store. A create writes the new
     *   agent's conversation and desk into it.
     * @param {'add'|'marketplace'} deps.tab  The tab to open on — the menu
     *   door the operator used.
     * @returns {{close: () => void, select: (tab: string) => void}|{close: () => void}}
     *   This dialog's handle — `close` it, or `select` a tab as if it had been
     *   chosen — or, when the Edit role dialog holds the slot, that dialog's.
     * @throws {Error} When the store is missing, or `tab` is not one of the
     *   two: a dialog opened on no tab has no pane to show.
     */
    function open(deps) {
        const { store, tab } = deps || {};
        if (!store) throw new Error('[agents-dialog] deps.store is required');
        if (!TABS.some((entry) => entry.id === tab)) {
            throw new Error(`[agents-dialog] unknown tab "${tab}"; expected add or marketplace`);
        }
        const held = SLOT.current();
        if (held) {
            if (OWN.has(held)) held.select(tab);
            return held;
        }

        const addPane = BossModAgentAddPane.create({
            store,
            onBrowse: () => selectTab('marketplace'),
            onDone: () => modal.close(),
            // A form saved as a template is a row BOTH panes list: the picker
            // offers it on the next create, and the Marketplace shows it under
            // Installed. Neither re-reads on its own, so the dialog says so.
            onTemplateSaved: () => {
                void addPane.refresh();
                void market.refreshLibrary();
            },
        });
        const market = BossModMarketplace.createPane({
            // The bridge. The tab switches FIRST, so the pane is live — its
            // footer resumed, its focus rules on — before the pick lands in
            // it. A different template than the draft on screen replaces the
            // draft, which is the picker's own rule; the same one keeps it.
            onUseTemplate: (template) => {
                selectTab('add');
                void addPane.pick({ kind: 'template', row: template });
            },
            onLibraryChanged: () => { void addPane.refresh(); },
        });

        /**
         * One pane's panel. `hidden` is set as a PROPERTY, and before the
         * dialog opens, so createModal's opening focus skips the pane that is
         * not up.
         */
        function panel(entry, content) {
            const node = h('div', {
                class: 'agents-panel', role: 'tabpanel', id: entry.panelId,
                'aria-labelledby': `agents-tab-${entry.id}`,
            }, content);
            node.hidden = entry.id !== tab;
            return node;
        }
        const panels = {
            add: panel(TABS[0], addPane.element),
            marketplace: panel(TABS[1], market.element),
        };

        /**
         * Put one pane up and the other away. What the tab group reports on
         * the operator's choice, and the second half of `selectTab`.
         *
         * @param {'add'|'marketplace'} id
         * @returns {void}
         */
        function showTab(id) {
            panels.add.hidden = id !== 'add';
            panels.marketplace.hidden = id !== 'marketplace';
            if (id === 'add') {
                addPane.activate();
                return;
            }
            addPane.deactivate();
            // Lazy: the catalog is a remote read, made the first time this
            // tab is actually looked at and never again after.
            market.activate();
        }

        const tabs = BossModTabs.create({
            label: TITLE,
            idPrefix: 'agents-tab',
            tabs: TABS,
            selected: tab,
            onSelect: showTab,
        });

        /**
         * Switch tabs from code — the empty picker's door, "Add agent from
         * this", or a second open on another tab. The control that asked is
         * in the pane just put away, so the keyboard goes to the tab that is
         * now selected: somewhere visible, and the next Tab reaches the pane.
         *
         * @param {'add'|'marketplace'} id
         * @returns {void}
         * @throws {Error} From BossModTabs, for an id the group does not hold.
         */
        function selectTab(id) {
            tabs.select(id);
            showTab(id);
            tabs.focus();
        }

        const handle = { close: () => modal.close(), select: selectTab };
        OWN.add(handle);

        const modal = BossModOverlays.createModal({
            title: TITLE,
            body: h('div', { class: 'agents-body' }, panels.add, panels.marketplace),
            // The Add agent pane's `‹` back to its picker, on the title row.
            // Hidden until its form step is up, and whenever its tab is away.
            lead: addPane.lead,
            // Where the Office keeps Map | Org: the right end of the head.
            tools: [tabs.element],
            // One size for both tabs, so a tab switch never resizes the box
            // under the pointer. The form is held to a measure inside it
            // (overlays.css), so the wider frame does not sprawl it.
            size: 'takeover',
            // NO footer of the dialog's own. The picker has none and neither
            // does the Marketplace; the form step's `Cancel` / `Create Agent`
            // is the Add agent pane's footer, which it suspends while away.
            // The frame's `✕` is the exit on every tab.
            actions: [],
            // An outside click closes it — until a form has landed. A stray
            // click must not throw a draft away.
            closeOnBackdrop: () => !addPane.holdsDraft(),
            onClose: () => {
                addPane.dispose();
                SLOT.release(handle);
            },
        });
        // Taken the moment the dialog exists, so nothing between here and the
        // end of this function can leave an open form the slot does not know.
        SLOT.claim(handle);
        // Read only by the stylesheet: the search fields' width and the form's
        // measure are this dialog's, not every takeover's.
        modal.element.setAttribute('data-dialog', 'agents');
        addPane.attach(modal);
        // The chevron, the two tabs' marks and both panes' magnifiers are
        // lucide placeholders until the panel is mounted, and createModal has
        // just mounted it. Scoped to this panel, never the document.
        BossModIcons.paint(modal.element, 'agents-dialog');
        showTab(tab);
        // The picker owns its own loading, empty, failed and ready states, so
        // nothing here awaits it.
        void addPane.refresh();
        return handle;
    }

    return { ICONS, open };
})();
