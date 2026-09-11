/**
 * BossMod AI — step 1 of Add agent: pick a template, or start blank.
 *
 * The create dialog's first step. Its whole input is the LOCAL template
 * library, read through context/agent-templates-api.js in one indexed query, so
 * it knows nothing about GitHub, refs, catalogs or trust; that is the
 * marketplace's job, and this offers the door to it rather than a copy of it.
 *
 * ORGANISED LIKE THE MARKETPLACE, and built from the same two modules: the
 * cards are marketplace/pack-card.js and the filters are
 * marketplace/filter-rail.js. That is not a resemblance, it is the same
 * builders over the same rows — an installed template IS a pack — and it
 * replaces a panel that printed a title and a specialty which, on nearly every
 * real pack, was the title again. What the operator read in the marketplace a
 * screen ago is what they read here: the mark, the category, the occasion to
 * hire, the mission.
 *
 * BLANK IS THE FIRST CELL of that same grid, not a sidecar beside it and not a
 * fallback under it. It is the one affordance that is offered in every state —
 * a library that is loading, empty or unreadable still has to let an operator
 * author an agent — so it is drawn before the state line rather than instead of
 * it. Clicking any cell IS the choice; there is no confirm step, because
 * picking again is free (the dialog keeps the draft when the same cell is
 * picked twice).
 *
 * NOTHING THE OPERATOR IS TOUCHING IS EVER REBUILT, which is why this file
 * carries none of the focus-restoration machinery marketplace-view.js needs.
 * That view rebuilds its whole host per interaction and puts focus back by id
 * and caret afterwards. Here the head is built once and never replaced, the
 * rail repaints its live row in place (BossModFilterRail's `select`), and only
 * the card grid is rebuilt — so the filter box keeps the word being typed and a
 * rail row survives its own click without anything having to hand focus back.
 *
 * Built with BossModDom.h. Titles, specialties and descriptions are remote,
 * pack-authored text that arrived here through an install; none of it may reach
 * a markup-string path.
 */
