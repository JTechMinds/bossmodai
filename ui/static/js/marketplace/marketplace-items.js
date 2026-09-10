/**
 * BossMod AI — the catalog and the local library, projected into one list.
 *
 * The marketplace shows two sources at once: packs the catalog lists at the
 * pinned commit, and templates already installed here — including ones the
 * catalog cannot show, because they came from a URL or because their pack has
 * since left the repo. This turns both into ONE item shape, so neither the
 * grid nor the detail view ever branches on where a row came from.
 *
 * Pure: every function takes what it reads and returns what it derived. No
 * state is mutated, nothing is fetched, and no node is built — which is what
 * makes the staleness rule below testable on its own, without a DOM and
 * without a server. Card state compares CONTENT HASHES, never commit SHAs.
 */
const BossModMarketplaceItems = (() => {

    /**
     * "code-review" -> "Code Review".
     *
     * Slugs are the CATALOG'S — `packs/<category>/` is whatever a contributor
     * adds — so the label is derived rather than kept in a map that would go
     * stale. It lives in the projection because BOTH views need it now: the
     * browse rail and card print it, and the mark that identifies a pack takes
     * its letters from it. A second copy is how "product-design" comes to read
     * two ways on one screen.
     *
     * @param {string|null} slug
     * @returns {string} Title-cased words, or '' when there is no slug —
     *   never a placeholder, so a caller decides for itself what an absent
     *   category looks like.
     */
    function categoryLabel(slug) {
        return String(slug || '').split('-').filter(Boolean)
            .map((part) => part.replace(/^[a-z]/, (letter) => letter.toUpperCase()))
            .join(' ');
    }

    /**
     * Which of the three states one catalog card is in.
     *
     * @param {object} card  A catalog pack card.
     * @param {object|null} installed  The template row installed from it.
     * @returns {'install'|'installed'|'update'}
     *
     * A card with no hash could not be parsed at the pin, so no update can be
     * claimed for it — it is still installed, and "Update available" on a guess
     * would be a lie. Commit SHAs cannot answer this either: the catalog pin is
     * repo-wide, so comparing SHAs would mark every installed template stale on
     * any pin bump, including packs whose file never changed.
     */
    function cardState(card, installed) {
        if (!installed) return 'install';
        if (card.content_hash && installed.content_hash
            && card.content_hash !== installed.content_hash) return 'update';
        return 'installed';
    }

    /**
     * The two paragraphs a pack opens with, above the labelled sections.
     *
     * `describe_pack` splits a hire description into `preamble` — the prose
     * before the first heading — and one key per heading it recognises. Both
     * halves are real content and they are NOT alternatives: a description
     * reading "Read this first.\n\nMission: …\nIn scope: …" carries a
     * preamble AND a mission, and the surface printed only the mission, so the
     * lead-in the pack author wrote appeared nowhere at all.
     *
     * Decided here, once, rather than in each view: the card and the detail
     * open on the same two paragraphs and must not be able to disagree about
     * which they are or which comes first.
     *
     * A pack that opens straight on `Mission:` has no lead-in, and there the
     * catalog index row's `summary` — its one-line When-to-hire — stands in.
     * That ORDER is the server's, not a second opinion invented here: the pack
     * is the file that gets installed, an index row can drift from it, and a
     * card promising one job while the installed template describes another
     * would be the index lying about the pack. What the client adds is length.
     * The server has one slot and picks the preamble's FIRST LINE for it; the
     * card and the detail have room for the whole lead-in, so they read it off
     * `sections` and fall back to `summary` only where the server's own rule
     * would have fallen back too.
     *
     * This is a content fallback and not a failure one. A row whose pack could
     * not be read never reaches here — `list_catalog` withholds it — so a null
     * `intro` means the pack and its catalog row both name no occasion to hire,
     * which is a thing a valid pack is allowed to do.
     *
     * @param {object} row  A catalog card or an installed template row. Reads
     *   `sections`, already checked by `parsed()`, and `summary`, which only a
     *   catalog card carries: `summary` lives in the catalog index, and
     *   installing snapshots the pack file, not the row that pointed at it.
     * @returns {{intro: string|null, mission: string|null}} Verbatim bodies, in
     *   the order they are read. Either is null when the pack carries none —
     *   never `''`, so a caller branches on truthiness and never renders an
     *   empty paragraph.
     */
    function openingText(row) {
        const group = row.sections.description;
        const body = (text) => (typeof text === 'string' && text.trim() ? text : null);
        return {
            intro: body(group.preamble) || body(row.summary),
            mission: body(group.mission),
        };
    }

    /**
     * The pack's specialty, or `''` when it only says the title again.
     *
     * Nearly every pack in the real catalog sets `specialty` to its own title —
     * "Code Auditor / Code Auditor", "Feature Planner / Feature Planner" — so
     * the line under the card's name carried no information at all while
     * holding the most valuable row on the card. A specialty that genuinely
     * DIFFERS is a role description worth reading and is kept; only the echo is
     * dropped. Deciding it here rather than in each view is what stops the card
     * and the detail from disagreeing about whether a pack has one.
     *
     * Compared trimmed and case-insensitively: "Code Auditor" and "code
     * auditor " are the same sentence wearing different whitespace, and
     * printing the second under the first is the same noise.
     *
     * @param {string} title  The row's display title.
     * @param {string} specialty  The row's `specialty`, possibly absent.
     * @returns {string} The specialty to show, or `''` — never null, so both
     *   views branch on the same truthiness they use for every other field.
     */
    function distinctSpecialty(title, specialty) {
        const value = String(specialty || '').trim();
        const name = String(title || '').trim();
        return value.toLowerCase() === name.toLowerCase() ? '' : value;
    }

    /**
     * The parsed half of one row, from whichever list it came out of.
     *
     * The server split `sections` out of the same parse the card came from, so
     * nothing here re-splits a string — and NEITHER source can leave it out any
     * more. A catalog card exists only for a pack that survived `list_catalog`'s
     * parse-and-quality gate; one that did not is withheld and never becomes a
     * card at all. An installed row derives `sections` as a computed field from
     * two TEXT NOT NULL columns. So the "absent sections" branch this used to
     * carry was dead against any payload either route can produce, and keeping
     * it meant a broken payload rendered as a blank card instead of saying so.
     *
     * `tools_hint` is a different question and keeps its answer: it is
     * genuinely absent on a pack that lists no tools, and an installed row
     * carries `[]` for the same fact.
     *
     * @param {object} row  A catalog card or an installed template row.
     * @returns {{sections: object, intro: string|null, mission: string|null,
     *   toolsHint: string[]|null}}
     * @throws {Error} When `sections` is missing or is not the two-half shape
     *   `describe_pack` returns. Named and loud, rather than an
     *   undefined-property crash three frames deeper or a card drawn blank.
     */
    function parsed(row) {
        const sections = row.sections;
        if (!sections || !sections.description || !sections.done) {
            throw new Error(
                `[marketplace-items] row "${row.id}" carries no parsed sections`,
            );
        }
        return {
            sections,
            ...openingText(row),
            toolsHint: Array.isArray(row.tools_hint) ? row.tools_hint : null,
        };
    }

    /**
     * Where an installed row stands relative to the catalog, when no card
     * carries it.
     *
     * Three different facts used to be one: a URL install that was never in
     * this catalog, a pack DELETED from the repo, and a pack the catalog still
     * lists that the app now refuses or could not read. The last of those only
     * became invisible when validation moved to the source — a refused pack
     * leaves the cards, so its installed row falls in here with the deleted
     * ones — and it is the one the operator can act on, because they are
     * running agents built from it.
     *
     * @param {object} row  An installed template row with no catalog card.
     * @param {object} withheldByPackId  Withheld rows keyed by pack id.
     * @returns {'url'|'gone'|'refused'|'unavailable'}
     */
    function extraStatus(row, withheldByPackId) {
        if (!row.pack_id) return 'url';
        const withheld = withheldByPackId[row.pack_id];
        return withheld ? withheld.kind : 'gone';
    }

    /**
     * Every item the catalog and the library together can show.
     *
     * @param {object} state  Reads `categories`, `installedByPackId`,
     *   `installedExtras`, `withheldByPackId` and `pin`.
     * @returns {object[]} Catalog cards first, then the installed rows the
     *   catalog cannot show. `key` is what selection is held by; `inCatalog`
     *   is what the rail's filters read; `intro` and `mission` are the two
     *   paragraphs both views open with, either null when absent;
     *   `catalogStatus` is why a row has no card, which the views turn into
     *   words through BossModMarketplaceWithheld.
     */
    function allItems(state) {
        const cards = state.categories.flatMap((group) => (group.packs || []).map((card) => {
            const installed = state.installedByPackId[card.id] || null;
            return {
                key: `pack:${card.id}`,
                title: card.title || card.id,
                specialty: distinctSpecialty(card.title || card.id, card.specialty),
                description: card.description || '',
                author: card.pack_author || null,
                packId: card.id,
                template: installed,
                state: cardState(card, installed),
                commitSha: state.pin,
                category: card.category || group.id,
                inCatalog: true,
                catalogStatus: 'catalog',
                ...parsed(card),
            };
        }));
        return cards.concat(state.installedExtras.map((row) => ({
            key: `tpl:${row.id}`,
            title: row.title,
            specialty: distinctSpecialty(row.title, row.specialty),
            description: row.description || '',
            author: row.author_name ? { name: row.author_name, url: row.author_url } : null,
            packId: row.pack_id || null,
            template: row,
            state: 'installed',
            commitSha: row.commit_sha || '',
            category: row.category,
            inCatalog: false,
            catalogStatus: extraStatus(row, state.withheldByPackId),
            ...parsed(row),
        })));
    }

    /**
     * What the current rail row and filter box leave on screen.
     *
     * @param {object} state  Reads `category` and `query` on top of what
     *   `allItems` needs.
     * @returns {object[]}
     *
     * The filter searches the pack's WHOLE description rather than the mission
     * the card renders: the word an operator half-remembers is as likely to be
     * in the scope or the handoff as in the first paragraph.
     */
    function visible(state) {
        const query = state.query.trim().toLowerCase();
        const items = allItems(state).filter((item) => {
            if (state.category === 'installed') return Boolean(item.template);
            if (state.category === 'all') return item.inCatalog;
            return item.inCatalog && item.category === state.category;
        });
        if (!query) return items;
        return items.filter((item) => `${item.title} ${item.specialty} ${item.description}`
            .toLowerCase().includes(query));
    }

    /**
     * Re-derive what is installed, after any change to any of the three lists.
     *
     * @param {object[]} templates  Every installed row.
     * @param {object[]} categories  The catalog's groups.
     * @param {object[]} withheld  The catalog rows that did not become cards.
     * @returns {{installedByPackId: object, installedExtras: object[],
     *   withheldByPackId: object}} Ready to `Object.assign` onto the state.
     *
     * `installedExtras` is every row the catalog cannot show — URL installs,
     * catalog packs that have left the repo, and now catalog packs the app
     * refuses or could not read — which is why it is derived from BOTH lists
     * rather than from the row's `source` field.
     *
     * `withheld` is a third input for exactly one reason: those last two cases
     * are no longer distinguishable from the cards alone. A refused pack is
     * absent from `categories` in the same way a deleted one is, so without
     * this map an installed row whose pack has gone bad renders as a row whose
     * pack was deleted, and the operator running agents built from it is told
     * nothing. Indexed once per data change here rather than re-scanned on
     * every render, and keyed by PACK id — that is what a withheld row and an
     * installed row have in common.
     */
    function indexInstalled(templates, categories, withheld) {
        const byPackId = {};
        templates.forEach((row) => { if (row.pack_id) byPackId[row.pack_id] = row; });
        const known = new Set();
        categories.forEach((group) => {
            (group.packs || []).forEach((pack) => known.add(pack.id));
        });
        const withheldByPackId = {};
        withheld.forEach((row) => { withheldByPackId[row.id] = row; });
        return {
            installedByPackId: byPackId,
            withheldByPackId,
            installedExtras: templates.filter(
                (row) => !row.pack_id || !known.has(row.pack_id),
            ),
        };
    }

    return { cardState, categoryLabel, allItems, visible, indexInstalled };
})();
