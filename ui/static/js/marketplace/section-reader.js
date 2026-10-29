/**
 * BossMod AI — a vertical list of sections beside the one panel being read.
 *
 * Two details read this way: a pack's contract in the marketplace
 * (marketplace-sections.js turns a pack's sections into entries) and an
 * extension's Status / Connection / Settings / Agents / About in the
 * Extensions view (extensions/extension-detail.js). The interaction was built
 * for the first and moved here unchanged when the second needed it, so the two
 * details cannot come to behave differently. The caller decides WHAT the
 * entries are; this owns HOW one is picked.
 *
 * Keyboard first, WAI-ARIA's vertical tabs exactly: roving tabindex, Up and
 * Down move the selection and take the focus with them, Home and End go to the
 * ends, and the panel is labelled by the tab that opened it. Hover is layered
 * ON TOP of that and never replaces it — it selects only after the pointer has
 * rested, so crossing the list on the way to the panel does not strobe it, and
 * it never moves focus. Touch and keyboard reach every section without it.
 *
 * The classes keep the `market-` prefix they were defined under
 * (marketplace.css), the precedent pack-card.js documents: the prefix says
 * where the vocabulary was defined, not who may spend it.
 *
 * Labels and subtitles reach the document as text nodes through h(); each
 * panel body is a node the caller built. There is no markup-string path here.
 */
