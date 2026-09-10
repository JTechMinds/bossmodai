/**
 * BossMod AI — one pack's contract, read one section at a time.
 *
 * The detail view used to stack every section down the page, each one fenced
 * off from the last by a rule: six horizontal lines to read six paragraphs,
 * and no way to see what the pack said about scope without scrolling past what
 * it said about handoff. This is the replacement — a vertical TAB LIST of the
 * sections the pack carries beside a panel holding the one being read.
 *
 * Its own module rather than more of marketplace-detail.js, and the seam is
 * real: that file owns the chrome around a pack — the back bar, the hero, the
 * byline, the install and uninstall it offers — and this owns the pack's own
 * text and the control that picks which of it is up. It is also the only part
 * of the marketplace with an INTERACTION of its own rather than a click that
 * hands straight back to the state module, which is the second reason it is
 * not spread through a view.
 *
 * Keyboard first, WAI-ARIA's vertical tabs exactly: roving tabindex, Up and
 * Down move the selection and take the focus with them, Home and End go to the
 * ends, and the panel is labelled by the tab that opened it. Hover is layered
 * ON TOP of that and never replaces it — it selects only after the pointer has
 * rested, so crossing the list on the way to the panel does not strobe it, and
 * it never moves focus. Touch and keyboard reach every section without it.
 *
 * Every string it renders is the pack author's and reaches the document as a
 * text node through h(). There is no markup-string path in this file.
 */
