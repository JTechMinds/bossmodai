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
 * text: which sections a pack carries, in what order, and what each panel
 * holds. The INTERACTION that picks one — the tab list's keyboard, its roving
 * tabindex and its hover intent — lives in section-reader.js, which the
 * Extensions detail reads its own sections through as well, so the two
 * details cannot come to behave differently.
 *
 * Every string it renders is the pack author's and reaches the document as a
 * text node through h(). There is no markup-string path in this file.
 */
const BossModMarketplaceSections = (() => {
    const { h } = BossModDom;

    const COPY = Object.freeze({ listLabel: 'Pack sections' });

    /**
     * The seven sections a pack can carry, in the order they are read.
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
        Object.freeze({
            id: 'communication', half: null, key: null,
            label: 'Communication', sub: 'How it talks',
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
     * @returns {Array<{spec: object, body?: string, tools?: string[],
     *   communication?: object}>}
     */
    function available(item) {
        // ABSENT — not `[]` — on a pack that lists no tools, and an installed
        // row carries `[]` for the same fact, so the test is length and both
        // answers land in the same place.
        const tools = Array.isArray(item.toolsHint) ? item.toolsHint.filter(Boolean) : [];
        const communication = item.communication && typeof item.communication === 'object'
            ? item.communication
            : null;
        return SECTIONS.map((spec) => {
            if (spec.id === 'tools') return tools.length ? { spec, tools } : null;
            if (spec.id === 'communication') {
                return communication ? { spec, communication } : null;
            }
            const body = sectionText(item.sections, spec.half, spec.key);
            return body ? { spec, body } : null;
        }).filter(Boolean);
    }

    // One chip per tool. A `·` between them would be CSS-generated content,
    // which a screen reader announces as a character with no meaning; a list of
    // chips says the same thing and says it once.
    function panelBody(entry) {
        if (entry.tools) {
            return h('ul', { class: 'market-detail-tools' },
                entry.tools.map((tool) => h('li', {}, tool)));
        }
        if (entry.communication) {
            return h('ul', { class: 'market-detail-tools' },
                ['tone', 'density', 'jargon', 'audience'].map((key) => (
                    h('li', {}, `${key}: ${entry.communication[key]}`)
                )));
        }
        return h('p', { class: 'market-detail-text' }, entry.body);
    }

    /**
     * The two-column reader: the sections this pack carries, and the one open.
     *
     * The list and its keyboard, hover and focus are section-reader.js's; this
     * hands it the pack's sections as entries. The ids keep their
     * `market-section` prefix, so the markup is what it was before the
     * interaction moved out.
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
        const entries = available(item).map((entry) => ({
            id: entry.spec.id, label: entry.spec.label, sub: entry.spec.sub, body: panelBody(entry),
        }));
        return BossModSectionReader.render({
            idPrefix: 'market-section',
            label: COPY.listLabel,
            entries,
            selectedId: state.sectionKey,
            onSelect: (id, focus) => handlers.onSection(id, focus),
        });
    }

    return { COPY, SECTIONS, sectionText, available, reader };
})();
