/**
 * BossMod AI — what the Extensions view says about each extension.
 *
 * The projection from the server's extension list items
 * (api/routes/extensions.py `_item`) to what the browse view and the detail
 * print: a card's chip and its tint, the rail's rows and counts, which filter
 * keeps which items, which sections a detail reads, and the facts its About
 * section lists. Pure functions over the server's item, with no DOM except
 * `markFor`, which builds the card's and the hero's bubble — so the view, the
 * detail and the controller read one answer to each question.
 *
 * Everything here is driven by the item and its manifest flags. It names no
 * extension, category or author: those are each extension's own manifest
 * values, and a slug becomes a label through the marketplace's one derivation
 * (BossModMarketplaceItems.categoryLabel), so a category reads the same way on
 * both surfaces.
 */
const BossModExtensionItems = (() => {
    // A rail row's id for a category: the prefix keeps a slug such as `on`
    // or `all` from colliding with a scope row.
    const CATEGORY_PREFIX = 'category:';

    const COPY = Object.freeze({
        scopeGroup: 'Show',
        categoryGroup: 'Categories',
        all: 'All',
        on: 'On',
        unavailable: 'Unavailable',
        couldNotStart: 'Couldn’t start',
        downloading: 'Downloading…',
        setupFailed: 'Setup failed',
        command: 'Agent command',
        liveView: 'Live view',
        liveViewWhere: (label) => `${label}, from each agent’s chat header`,
        agentView: 'Per-agent view',
        agentViewWhere: (label) => `${label}, from each agent’s desk`,
        needs: 'Needs',
        needsImages: 'A model that can see images',
        setup: 'Setup',
        extensionId: 'Extension ID',
    });

    /**
     * The sections a detail can read, in reading order. Label and subtitle are
     * frozen together because they are one row of the section list.
     */
    const SECTIONS = Object.freeze({
        status: Object.freeze({ id: 'status', label: 'Status', sub: 'What it’s doing now' }),
        connection: Object.freeze({ id: 'connection', label: 'Connection', sub: 'Sign-in and models' }),
        settings: Object.freeze({ id: 'settings', label: 'Settings', sub: 'How it runs' }),
        agents: Object.freeze({ id: 'agents', label: 'Agents', sub: 'Who can use it' }),
        about: Object.freeze({ id: 'about', label: 'About', sub: 'What it adds' }),
    });

    /**
     * A valid item's setup state. The server sends one for every valid item,
     * so its absence is a contract break, said as one.
     *
     * @param {object} item
     * @returns {string}
     * @throws {Error} When a valid item carries no setup state.
     */
    function setupState(item) {
        if (!item.setup || !item.setup.state) {
            throw new Error(`[extension-items] "${item.id}" has no setup state`);
        }
        return item.setup.state;
    }

    /**
     * The card's chip: a STATE, never a control. First match wins.
     *
     * @param {object} item  A server item.
     * @returns {{label: string, tone: ('ok'|'attention'|null)}|null} Null for an
     *   extension that is off and fine — it carries no chip, like an
     *   uninstalled pack.
     */
    function chip(item) {
        if (!item.valid) return { label: COPY.unavailable, tone: 'attention' };
        if (item.enabled && item.service_error) return { label: COPY.couldNotStart, tone: 'attention' };
        const setup = setupState(item);
        if (setup === 'installing') return { label: COPY.downloading, tone: null };
        if (setup === 'failed') return { label: COPY.setupFailed, tone: 'attention' };
        if (item.enabled) return { label: COPY.on, tone: 'ok' };
        return null;
    }

    /**
     * The filter rail's two groups, with what each row would leave.
     *
     * @param {object[]} items  Every server item.
     * @returns {Array<{id: string, title: string, rows: Array<{id: string,
     *   label: string, count: number}>}>} `Show` (All / On), then `Categories`
     *   from the valid items in first-seen order. BossModFilterRail drops a
     *   group with no rows.
     * @throws {Error} When a valid item carries no category: the manifest
     *   requires one, so a missing one is a contract break, not a state.
     */
    function railGroups(items) {
        const order = [];
        const counts = new Map();
        items.filter((item) => item.valid).forEach((item) => {
            if (!item.category) throw new Error(`[extension-items] "${item.id}" has no category`);
            if (!counts.has(item.category)) {
                order.push(item.category);
                counts.set(item.category, 0);
            }
            counts.set(item.category, counts.get(item.category) + 1);
        });
        return [
            {
                id: 'scope',
                title: COPY.scopeGroup,
                rows: [
                    { id: 'all', label: COPY.all, count: items.length },
                    { id: 'on', label: COPY.on, count: items.filter((item) => item.enabled).length },
                ],
            },
            {
                id: 'category',
                title: COPY.categoryGroup,
                rows: order.map((slug) => ({
                    id: CATEGORY_PREFIX + slug,
                    label: BossModMarketplaceItems.categoryLabel(slug),
                    count: counts.get(slug),
                })),
            },
        ];
    }

    /**
     * The items a rail row keeps.
     *
     * @param {object[]} items
     * @param {string} filter  `'all'`, `'on'` or `'category:<slug>'`.
     * @returns {object[]} A new array; the list itself is never reordered.
     * @throws {Error} On any other filter: a row id this module did not hand
     *   out is a bug in the caller, and an empty grid would hide it.
     */
    function visible(items, filter) {
        if (filter === 'all') return items.slice();
        if (filter === 'on') return items.filter((item) => item.enabled);
        if (typeof filter === 'string' && filter.startsWith(CATEGORY_PREFIX)
            && filter.length > CATEGORY_PREFIX.length) {
            const slug = filter.slice(CATEGORY_PREFIX.length);
            return items.filter((item) => item.valid && item.category === slug);
        }
        throw new Error(`[extension-items] unknown filter "${filter}"`);
    }

    /**
     * The sections an extension's detail reads, in order.
     *
     * Driven ONLY by the manifest's flags, never by whether it is on, so the
     * tabs never jump when the switch flips; an off section says how to turn
     * it on instead.
     *
     * @param {object} item
     * @returns {Array<{id: string, label: string, sub: string}>} Empty for an
     *   invalid item, which has nothing to read but its reason.
     */
    function sectionsFor(item) {
        if (!item.valid) return [];
        const out = [];
        if (item.panel) out.push(SECTIONS.status);
        if (item.model_provider) out.push(SECTIONS.connection);
        if (item.settings) out.push(SECTIONS.settings);
        if (item.agent_config || item.requires_image_model) out.push(SECTIONS.agents);
        out.push(SECTIONS.about);
        return out;
    }

    /**
     * What the About section lists: only the facts the item has, then its id,
     * so About is never empty.
     *
     * @param {object} item
     * @returns {Array<{label: string, value: string}>} For BossModFactList.
     */
    function aboutFacts(item) {
        const facts = [];
        if (item.command) facts.push({ label: COPY.command, value: `${item.command.name} — ${item.command.summary}` });
        if (item.live_view) facts.push({ label: COPY.liveView, value: COPY.liveViewWhere(item.live_view.label) });
        if (item.agent_view) facts.push({ label: COPY.agentView, value: COPY.agentViewWhere(item.agent_view.label) });
        if (item.requires_image_model) facts.push({ label: COPY.needs, value: COPY.needsImages });
        if (item.setup_label) facts.push({ label: COPY.setup, value: item.setup_label });
        facts.push({ label: COPY.extensionId, value: item.id });
        return facts;
    }

    /**
     * The bubble that leads a card and the detail's hero.
     *
     * The category's mark, the marketplace's own builder, when the item has a
     * category. An invalid item with no manifest has none — a real state here,
     * not the defect `categoryMark` documents — so it wears an avatar of its
     * name, seeded by its id.
     *
     * @param {object} item
     * @param {'chip'|'sm'|'md'|'lg'} size
     * @returns {HTMLElement} Decorative (`aria-hidden`): the name and the
     *   category are printed beside it.
     */
    function markFor(item, size) {
        if (item.category) return BossModPackCard.categoryMark(item.category, size);
        return BossModAvatar.create({ name: item.name, color: BossModAvatar.seedFor(item.id), size });
    }

    /**
     * What one card prints, in BossModPackCard.packCard's `view` shape.
     *
     * @param {object} item
     * @returns {object} Title, category chip, the version as a tag, the
     *   one-line summary, the author and the state chip with its tint.
     */
    function cardView(item) {
        const state = chip(item);
        return {
            title: item.name,
            category: item.category,
            tags: item.version ? [`v${item.version}`] : [],
            mission: item.summary,
            author: item.author ? { name: item.author, url: null } : null,
            chip: state ? state.label : null,
            chipTone: state ? state.tone : null,
            mark: item.category ? undefined : markFor(item, 'md'),
        };
    }

    return { COPY, SECTIONS, chip, railGroups, visible, sectionsFor, aboutFacts, markFor, cardView, setupState };
})();