const BossModAgentTemplatePicker = (() => {
    const { h, clear } = BossModDom;
    const API = BossModAgentTemplatesApi;
    // The projection and the two builders the marketplace grid also spends.
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
        reading: 'Reading your template library…',
        empty: 'No templates installed yet.',
        browse: 'Browse marketplace',
        failed: 'Couldn’t read your template library.',
        retry: 'Try again',
        noMatch: (query) => `No template matches “${query}”.`,
    });

    /** Every scope row the rail offers. One today; the group exists because the
     *  marketplace's does, and the two rails must not read as different kinds
     *  of thing when they are showing the same library. */
    const ALL = 'all';

    /**
     * Build the picker.
     *
     * @param {object} deps
     * @param {(template: object|null) => void} deps.onPick  Called with the
     *   chosen `AgentTemplate` row, or NULL for Blank. Null is a real answer,
     *   not an absent one — the dialog builds a different form for it.
     * @param {() => void} deps.onBrowse  Opens the marketplace. Reachable from
     *   the header in every state, and from the empty state's own button, which
     *   is the only door a first-run operator can see.
     * @returns {{element: HTMLElement, refresh: () => Promise<void>,
     *   focus: () => void}} `refresh` re-reads the library and repaints;
     *   `focus` puts the keyboard on the Find box, or on the Blank cell when
     *   there is nothing to filter — the dialog owes focus a home on every step
     *   swap, and an input that is not rendered cannot take it.
     * @throws {Error} When either callback is missing. A picker whose cells
     *   answer to nobody is a dead end, and a silent one.
     */
    function createPicker(deps) {
        const { onPick, onBrowse } = deps || {};
        if (typeof onPick !== 'function') throw new Error('[picker] onPick is required');
        if (typeof onBrowse !== 'function') throw new Error('[picker] onBrowse is required');

        // 'loading' is a state, not a spinner: the read is one local query and
        // is normally done before the operator looks, but it can FAIL, and a
        // library that could not be read must never render as an empty one.
        let status = 'loading';
        /** Raw `AgentTemplate` rows — what `onPick` hands back. */
        let templates = [];
        /** The same rows projected into what a card prints. Parallel to
         *  `templates` by index, which is how a card gets back to its row. */
        let items = [];
        let query = '';
        let category = ALL;
        let rail = null;

        // ─── The head, built once and never rebuilt ───
        const findInput = h('input', {
            class: 'field-input', id: 'agent-template-find', type: 'search',
            placeholder: COPY.findHint,
            oninput: (event) => { query = event.target.value; renderCards(); },
        });
        const findRow = h('div', { class: 'picker-find-row' },
            h('label', { class: 'field-label', for: 'agent-template-find' },
                COPY.findLabel),
            findInput);
        // THE FILTER ALONE. `Browse marketplace` sat here for one round and
        // read as part of the filter — "find a template" and "go somewhere
        // else to get one" are different errands, and one line said they were
        // the same. It is the footer's lead action now
        // (context/agent-dialog-footer.js); the empty state below keeps its own
        // copy, which is the door a first-run operator can actually see.
        const head = h('div', { class: 'picker-head' }, findRow);

        const railHost = h('div', { class: 'picker-rail-host' });
        const cardsEl = h('div', { class: 'picker-cards' });
        const statusEl = h('div', { class: 'picker-status-host' });
        const grid = h('div', { class: 'picker-grid' }, cardsEl, statusEl);
        const body = h('div', { class: 'picker-body' }, railHost, grid);
        const element = h('div', { class: 'picker' }, head, body);

        /**
         * The Blank cell.
         *
         * Composes `.market-card` so it sits in the grid at the same size and
         * rhythm as the packs beside it — it is a peer, and a cell that was
         * shaped differently would read as a fallback again — and adds
         * `.picker-blank` for the dashed edge that says "nothing is in here
         * yet". The mark is `.avatar-empty`, which is already this app's word
         * for an unfilled seat: the roster's own Add agent row wears it.
         *
         * @returns {HTMLElement}
         */
        function blankCell() {
            return h('button', {
                class: 'market-card picker-blank', type: 'button', id: 'agent-pick-blank',
                onclick: () => onPick(null),
            },
            h('span', { class: 'market-card-head' },
                h('span', {
                    class: 'avatar avatar-md avatar-empty', 'aria-hidden': 'true',
                }, COPY.blankMark),
                h('span', { class: 'market-card-title' }, COPY.blank)),
            h('span', { class: 'market-card-desc' }, COPY.blankHint));
        }

        /**
         * One template, as the card the marketplace draws for the same row.
         *
         * `picker-card` carries no style of its own: it is what tells a
         * template cell apart from the Blank cell beside it, which both this
         * module's own reasoning and the dialog's tests need a name for.
         *
         * @param {object} item  From `ITEMS.templateItem`.
         * @param {number} at  Index into `templates`, and the card's number.
         * @returns {HTMLElement}
         */
        function templateCard(item, at) {
            return PACK_CARD.packCard(item, {
                id: `picker-card-${at}`,
                onSelect: () => onPick(templates[at]),
                extraClass: 'picker-card',
            });
        }

        /** The state line under the grid, and the one action it may offer. */
        function statusLine(text, role) {
            return h('p', { class: 'picker-status', role }, text);
        }

        /**
         * Which rows survive the rail and the filter box.
         *
         * The filter searches the pack's WHOLE description rather than the
         * mission the card renders — the word an operator half-remembers is as
         * likely to be in the scope or the handoff as in the first paragraph —
         * which is the rule BossModMarketplaceItems.visible already applies to
         * the catalog. Kept in step deliberately: a template found in the
         * marketplace has to be findable here by the same words.
         *
         * @returns {number[]} Indices into `items`, so a card keeps the number
         *   that reaches its row.
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

        /** Category -> how many templates are in it, in category order. */
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
         * Rebuild the rail. Called only when the LIBRARY changed — a category
         * click repaints the live row in place instead, which is what lets the
         * button that was clicked keep the keyboard.
         *
         * @returns {void}
         */
        function renderRail() {
            clear(railHost);
            rail = null;
            // Nothing to narrow unless the library both read and holds
            // something. A rail of one row that filters nothing is furniture.
            const offered = status === 'ready' && items.length > 0;
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
                        rows: [{ id: ALL, label: COPY.all, count: items.length }],
                    },
                    { id: 'category', title: COPY.categoryGroup, rows: categoryRows() },
                ],
            });
            railHost.append(rail.element);
        }

        /**
         * Rebuild the cards and the state line beneath them.
         *
         * Blank is appended FIRST and unconditionally, in every state — see the
         * module header.
         *
         * @returns {void}
         */
        function renderCards() {
            clear(cardsEl);
            clear(statusEl);
            // The filter box has nothing to filter unless the library both read
            // and holds something, so it is hidden rather than offered empty.
            findRow.hidden = !(status === 'ready' && templates.length > 0);
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
            if (!templates.length) {
                statusEl.append(
                    statusLine(COPY.empty, 'status'),
                    h('button', {
                        class: 'btn btn-sm picker-action', type: 'button',
                        id: 'agent-template-browse',
                        onclick: () => onBrowse(),
                    }, COPY.browse));
                return;
            }
            const visible = matching();
            if (!visible.length) {
                statusEl.append(statusLine(COPY.noMatch(query.trim()), 'status'));
                return;
            }
            visible.forEach((at) => cardsEl.append(templateCard(items[at], at)));
        }

        /**
         * Re-read the library and repaint.
         *
         * @returns {Promise<void>} Never rejects: a failed read is a rendered
         *   state with a retry, not an exception the dialog has to catch. A
         *   row the projection refuses — one whose parsed sections the server
         *   did not send — fails the whole read for the same reason
         *   `BossModMarketplaceItems.parsed` throws rather than returning a
         *   blank: a library rendered from a broken payload is worse than one
         *   that says it could not be read.
         */
        async function refresh() {
            status = 'loading';
            renderRail();
            renderCards();
            try {
                const rows = await API.listTemplates();
                // A body that is not a list is a broken read, not an empty
                // library, and must not be painted as one.
                if (!Array.isArray(rows)) throw new Error('the library did not answer with a list');
                items = rows.map((row) => ITEMS.templateItem(row));
                templates = rows;
                status = 'ready';
                // A category that has just left the library would filter the
                // grid to nothing with no rail row to undo it.
                if (category !== ALL && !items.some((item) => item.category === category)) {
                    category = ALL;
                }
            } catch (err) {
                console.error('[picker] the template library could not be read', err);
                templates = [];
                items = [];
                status = 'failed';
            }
            renderRail();
            renderCards();
        }

        function focus() {
            if (!findRow.hidden) findInput.focus();
            else element.querySelector('#agent-pick-blank').focus();
        }

        /**
         * Where focus goes once a repaint has removed what was holding it.
         *
         * The panel the retry rebuilt has one of three shapes, and each has its
         * own first control: another retry if the read failed again, `Browse
         * marketplace` if it succeeded onto an empty library, and the Find box
         * if it succeeded onto a full one. Both buttons carry `.picker-action`,
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
