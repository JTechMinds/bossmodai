/**
 * BossMod AI — everything the marketplace says about packs it will not offer.
 *
 * The agent-packs browse route sends a `withheld` row for every catalog entry
 * that did not become a card. Withholding the CARD is right — install runs the same
 * gate the browse list does, so a card for a pack this app rejects is a card
 * whose only outcome is an error. Withholding the FACT is not: the operator
 * owns the catalog repo, and a pack that silently vanished from their own
 * catalog is a pack that stays broken. Hide them from the grid, never hide
 * that they were hidden.
 *
 * Two kinds, and they are not the same fact. REFUSED means the server read the
 * pack and rejected its content — the maintainer has a file to fix, and the
 * reason names it. UNAVAILABLE means the server could not read the file at
 * all — nothing whatever is known about its content, the cause is as likely a
 * GitHub 502 as anything in the repo, and the only honest offer is a retry.
 * Telling a maintainer their pack is broken because a fetch failed is a false
 * accusation, so the wording of each kind is decided here, once.
 *
 * Its own module rather than more of marketplace-view.js: this is one
 * responsibility — reporting what the app would not offer, in the notice under
 * the grid AND on an installed row whose catalog entry has gone bad — and
 * marketplace-view.js is at 253 of its 300 lines. It loads FIRST of the
 * marketplace modules; it calls BossModDom and nothing else.
 *
 * Every string it renders — titles, paths, categories, and the server's
 * verbatim error message, which can quote pack-authored text — is REMOTE data
 * and reaches the document as a text node through h(). There is no
 * markup-string path in this file.
 */