const BossModSectionReader = (() => {
    const { h } = BossModDom;

    // How long the pointer has to REST on an entry before it selects it.
    // Dragging the pointer down the list on its way to the panel crosses every
    // entry in a few milliseconds, and a panel that repaints four times on the
    // way past is a panel nobody can read.
    const HOVER_DELAY_MS = 120;

    // The pending hover intent, and there is only ever one: arming a second
    // entry cancels the first, which is what makes a pointer dragged down the
    // list select the entry it STOPS on rather than every entry it crossed.
    let hoverTimer = null;

    function cancelHover() {
        if (hoverTimer === null) return;
        clearTimeout(hoverTimer);
        hoverTimer = null;
    }

    /**
     * Select after the pointer has rested on an entry, and not before.
     *
     * @param {object} entry  The entry the pointer is over.
     * @param {boolean} selected  Whether its panel is already up.
     * @param {HTMLElement} node  The tab itself, checked when the timer fires.
     * @param {object} config  The render config; reads `onSelect`.
     * @returns {void}
     */
    function armHover(entry, selected, node, config) {
        cancelHover();
        // The pointer resting on the entry that is already up has nothing to
        // select, and selecting it anyway rebuilds the very node the pointer is
        // standing on — which a browser answers with a fresh mouseenter on the
        // replacement. That is a loop, not a selection.
        if (selected) return;
        hoverTimer = setTimeout(() => {
            hoverTimer = null;
            // The list was rebuilt or torn down while the pointer rested — a
            // finished install, `‹ Back`, or Esc. A timer must not reach past
            // the surface that armed it.
            if (!document.body.contains(node)) return;
            // No focus selector: a pointer must never take the keyboard off
            // whatever the operator left it on.
            config.onSelect(entry.id, null);
        }, HOVER_DELAY_MS);
    }

    // One way in for every deliberate selection — a click, the arrows and both
    // ends — so a pending hover cannot land on top of the choice just made.
    function choose(entry, focus, config) {
        cancelHover();
        config.onSelect(entry.id, focus);
    }

    const STEP = Object.freeze({ ArrowDown: 1, ArrowUp: -1 });

    // Automatic activation: the arrows move the selection and the panel
    // together, because the panel is already built and there is nothing to
    // wait for. Up and Down WRAP — a handful of entries at most, which is short
    // enough that stopping dead at the end is a worse answer than arriving at
    // the other one. `at` is the index of the tab the key came FROM, not the
    // selected one: after a hover has moved the selection out from under the
    // keyboard, the arrows still step from where the focus actually is.
    function move(event, at, entries, config) {
        let next = null;
        if (Object.prototype.hasOwnProperty.call(STEP, event.key)) {
            next = (at + STEP[event.key] + entries.length) % entries.length;
        } else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = entries.length - 1;
        if (next === null) return;
        event.preventDefault();
        choose(entries[next], `#${config.idPrefix}-${next}`, config);
    }

    // A real tab: `aria-selected` says which one is up, the roving tabindex
    // makes the whole list ONE tab stop, and `aria-controls` is published only
    // by the selected tab because the others have no panel in the document to
    // point at. The tabs are NUMBERED, not named after their entry: the rail
    // and the cards number their rows and a fourth convention here would be
    // one to remember for nothing.
    function tab(entry, at, selected, entries, config) {
        const id = `${config.idPrefix}-${at}`;
        return h('button', {
            class: 'market-section-tab', type: 'button', id, role: 'tab',
            'aria-selected': selected ? 'true' : 'false',
            'aria-controls': selected ? `${config.idPrefix}-panel` : null,
            tabindex: selected ? '0' : '-1',
            onclick: () => choose(entry, `#${id}`, config),
            onkeydown: (event) => move(event, at, entries, config),
            onmouseenter: (event) => armHover(entry, selected, event.target, config),
            onmouseleave: () => cancelHover(),
        },
        h('span', { class: 'market-section-name' }, entry.label),
        h('span', { class: 'market-section-sub' }, entry.sub));
    }

    /**
     * The two-column reader: the list of entries, and the one open.
     *
     * @param {object} config
     * @param {string} config.idPrefix  Tabs are `${idPrefix}-${n}` and the
     *   panel `${idPrefix}-panel`; one reader is mounted per prefix at a time.
     * @param {string} config.label  The tab list's accessible name.
     * @param {Array<{id: string, label: string, sub: string, body: Node}>} config.entries
     *   In reading order. `id` is what `onSelect` reports; `label` over `sub`
     *   is one row of the list; `body` is the node the panel holds while the
     *   entry is up (re-parented, never copied, so a caller may hand over the
     *   same live node on every render).
     * @param {string|null} config.selectedId  The entry being read. Null, or
     *   an id no longer among the entries, reads the first — the default for
     *   a detail just opened, not a rescue.
     * @param {(id: string, focusSelector: (string|null)) => void} config.onSelect
     *   Called with the chosen entry's id and the tab to land on: its own id
     *   after a click, the arrived-at one after an arrow key, and null for a
     *   hover, because a pointer must never take the keyboard.
     * @returns {HTMLElement|null} Null when there are no entries, so the caller
     *   draws neither an empty reader nor a rule above one.
     * @throws {Error} Without an idPrefix, a label or an onSelect, or for an
     *   entry without an id, a label or a body: a tab that names nothing or
     *   opens nothing is a dead control, and a silent one.
     */
    function render(config) {
        const { idPrefix, label, entries, selectedId } = config || {};
        if (!idPrefix) throw new Error('[section-reader] config.idPrefix is required');
        if (!label) throw new Error('[section-reader] config.label is required');
        if (typeof (config && config.onSelect) !== 'function') {
            throw new Error('[section-reader] config.onSelect is required');
        }
        if (!Array.isArray(entries)) throw new Error('[section-reader] config.entries must be an array');
        entries.forEach((entry, index) => {
            if (!entry || !entry.id || !entry.label || !entry.body) {
                throw new Error(`[section-reader] entry ${index} needs an id, a label and a body`);
            }
        });
        if (!entries.length) return null;
        const held = entries.findIndex((entry) => entry.id === selectedId);
        const at = held === -1 ? 0 : held;
        return h('div', { class: 'market-detail-read' },
            h('div', {
                class: 'market-section-list', role: 'tablist',
                'aria-orientation': 'vertical', 'aria-label': label,
            }, entries.map((entry, index) => tab(entry, index, index === at, entries, config))),
            // `tabindex="0"`: the panel scrolls and may hold no control of its
            // own, so without a tab stop its content is unreachable by keyboard
            // the moment it is taller than the box.
            h('div', {
                class: 'market-section-panel', id: `${idPrefix}-panel`, role: 'tabpanel',
                tabindex: '0', 'aria-labelledby': `${idPrefix}-${at}`,
            }, entries[at].body));
    }

    return { HOVER_DELAY_MS, render };
})();
