/**
 * BossMod AI — one pack, as a card, for every surface that lists packs.
 *
 * TWO SURFACES NOW, which is why this is a module rather than a function inside
 * marketplace-view.js. The marketplace grid lists the catalog; the Add agent
 * dialog's first step lists the local template library. They are the same rows
 * — a template IS a pack, installed — and an operator who found "Code Auditor"
 * in one has to recognise it in the other. Two builders is how a card comes to
 * mean one thing in the takeover and another in the dialog, and the Add agent
 * picker had already drifted that far: it printed a title and a specialty that
 * was usually the title again, while the card a screen away carried the mark,
 * the category, the occasion to hire and the mission.
 *
 * The MARK lives here for the same reason and is spent by three callers now —
 * this card, the detail hero, and the picker.
 *
 * The CSS is marketplace.css's `.market-card*`, unchanged and unmoved. The
 * class prefix names where the vocabulary was defined rather than who may spend
 * it; renaming it to `.pack-*` is a rename of ~120 call sites across two
 * stylesheets and seventy-five assertions, and the duplication this module
 * exists to remove is gone either way.
 *
 * Built with BossModDom.h. Titles, specialties, missions and author names are
 * pack-authored REMOTE text and must never reach a markup-string path.
 */
const BossModPackCard = (() => {
    const { h } = BossModDom;
    const ITEMS = BossModMarketplaceItems;

    /**
     * The bubble that says which FAMILY a pack belongs to.
     *
     * Moved here from marketplace-detail.js when the picker became a third
     * caller. The card and the hero must identify a pack the same way, and two
     * builders is how they come to differ. It is the roster's own avatar — a
     * template is an agent nobody has hired yet, and this app has one bubble.
     *
     * The COLOUR is derived from the slug, not the label: the slug is the
     * stable half, so Engineering is one colour in the grid, in the read, in
     * the picker, and after a restart, whatever the labeller does. The LETTERS
     * come from the label, so the mark and the word beside it cut the name at
     * the same boundaries — `Data Analyst` is `DA`, and a third word gets no
     * third letter.
     *
     * Decorative, and it may only stay that way because every surface that
     * draws it also prints the category as a WORD — the card's chip, the hero's
     * byline, the picker's chip. A coloured `E` is not the word "Engineering",
     * and colour alone may not carry it (SC 1.4.1). The word is also why the
     * bubble is `aria-hidden`: announcing both would say the same thing twice.
     *
     * @param {string|null} slug  The catalog's category id. Untrusted text: it
     *   reaches the document only as `h()`'s text node, never as markup.
     * @param {'chip'|'sm'|'md'|'lg'} size
     * @returns {HTMLElement} For a row with NO category — which every route
     *   should always send, so it is a defect rather than a state — the neutral
     *   grey circle carrying `?`, the same answer the module already gives a
     *   nameless agent. Never an empty circle.
     */
    function categoryMark(slug, size) {
        return BossModAvatar.create({
            name: null,
            text: BossModAvatar.initials(ITEMS.categoryLabel(slug)),
            color: BossModAvatar.seedFor(slug),
            size,
        });
    }

    /**
     * One pack, as a card.
     *
     * Mark and name, then WHAT KIND, then what it does, then who wrote it. The
     * line under the name used to be the specialty and read "Code Auditor"
     * under "Code Auditor" on nearly every pack in the catalog — the projection
     * drops that echo (BossModMarketplaceItems.distinctSpecialty), and the
     * category takes the row it was wasting. It is title-cased by the same
     * helper the rail's rows are: one derivation, so a slug cannot read two
     * ways on one screen.
     *
     * The body is the projection's two opening paragraphs — the author's
     * lead-in, then the mission — never the raw string they were cut from:
     * "Mission: …" on a card is a parser's output, not a summary. Each is
     * clamped on its own, so a pack with a lead-in costs the card two lines
     * rather than pushing its mission off the bottom of the one clamp.
     *
     * One button, PHRASING CONTENT ONLY — a control inside a control is not a
     * thing, which is why the author is plain here and a link in the detail.
     * `aria-current`, not `aria-pressed`: the card is not a toggle, it opens a
     * view, and the mark it keeps afterwards says "this is the one you were
     * reading" — the same word, and the same tint, the rail's live row uses.
     *
     * @param {object} view  What the card prints. `title` and `category` are
     *   required; `specialty`, `intro`, `mission`, `note`, `author` and `chip`
     *   are each drawn only when present, so a caller that has none of them
     *   gets a card with no empty rows rather than a stack of blank bands.
     *   `author` is the PROJECTION'S SHAPE — `{name, url}` or null, exactly
     *   what marketplace-items.js puts on an item — and the unwrap to a
     *   printable name happens here. It was the caller's, and only one of the
     *   two callers did it: the picker handed the object straight through and
     *   the card printed `[object Object]` where the author belongs. A field
     *   whose shape is known to the projection and to this renderer must not
     *   also have to be known by everything in between. An
     *   empty `category` label means an empty chip, which is a blue box that
     *   says nothing — so the chip follows the label's truthiness, not the
     *   field's presence.
     * @param {object} deps
     * @param {string} deps.id  The card's dom id. Required: a rebuild drops
     *   whatever node held focus, and the id is what a caller hands focus back
     *   to. Callers NUMBER their cards rather than naming them after the pack —
     *   a pack id is catalog data, and remote text has no business in a
     *   selector string.
     * @param {boolean} [deps.selected]  Paints `aria-current`.
     * @param {(event: Event) => void} deps.onSelect
     * @param {string} [deps.extraClass]  One extra class on the button, for a
     *   caller whose grid holds a cell this card shape does not describe.
     * @returns {HTMLElement} A `<button class="market-card">`.
     * @throws {Error} Without an id or an onSelect. A card nobody can focus
     *   after a rebuild, or one that answers to nobody, is a dead end and a
     *   silent one.
     */
    function packCard(view, deps) {
        const { id, selected, onSelect, extraClass } = deps || {};
        if (!id) throw new Error('[pack-card] deps.id is required');
        if (typeof onSelect !== 'function') throw new Error('[pack-card] deps.onSelect is required');
        const category = ITEMS.categoryLabel(view.category);
        // The projection carries `{name, url}`; a card prints the name and the
        // detail view is where the url becomes a link. Unwrapped HERE so both
        // callers can hand over the item's own field untouched.
        const author = view.author && view.author.name ? view.author.name : null;
        return h('button', {
            class: `market-card${extraClass ? ` ${extraClass}` : ''}`,
            type: 'button',
            id,
            'data-pack-id': view.packId || null,
            'data-installed': view.installedState || null,
            'aria-current': selected ? 'true' : null,
            onclick: onSelect,
        },
        // The mark LEADS, the way it leads the detail hero: same builder, same
        // bubble, so a pack is identified the same way in the grid, in the read
        // and in the picker. It does not replace the chip below it — the two
        // are on different bands doing different jobs, so neither reads as the
        // other said twice.
        h('span', { class: 'market-card-head' },
            categoryMark(view.category, 'md'),
            h('span', { class: 'market-card-title' }, view.title)),
        category || view.specialty ? h('span', { class: 'market-card-meta' },
            category ? h('span', { class: 'market-card-category' }, category) : null,
            view.specialty
                ? h('span', { class: 'market-card-specialty' }, view.specialty) : null) : null,
        view.intro ? h('span', { class: 'market-card-intro' }, view.intro) : null,
        view.mission ? h('span', { class: 'market-card-desc' }, view.mission) : null,
        // An installed row with no card behind it: gone from the repo, or still
        // listed and now refused. Two different facts, and the operator is
        // running agents built from this one either way.
        view.note ? h('span', { class: 'market-card-note' }, view.note) : null,
        author || view.chip ? h('span', { class: 'market-card-foot' },
            author ? h('span', { class: 'market-card-author' }, author) : null,
            view.chip ? h('span', { class: 'market-card-state' }, view.chip) : null) : null);
    }

    return { categoryMark, packCard };
})();