const BossModMarketplaceWithheld = (() => {
    const { h } = BossModDom;

    // The server's whole vocabulary for `withheld[].kind`. `list_catalog`
    // decides it by which call failed — the fetch, or the parse-and-quality
    // gate — so there is no third value and no inference from `code` here.
    const REFUSED = 'refused';
    const UNAVAILABLE = 'unavailable';

    // Every state an installed row can be in relative to the catalog, as
    // marketplace-items.js projects them. `catalog` and `url` are listed with
    // an explicit null rather than left out: a card-backed row is fine and a
    // URL install was never in this catalog, and saying so here is what lets
    // an UNKNOWN status be a thrown error instead of a missing warning.
    const NOTE = Object.freeze({
        catalog: null,
        url: null,
        gone: 'This pack is no longer in the catalog at this pin.',
        refused: 'The catalog still lists this pack and the app now refuses it. '
            + 'It is named under the grid, with the reason.',
        unavailable: 'The catalog still lists this pack, but its file could not '
            + 'be read at this pin.',
    });

    const COPY = Object.freeze({
        title: 'Not shown in this catalog',
        refusedHead: 'Refused — read, and rejected. The pack has to change.',
        unavailableHead: 'Unavailable — not read at all. Nothing is known about these.',
        retry: 'Try loading the catalog again',
        refusedWord: 'refused',
        unavailableWord: 'unavailable',
    });

    /**
     * Split the flat `withheld` list into its two kinds, in catalog order.
     *
     * @param {object[]} rows  The API's `withheld`.
     * @returns {{refused: object[], unavailable: object[]}}
     * @throws {Error} On a `kind` this build does not know. The server and this
     *   file ship together, so an unknown kind is a contract break, and a row
     *   quietly sorted into the wrong pile — or dropped — is the silent
     *   fallback this whole surface exists to undo.
     */
    function byKind(rows) {
        const groups = { refused: [], unavailable: [] };
        rows.forEach((row) => {
            if (row.kind !== REFUSED && row.kind !== UNAVAILABLE) {
                throw new Error(
                    `[marketplace-withheld] unknown withheld kind "${row.kind}" `
                    + `for pack "${row.id}"`,
                );
            }
            groups[row.kind].push(row);
        });
        return groups;
    }

    // "2 packs in this catalog are not shown here: 1 refused, 1 unavailable."
    // One sentence, and it counts BOTH kinds: the operator's first question is
    // how many rows are missing from the grid in front of them.
    function summary(groups) {
        const total = groups.refused.length + groups.unavailable.length;
        const parts = [];
        if (groups.refused.length) {
            parts.push(`${groups.refused.length} ${COPY.refusedWord}`);
        }
        if (groups.unavailable.length) {
            parts.push(`${groups.unavailable.length} ${COPY.unavailableWord}`);
        }
        const noun = total === 1 ? 'pack' : 'packs';
        const verb = total === 1 ? 'is' : 'are';
        return `${total} ${noun} in this catalog ${verb} not shown here: ${parts.join(', ')}.`;
    }

    // One row: what it is called, which category lost it, where its file is,
    // and the server's own words. The path is what the maintainer opens, so it
    // is rendered as <code> rather than buried in the sentence — and it is
    // published for both kinds, because "which file" is the question either
    // way.
    function row(entry) {
        return h('li', { class: 'market-withheld-row' },
            h('p', { class: 'market-withheld-name' },
                h('span', { class: 'market-withheld-title' }, entry.title || entry.id),
                h('span', { class: 'market-withheld-where' }, entry.category)),
            h('code', { class: 'market-withheld-path' }, entry.path),
            h('p', { class: 'market-withheld-why' }, `${entry.message} (${entry.code})`));
    }

    // A group is a heading, its rows, and — for unavailable only — the one
    // action that makes sense. There is nothing to press about a refusal: the
    // fix is in the catalog repo, not in this app.
    function group(rows, heading, action) {
        if (!rows.length) return null;
        return h('div', { class: 'market-withheld-group' },
            h('h4', { class: 'market-withheld-kind' }, heading),
            h('ul', { class: 'market-withheld-list' }, rows.map(row)),
            action);
    }

    /**
     * The quiet notice under the grid, or null when there is nothing to say.
     *
     * @param {object[]} rows  `state.withheld`.
     * @param {object} handlers  Reads `onRetry(focusSelector)`.
     * @returns {HTMLElement|null} Null when nothing was withheld — an empty
     *   notice would claim a problem that does not exist.
     *
     * A region with a name, so it is reachable rather than merely present, and
     * `role="status"` on the SUMMARY LINE ONLY. Announced, politely, without
     * moving focus — and scoped to one short sentence because the browse view
     * is rebuilt on every keystroke in the filter box, and a live region
     * wrapped around the whole list would re-read every path and every reason
     * on each of them. Never `role="alert"`: the grid beside it loaded fine,
     * these are rows the app declined to offer, and an assertive interruption
     * for a state that is not an error is how operators learn to ignore alerts.
     */
    function notice(rows, handlers) {
        if (!rows.length) return null;
        const groups = byKind(rows);
        // Built only when there is an unreadable row to offer it for: an
        // orphan button with a listener on it is a node nobody asked for.
        // It lands the keyboard on the filter box, because a retry that works
        // deletes the very control the finger was on.
        const retry = groups.unavailable.length ? h('button', {
            class: 'market-action', id: 'market-withheld-retry', type: 'button',
            onclick: () => handlers.onRetry('#market-find'),
        }, COPY.retry) : null;
        return h('section', {
            class: 'market-withheld', 'aria-labelledby': 'market-withheld-head',
        },
        h('h3', { class: 'market-withheld-head', id: 'market-withheld-head' }, COPY.title),
        h('p', { class: 'market-withheld-sum', role: 'status' }, summary(groups)),
        group(groups.refused, COPY.refusedHead, null),
        group(groups.unavailable, COPY.unavailableHead, retry));
    }

    /**
     * What an installed row whose catalog entry is missing has to be told.
     *
     * The one place withholding actively hides something from a person already
     * affected: they are running agents built from that pack. A row that LEFT
     * the repo and a row the catalog still lists but the app now refuses used
     * to render identically — both merely absent from the cards — and only one
     * of them is something the operator can go and fix.
     *
     * @param {string} catalogStatus  marketplace-items.js's projection.
     * @returns {string|null} Null for a row that needs no warning: one with a
     *   card behind it, and one installed from a URL that was never in this
     *   catalog at all.
     * @throws {Error} On a status this file has no wording for. A row rendered
     *   silently unwarned is the exact failure this function exists to end.
     */
    function installedNote(catalogStatus) {
        if (!Object.prototype.hasOwnProperty.call(NOTE, catalogStatus)) {
            throw new Error(
                `[marketplace-withheld] no wording for catalog status "${catalogStatus}"`,
            );
        }
        return NOTE[catalogStatus];
    }

    return { COPY, REFUSED, UNAVAILABLE, byKind, notice, installedNote };
})();
