/**
 * BossMod AI — step 1 of Add agent: a template, a recent agent, or blank.
 *
 * The create pane's first step (context/agent-add-pane.js). Its input is two
 * local reads made together: the template library
 * (context/agent-templates-api.js) and every agent SNAPSHOT
 * (context/agent-api.js). Neither knows anything about GitHub, refs, catalogs
 * or trust; that is the marketplace's job — the other tab of the same Agents
 * dialog — and this offers the door to it rather than a copy of it.
 *
 * RECENT IS A SCOPE, not a second grid: a snapshot is the setup of an agent
 * made here — perhaps since deleted — and picking it fills the same form a
 * template does, minus the connection secrets a snapshot never holds. So
 * `onPick` hands back a CHOICE, `{kind}` plus its row: "blank", "this
 * template" and "this agent again" are three forms to build, and a nullable
 * template could name only one.
 *
 * ORGANISED LIKE THE MARKETPLACE, and built from the same two modules: the
 * cards are marketplace/pack-card.js and the filters are
 * marketplace/filter-rail.js. Not a resemblance — the same builders over the
 * same rows, because an installed template IS a pack.
 *
 * BLANK IS THE FIRST CELL of that same grid, not a sidecar beside it and not a
 * fallback under it: it is the one affordance offered in every state, so it is
 * drawn before the state line rather than instead of it. Clicking any cell IS
 * the choice; there is no confirm step, because picking again is free (the
 * pane keeps the draft when the same cell is picked twice).
 *
 * NOTHING THE OPERATOR IS TOUCHING IS EVER REBUILT, which is why this file
 * carries none of the focus-restoration machinery marketplace-view.js needs.
 * The head is built once, the rail repaints its live row in place
 * (BossModFilterRail's `select`), and only the card grid is rebuilt — so the
 * filter box keeps the word being typed and a rail row survives its own click.
 *
 * Built with BossModDom.h. Titles, specialties, descriptions and agent names
 * are operator- or pack-authored text; none of it may reach a markup string.
 */