const BossModMarketplaceSections = (() => {
    const { h } = BossModDom;

    // The one panel every tab drives. One pack is mounted at a time, so this
    // id names exactly one node — the same rule `#market-close` is built on.
    const PANEL_ID = 'market-section-panel';

    // How long the pointer has to REST on an entry before it selects it.
    // Dragging the pointer down the list on its way to the panel crosses every
    // entry in a few milliseconds, and a panel that repaints four times on the
    // way past is a panel nobody can read.
    const HOVER_DELAY_MS = 120;

    const COPY = Object.freeze({ listLabel: 'Pack sections' });

    /**
     * The six sections a pack can carry, in the order they are read.
     *
     * `id` is what the state holds, and it is OURS: a section id ends up in a
     * selector, and catalog text has no business in one. `half` and `key` name
     * where the body lives in the server's parse — the description splits into
     * the mission and the three contract sections, `what_done_looks_like` into
     * the bar and its counter-examples. `tools` has neither, because it is the
     * row's own `tools_hint` list rather than a paragraph.
     *
     * The label and the subtitle are frozen together because they are one row
     * of the list: the name says which section it is, the line under it says
     * what reading it will tell you. Split across two tables they would drift.
     */
    const SECTIONS = Object.freeze([
        Object.freeze({
            id: 'in-scope', half: 'description', key: 'in_scope',
            label: 'In scope', sub: 'What it takes on',
        }),
        Object.freeze({
            id: 'out-of-scope', half: 'description', key: 'out_of_scope',
            label: 'Out of scope', sub: 'What it won’t do',
        }),
        Object.freeze({
            id: 'handoff', half: 'description', key: 'handoff',
            label: 'Handoff', sub: 'Who gets the result',
        }),
        Object.freeze({
            id: 'done', half: 'done', key: 'preamble',
            label: 'Done looks like', sub: 'The bar it must clear',
        }),
        Object.freeze({
            id: 'fail-examples', half: 'done', key: 'fail_examples',
            label: 'Fail examples', sub: 'What doesn’t count',
        }),
        Object.freeze({
            id: 'tools', half: null, key: null,
            label: 'Tools', sub: 'What it expects to use',
        }),
    ]);

    /**
     * One section body out of the server's parsed `sections`, or null.
     *
     * Two absences are real and both answer the same way, because the caller's
     * question is "is there something to render here": a single section is
     * `null` when the text carried no such heading, and a section present but
     * blank is nothing to show either. The `sections` object itself is no
     * longer one of them — marketplace-items.js requires it and says so loudly
     * — and this stays null-tolerant only as its exported contract.
     *
     * @param {object|null} sections  The API's `sections`, or null.
     * @param {'description'|'done'} half
     * @param {string} key  `in_scope`, `preamble`, `fail_examples`, …
     * @returns {string|null} The body verbatim, or null. Never `''`, so a
     *   caller can branch on truthiness and never render an empty block.
     */
    function sectionText(sections, half, key) {
        const group = sections ? sections[half] : null;
        const value = group ? group[key] : null;
        return typeof value === 'string' && value.trim() ? value : null;
    }

    /**
     * The sections this pack actually carries, in reading order.
     *
     * A tab with nothing behind it claims the pack answered a question it never
     * answered, and a panel that opens empty is worse: it reads as a pack that
     * said nothing rather than as a pack that was never asked. So a section the
     * server returned as null produces no entry at all, and a pack that carries
     * none produces an empty list — which is how the caller knows to draw
     * neither the reader nor the rule above it.
     *
     * @param {object} item  A marketplace-items.js projection. Reads `sections`
     *   and `toolsHint`.
     * @returns {Array<{spec: object, body?: string, tools?: string[]}>}
     */
    function available(item) {
        // ABSENT — not `[]` — on a pack that lists no tools, and an installed
        // row carries `[]` for the same fact, so the test is length and both
        // answers land in the same place.
        const tools = Array.isArray(item.toolsHint) ? item.toolsHint.filter(Boolean) : [];
        return SECTIONS.map((spec) => {
            if (spec.id === 'tools') return tools.length ? { spec, tools } : null;
            const body = sectionText(item.sections, spec.half, spec.key);
            return body ? { spec, body } : null;
        }).filter(Boolean);
    }

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
     * @param {object} handlers  Reads `onSection`.
     * @returns {void}
     */
    function armHover(entry, selected, node, handlers) {
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
            handlers.onSection(entry.spec.id, null);
        }, HOVER_DELAY_MS);
    }

    // One way in for every deliberate selection — a click, the arrows and both
    // ends — so a pending hover cannot land on top of the choice just made.
    function choose(entry, focus, handlers) {
        cancelHover();
        handlers.onSection(entry.spec.id, focus);
    }

    const STEP = Object.freeze({ ArrowDown: 1, ArrowUp: -1 });

    // Automatic activation: the arrows move the selection and the panel
    // together, because the panel is already built and there is nothing to
    // wait for. Up and Down WRAP — six entries at the very most, which is short
    // enough that stopping dead at the end is a worse answer than arriving at
    // the other one. `at` is the index of the tab the key came FROM, not the
    // selected one: after a hover has moved the selection out from under the
    // keyboard, the arrows still step from where the focus actually is.
    function move(event, at, entries, handlers) {
        let next = null;
        if (Object.prototype.hasOwnProperty.call(STEP, event.key)) {
            next = (at + STEP[event.key] + entries.length) % entries.length;
        } else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = entries.length - 1;
        if (next === null) return;
        event.preventDefault();
        choose(entries[next], `#market-section-${next}`, handlers);
    }

    // A real tab: `aria-selected` says which one is up, the roving tabindex
    // makes the whole list ONE tab stop, and `aria-controls` is published only
    // by the selected tab because the others have no panel in the document to
    // point at. The entries are NUMBERED — the section ids are ours, but the
    // rail and the cards number their rows and a fourth convention here would
    // be one to remember for nothing.
    function tab(entry, at, selected, entries, handlers) {
        const id = `market-section-${at}`;
        return h('button', {
            class: 'market-section-tab', type: 'button', id, role: 'tab',
            'aria-selected': selected ? 'true' : 'false',
            'aria-controls': selected ? PANEL_ID : null,
            tabindex: selected ? '0' : '-1',
            onclick: () => choose(entry, `#${id}`, handlers),
            onkeydown: (event) => move(event, at, entries, handlers),
            onmouseenter: (event) => armHover(entry, selected, event.target, handlers),
            onmouseleave: () => cancelHover(),
        },
        h('span', { class: 'market-section-name' }, entry.spec.label),
        h('span', { class: 'market-section-sub' }, entry.spec.sub));
    }

    // One chip per tool. A `·` between them would be CSS-generated content,
    // which a screen reader announces as a character with no meaning; a list of
    // chips says the same thing and says it once.
    function panelBody(entry) {
        if (entry.tools) {
            return h('ul', { class: 'market-detail-tools' },
                entry.tools.map((tool) => h('li', {}, tool)));
        }
        return h('p', { class: 'market-detail-text' }, entry.body);
    }

    /**
     * The two-column reader: the sections this pack carries, and the one open.
     *
     * @param {object} item  The projected pack.
     * @param {object} state  Reads `sectionKey`, the id of the section being
     *   read. Null — a pack just opened — reads the first section the pack
     *   carries. That is the DEFAULT, not a rescue: a pack whose sections have
     *   changed under a held id lands there too, and landing on the first thing
     *   the pack says is the same answer either way.
     * @param {object} handlers  Reads `onSection(id, focusSelector)`.
     * @returns {HTMLElement|null} Null when the pack carries no section at all,
     *   so the caller draws neither an empty reader nor a rule above one.
     */
    function reader(item, state, handlers) {
        const entries = available(item);
        if (!entries.length) return null;
        const held = entries.findIndex((entry) => entry.spec.id === state.sectionKey);
        const at = held === -1 ? 0 : held;
        return h('div', { class: 'market-detail-read' },
            h('div', {
                class: 'market-section-list', role: 'tablist',
                'aria-orientation': 'vertical', 'aria-label': COPY.listLabel,
            }, entries.map((entry, index) => tab(entry, index, index === at, entries, handlers))),
            // `tabindex="0"`: the panel scrolls and holds no control of its
            // own, so without a tab stop its content is unreachable by keyboard
            // the moment it is taller than the box.
            h('div', {
                class: 'market-section-panel', id: PANEL_ID, role: 'tabpanel',
                tabindex: '0', 'aria-labelledby': `market-section-${at}`,
            }, panelBody(entries[at])));
    }

    return { COPY, SECTIONS, HOVER_DELAY_MS, sectionText, available, reader };
})();