const BossModAgentTemplatePicker = (() => {
    const { h, clear } = BossModDom;
    const API = BossModAgentTemplatesApi;
    // The snapshots read; then the projection and two builders the
    // marketplace grid also spends.
    const AGENT_API = BossModAgentApi;
    const ITEMS = BossModMarketplaceItems;
    const PACK_CARD = BossModPackCard;
    const FILTER_RAIL = BossModFilterRail;

    const COPY = Object.freeze({
        blank: 'Blank agent',
        blankHint: 'Start from an empty form.',
        blankMark: '+',
        findLabel: 'Find a template',
        findHint: 'Filter by name, specialty or description',
        railLabel: 'Template sections',
        scopeGroup: 'Show',
        categoryGroup: 'Categories',
        all: 'All',
        recent: 'Recent',
        local: 'Local',
        reading: 'Reading your template library…',
        empty: 'No templates installed yet.',
        browse: 'Browse marketplace',
        failed: 'Couldn’t read your templates or your recent agents.',
        retry: 'Try again',
        noMatch: (query) => `No template matches “${query}”.`,
        noRecentMatch: (query) => `No recent agent matches “${query}”.`,
    });

    /** The two scope rows. `ALL` is the library, `RECENT` the snapshots; a
     *  category row is any other id, and it narrows the library. One live row
     *  across both groups, which is what the rail paints. */
    const ALL = 'all';
    const RECENT = 'recent';

    /**
     * Build the picker.
     *
     * @param {object} deps
     * @param {(choice: object) => void} deps.onPick  Called with the cell the
     *   operator picked: `{kind: 'blank'}`, `{kind: 'template', row}` with an
     *   `AgentTemplate`, or `{kind: 'snapshot', row}` with an `AgentSnapshot`.
     *   Blank is a real answer, not an absent one — the pane builds a
     *   different form for each of the three.
     * @param {() => void} deps.onBrowse  Switches the Agents dialog to its
     *   Marketplace tab. Reached from the empty state's own button, because a
     *   first-run operator with nothing installed is looking at the middle of
     *   this pane rather than at the tab in the dialog's head.
     * @returns {{element: HTMLElement, refresh: () => Promise<void>,
     *   focus: () => void}} `refresh` re-reads both lists and repaints;
     *   `focus` puts the keyboard on the Find box, or on the Blank cell when
     *   there is nothing to filter — an input that is not rendered cannot
     *   take it.
     * @throws {Error} When either callback is missing. A picker whose cells
     *   answer to nobody is a dead end, and a silent one.
     */
    function createPicker(deps) {
        const { onPick, onBrowse } = deps || {};
        if (typeof onPick !== 'function') throw new Error('[picker] onPick is required');
        if (typeof onBrowse !== 'function') throw new Error('[picker] onBrowse is required');

        // 'loading' is a state, not a spinner: two local reads, normally done
        // before the operator looks — but they can FAIL, and what could not be
        // read must never render as an empty grid.
        let status = 'loading';
        /** The library, projected into what a card prints. Each item carries
         *  its own `AgentTemplate` row, which is what `onPick` hands back. */
        let items = [];
        /** Raw `AgentSnapshot` rows, newest first, as the server ordered them. */
        let snaps = [];
        let query = '';
        /** The live rail row: ALL, RECENT, or a category slug. */
        let category = ALL;
        let rail = null;

        // ─── The head, built once and never rebuilt ───
        //
        // The app's toolbar search (core/search-field.js), the control the
        // Marketplace tab puts in the same place: a magnifier inside one
        // bordered box, its label the input's accessible name. Its glyph is
        // painted by the dialog, which paints the whole panel once it is up.
        const find = BossModSearchField.create({
            placeholder: COPY.findHint,
            label: COPY.findLabel,
            onInput: (event) => { query = event.target.value; renderCards(); },
        });
        find.input.id = 'agent-template-find';
        // THE FILTER ALONE. `Browse marketplace` sat here for one round and
        // read as part of it — "find a template" and "go and get one" are
        // different errands. The marketplace is the dialog's other tab now;
        // the empty state below keeps its own door, which is the one a
        // first-run operator can actually see.
        const head = h('div', { class: 'picker-head' }, find.element);

        const railHost = h('div', { class: 'picker-rail-host' });
        const cardsEl = h('div', { class: 'picker-cards' });
        const statusEl = h('div', { class: 'picker-status-host' });
        const grid = h('div', { class: 'picker-grid' }, cardsEl, statusEl);
        const body = h('div', { class: 'picker-body' }, railHost, grid);
        const element = h('div', { class: 'picker' }, head, body);

        /**
         * The Blank cell: `.market-card` so it is a PEER of the cards beside
         * it rather than a fallback under them, plus `.picker-blank` for the
         * dashed edge. Its mark is `.avatar-empty`, this app's word for an
         * unfilled seat — the roster's own Add agent row wears it.
         *
         * @returns {HTMLElement}
         */
        function blankCell() {
            return h('button', {
                class: 'market-card picker-blank', type: 'button', id: 'agent-pick-blank',
                onclick: () => onPick({ kind: 'blank' }),
            },
            h('span', { class: 'market-card-head' },
                h('span', {
                    class: 'avatar avatar-md avatar-empty', 'aria-hidden': 'true',
                }, COPY.blankMark),
                h('span', { class: 'market-card-title' }, COPY.blank)),
            h('span', { class: 'market-card-desc' }, COPY.blankHint));
        }

        /**
         * One template, as the card the marketplace draws for the same row —
         * plus the `Local` tag when the operator saved it here rather than
         * installing it. `picker-card` carries no style of its own: it is what
         * tells a template cell apart from the Blank cell beside it.
         *
         * @param {object} item  From `ITEMS.templateItem`.
         * @param {number} at  The card's number, for focus after a rebuild.
         * @returns {HTMLElement}
         */
        function templateCard(item, at) {
            return PACK_CARD.packCard({ ...item, tags: item.local ? [COPY.local] : [] }, {
                id: `picker-card-${at}`,
                onSelect: () => onPick({ kind: 'template', row: item.template }),
                extraClass: 'picker-card',
            });
        }

        /** One Recent agent, as marketplace/pack-card.js draws a snapshot.
         *  `.picker-recent` styles nothing: it names the kind of cell. */
        function snapshotCard(row, at) {
            return PACK_CARD.snapshotCard(row, {
                id: `picker-recent-${at}`,
                onSelect: () => onPick({ kind: 'snapshot', row }),
                extraClass: 'picker-card picker-recent',
            });
        }

        /** The state line under the grid. */
        const statusLine = (text, role) => h('p', { class: 'picker-status', role }, text);

        /**
         * Which templates survive the rail row and the filter box.
         *
         * The filter reads the pack's WHOLE description, not the mission the
         * card renders: the word an operator half-remembers is as likely to be
         * in the scope or the handoff. That is BossModMarketplaceItems.visible's
         * rule for the catalog, kept in step so a template found in the
         * marketplace is findable here by the same words.
         *
         * @returns {number[]} Indices into `items`.
         */
        function matching() {
            const needle = query.trim().toLowerCase();
            return items.map((item, at) => at).filter((at) => {
                const item = items[at];
                if (category !== ALL && item.category !== category) return false;
                if (!needle) return true;
                return `${item.title} ${item.specialty} ${item.description}`
                    .toLowerCase().includes(needle);
            });
        }

        /** The Recent cards the filter box leaves, newest first. It reads
         *  name, role and description — the three a snapshot card prints. */
        function recentCards() {
            const needle = query.trim().toLowerCase();
            return snaps.filter((row) => !needle
                || `${row.name} ${row.role || ''} ${row.description || ''}`
                    .toLowerCase().includes(needle)).map(snapshotCard);
        }

        /** Category -> how many templates it holds, in category order. */
        function categoryRows() {
            const counts = new Map();
            items.forEach((item) => {
                const key = item.category || '';
                counts.set(key, (counts.get(key) || 0) + 1);
            });
            return Array.from(counts.entries())
                .sort((a, b) => a[0].localeCompare(b[0]))
                .map(([slug, count]) => ({
                    id: slug, label: ITEMS.categoryLabel(slug), count,
                }));
        }

        /**
         * Rebuild the rail. Only when the LIBRARY changed — a row click
         * repaints the live mark in place, which is what lets the button that
         * was clicked keep the keyboard.
         *
         * @returns {void}
         */
        function renderRail() {
            clear(railHost);
            rail = null;
            // Nothing to narrow unless a read landed on something.
            const offered = status === 'ready' && Boolean(items.length || snaps.length);
            body.setAttribute('data-rail', offered ? 'shown' : 'none');
            if (!offered) return;
            rail = FILTER_RAIL.createRail({
                label: COPY.railLabel,
                idPrefix: 'picker-rail',
                current: category,
                onSelect: (id) => {
                    category = id;
                    rail.select(id);
                    renderCards();
                },
                groups: [
                    {
                        id: 'scope',
                        title: COPY.scopeGroup,
                        // Recent is offered only when there is an agent to
                        // recreate: a row that filters to nothing is furniture.
                        rows: [
                            { id: ALL, label: COPY.all, count: items.length },
                            ...(snaps.length
                                ? [{ id: RECENT, label: COPY.recent, count: snaps.length }] : []),
                        ],
                    },
                    { id: 'category', title: COPY.categoryGroup, rows: categoryRows() },
                ],
            });
            railHost.append(rail.element);
        }

        /**
         * Rebuild the cards and the state line beneath them. Blank is appended
         * FIRST and unconditionally, in every state — see the module header.
         *
         * @returns {void}
         */
        function renderCards() {
            clear(cardsEl);
            clear(statusEl);
            // Nothing to filter unless a read landed on something, so the
            // box is hidden rather than offered empty.
            find.element.hidden = !(status === 'ready' && Boolean(items.length || snaps.length));
            cardsEl.append(blankCell());
            if (status === 'loading') {
                statusEl.append(statusLine(COPY.reading, 'status'));
                return;
            }
            if (status === 'failed') {
                statusEl.append(
                    statusLine(COPY.failed, 'alert'),
                    h('button', {
                        class: 'btn btn-sm picker-action', type: 'button',
                        id: 'agent-template-retry',
                        // refresh() repaints over this very button, so the
                        // retry places focus after it: a control the operator
                        // activated must never hand the keyboard to <body>.
                        onclick: () => { void refresh().then(focusAfterRepaint); },
                    }, COPY.retry));
                return;
            }
            const recent = category === RECENT;
            if (!recent && !items.length) {
                statusEl.append(
                    statusLine(COPY.empty, 'status'),
                    h('button', {
                        class: 'btn btn-sm picker-action', type: 'button',
                        id: 'agent-template-browse',
                        onclick: () => onBrowse(),
                    }, COPY.browse));
                return;
            }
            const cards = recent
                ? recentCards()
                : matching().map((at) => templateCard(items[at], at));
            if (!cards.length) {
                statusEl.append(statusLine(
                    (recent ? COPY.noRecentMatch : COPY.noMatch)(query.trim()), 'status'));
                return;
            }
            cards.forEach((card) => cardsEl.append(card));
        }

        /**
         * Re-read both lists and repaint.
         *
         * @returns {Promise<void>} Never rejects: a failed read is a rendered
         *   state with a retry, not an exception the pane has to catch. A row
         *   the projection refuses — one whose parsed sections the server did
         *   not send — fails the whole read, because a grid drawn from a
         *   broken payload is worse than one that says it could not be read.
         */
        async function refresh() {
            status = 'loading';
            renderRail();
            renderCards();
            try {
                // Together, and either failing is the one failed state: half a
                // grid is not something the operator can act on.
                const [rows, snapshots] = await Promise.all(
                    [API.listTemplates(), AGENT_API.listSnapshots()]);
                // A body that is not a list is a broken read, not an empty
                // library, and must not be painted as one.
                if (!Array.isArray(rows) || !Array.isArray(snapshots)) {
                    throw new Error('the library did not answer with a list');
                }
                items = rows.map((row) => ITEMS.templateItem(row));
                snaps = snapshots;
                status = 'ready';
                // A rail row that has just left would filter the grid to
                // nothing, with no row on screen to undo it.
                if (category === RECENT ? !snaps.length
                    : category !== ALL && !items.some((item) => item.category === category)) {
                    category = ALL;
                }
            } catch (err) {
                console.error('[picker] the library could not be read', err);
                items = [];
                snaps = [];
                status = 'failed';
            }
            renderRail();
            renderCards();
        }

        function focus() {
            if (!find.element.hidden) find.input.focus();
            else element.querySelector('#agent-pick-blank').focus();
        }

        /**
         * Where focus goes once a repaint has removed what was holding it: the
         * rebuilt panel's own first control — another retry, `Browse
         * marketplace`, or the Find box. Both buttons carry `.picker-action`,
         * which is why one lookup answers for both.
         *
         * @returns {void}
         */
        function focusAfterRepaint() {
            const action = statusEl.querySelector('.picker-action');
            if (action) action.focus();
            else focus();
        }

        renderRail();
        renderCards();
        return { element, refresh, focus };
    }

    return { COPY, createPicker };
})();
