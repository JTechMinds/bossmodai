/**
 * BossMod AI — the marketplace's reading view, and the two parts both views spend.
 *
 * Picking a card no longer opens a pane beside the grid: it REPLACES the rail
 * and the grid with this, a full-width view of one pack. A 320px third column
 * could not hold a hire contract — mission, both scopes, handoff, the done bar
 * and its fail examples — without clipping it, which is what the operator was
 * reading when they asked for this.
 *
 * It reads TOP DOWN and then across. Who the pack is, what it is for and what
 * it costs to hire sit full width at the top — the name, the byline, the one
 * action, and both opening paragraphs. Under ONE rule, and only one, the pack's
 * contract is read a section at a time in marketplace-sections.js's tab list.
 * The stack of section-by-section separators this used to draw is gone with it:
 * six rules down a page is a page with no shape, only fences.
 *
 * The structure is the SERVER'S. Both the agent-packs cards and the
 * agent-templates rows always carry `sections`, split by the same parser the
 * pack quality gate reads, so nothing here re-splits a string and nothing here
 * invents a section it was not given. A pack that does not parse is withheld
 * at the source and never becomes a card at all; what is still genuinely
 * absent is any SINGLE section inside one, which may be null.
 *
 * It loads BEFORE marketplace-view.js and owns `confirmStrip`, `dismissButton`
 * and `categoryMark` because both views spend them — the trust question is
 * asked in the browse view beside the URL row that raised it, the uninstall
 * question is asked here, the takeover's one `✕` is in the top-right corner of
 * whichever view is up, and the bubble that says which family a pack belongs to
 * leads its card and its hero alike. One builder each, two call sites, and no
 * import cycle for index.html's load order to fail on.
 */
const BossModMarketplaceDetail = (() => {
    const { h } = BossModDom;
    const ITEMS = BossModMarketplaceItems;
    const WITHHELD = BossModMarketplaceWithheld;
    const READER = BossModMarketplaceSections;

    const COPY = Object.freeze({
        // The back control is the chevron and nothing else, so this is its
        // whole accessible name and it has to say where it goes. It returns to
        // the CATALOG, which is mostly packs that are not installed templates,
        // so it never names the library.
        backLabel: 'Back to the marketplace',
        dismiss: 'Close the marketplace',
        by: 'By',
        pinned: 'pinned',
        install: 'Install',
        update: 'Update',
        installed: 'Installed',
        installing: 'Installing…',
        uninstall: 'Uninstall',
        removing: 'Removing…',
        uninstallAsk: 'Remove this template from your library? Agents already '
            + 'created from it are unaffected.',
        remove: 'Remove',
        cancel: 'Cancel',
        trustConfirm: 'Install anyway',
        detailLabel: 'Agent detail',
    });

    function shortSha(sha) {
        return String(sha || '').replace(/[^0-9a-f]/gi, '').slice(0, 7);
    }

    /**
     * The bubble that says which FAMILY a pack belongs to.
     *
     * Here, and spent by both views, for the reason `confirmStrip` and
     * `dismissButton` are: the card and the hero must identify a pack the same
     * way, and two builders is how they come to differ. It is the roster's own
     * avatar — a template is an agent nobody has hired yet, and this app has
     * one bubble.
     *
     * The COLOUR is derived from the slug, not the label: the slug is the
     * stable half, so Engineering is one colour in the grid, in the read, and
     * after a restart, whatever the labeller does. The LETTERS come from the
     * label, so the mark and the word beside it cut the name at the same
     * boundaries — `Data Analyst` is `DA`, and a third word gets no third
     * letter.
     *
     * Decorative, and it may only stay that way because every surface that
     * draws it also prints the category as a WORD — the card's chip, the
     * hero's byline. A coloured `E` is not the word "Engineering", and colour
     * alone may not carry it (SC 1.4.1). The word is also why the bubble is
     * `aria-hidden`: announcing both would say the same thing twice.
     *
     * @param {string|null} slug  The catalog's category id. Untrusted text: it
     *   reaches the document only as `h()`'s text node, never as markup.
     * @param {'chip'|'sm'|'md'|'lg'} size
     * @returns {HTMLElement} For a row with NO category — which both routes
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
     * The inline question both views ask: trust before a URL install, and
     * confirmation before an uninstall.
     *
     * Inline, never a nested dialog: a second focus trap over the takeover is
     * the failure this whole surface is shaped to avoid.
     *
     * @param {object} config  `text`, `id` and `label` for the confirming
     *   button, `tone` for its class, `onConfirm` and `onCancel`.
     * @returns {HTMLElement}
     */
    function confirmStrip(config) {
        return h('div', { class: 'market-confirm', role: 'alert' },
            h('p', { class: 'market-confirm-text' }, config.text),
            h('div', { class: 'market-confirm-actions' },
                h('button', {
                    class: `market-action ${config.tone}`, id: config.id,
                    type: 'button', onclick: config.onConfirm,
                }, config.label),
                h('button', {
                    class: 'market-action', type: 'button', onclick: config.onCancel,
                }, COPY.cancel)));
    }

    // The hero's top-right slot, and only ever an ACTION. An installed-and
    // -current pack has none — its state moved to the byline — because the
    // biggest, most prominent element on the screen must not be the one thing
    // that cannot be pressed. `Uninstall` does not inherit the slot: it is the
    // destructive action and stays a quiet secondary beside it.
    function primary(item, state, handlers) {
        if (item.state === 'installed') return null;
        const busy = state.busyId === item.key;
        return h('button', {
            class: 'market-action primary market-action-lead', id: 'market-install',
            type: 'button', disabled: busy, onclick: () => handlers.onInstall(item),
        }, busy ? COPY.installing : (item.state === 'update' ? COPY.update : COPY.install));
    }

    function actions(item, state, handlers) {
        const template = item.template;
        const removing = Boolean(template) && state.busyId === template.id;
        const rows = [
            primary(item, state, handlers),
            template ? h('button', {
                class: 'market-action', id: 'market-uninstall', type: 'button',
                disabled: removing, onclick: () => handlers.onUninstall(template.id),
            }, removing ? COPY.removing : COPY.uninstall) : null,
        ].filter(Boolean);
        return rows.length ? h('div', { class: 'market-detail-actions' }, rows) : null;
    }

    // `pack_author.url` is the one remote value that reaches an href, and it
    // is validated server-side; the target still carries rel="noopener
    // noreferrer" because a new tab must not be handed a window reference.
    // An author with no name gets no link — an anchor with no accessible name
    // is a control a screen reader cannot announce.
    //
    // `Installed` rides this line rather than the hero: it is metadata about
    // the pack, the same kind of fact as who wrote it and which commit it was
    // read at, and it earned no more room than they get.
    function byline(item) {
        const author = item.author && item.author.name ? item.author : null;
        const sha = shortSha(item.commitSha);
        const parts = [];
        // FIRST, and it is new here: the hero drew a mark for the category and
        // named it nowhere, so a pack opened from the `Engineering` rail row
        // showed no category at all — and a decorative bubble beside no word
        // leaves the family carried by colour alone (SC 1.4.1). It is the same
        // kind of fact as the author and the pin, so it reads on their line
        // rather than taking a band of its own.
        const category = ITEMS.categoryLabel(item.category);
        if (category) parts.push(`${category} · `);
        if (author && author.url) {
            parts.push(`${COPY.by} `, h('a', {
                class: 'market-detail-author', href: author.url,
                target: '_blank', rel: 'noopener noreferrer',
            }, author.name));
        } else if (author) {
            parts.push(`${COPY.by} ${author.name}`);
        }
        if (sha) parts.push(`${parts.length ? ' · ' : ''}${COPY.pinned} ${sha}`);
        if (item.state === 'installed') {
            if (parts.length) parts.push(' · ');
            parts.push(h('span', { class: 'market-detail-installed' }, COPY.installed));
        }
        return parts.length ? h('p', { class: 'market-detail-by' }, parts) : null;
    }

    /**
     * The `✕` that dismisses the whole takeover, for whichever view is up.
     *
     * The takeover carries no footer — a full-screen surface puts its exit in
     * its own top-right corner, and the detail view used to offer that `✕` and
     * the modal's footer `Close` at once, two controls for one errand. One
     * builder, so browse cannot end up without an exit and the two cannot
     * drift; one view is mounted at a time, so the id names exactly one node.
     *
     * @param {object} handlers  Reads `onDismiss`.
     * @returns {HTMLElement} A real button with an accessible name — the glyph
     *   alone announces as a punctuation mark.
     */
    function dismissButton(handlers) {
        return h('button', {
            class: 'market-close', id: 'market-close', type: 'button',
            'aria-label': COPY.dismiss, onclick: () => handlers.onDismiss(),
        }, '✕');
    }

    // Two controls, two different errands: `‹` goes back to the grid this
    // replaced, and `✕` dismisses the takeover. Both are a glyph and nothing
    // else, and the pair now match: the word `Back` beside the chevron was the
    // only visible label in the bar, and it said less than the announced name
    // already does. The mark is aria-hidden, so the button announces as "Back
    // to the marketplace" rather than as a punctuation mark — the glyph is the
    // affordance and `aria-label` is the whole of the name.
    function bar(handlers) {
        return h('div', { class: 'market-detail-bar' },
            h('button', {
                class: 'market-detail-back', id: 'market-back', type: 'button',
                'aria-label': COPY.backLabel, onclick: () => handlers.onBack(),
            },
            h('span', { class: 'market-detail-back-mark', 'aria-hidden': 'true' }, '‹')),
            dismissButton(handlers));
    }

    /**
     * Build the detail view for the item the state has selected.
     *
     * Every string it renders — title, specialty, author name, and each section
     * body — is remote pack-authored data and reaches the document through
     * BossModDom.h as a text node. There is no markup-string path in this file.
     *
     * @param {object} state  BossModMarketplace's state. `selected` is the item
     *   to read, `busyId` gates the two actions, `sectionKey` is which of the
     *   pack's sections is open, and `error`, `notice` and `pendingUninstall`
     *   are the three things that can sit above the hero.
     * @param {object} handlers  onBack, onDismiss, onInstall, onUninstall,
     *   onUninstallConfirm, onUninstallCancel, onSection.
     * @returns {HTMLElement} The whole view, ready to replace the browse one.
     * @throws {Error} When nothing is selected. Rendering an empty detail would
     *   put the operator in a view with no content and no cause to read.
     */
    function render(state, handlers) {
        const item = state.selected;
        if (!item) throw new Error('[marketplace-detail] rendered with nothing selected');
        // An installed row whose catalog entry has gone bad, said in the view
        // where the pack is actually read. One wording, shared with the card.
        const note = WITHHELD.installedNote(item.catalogStatus);
        // Built before the tree so the rule above it is drawn only when there
        // is something under it to separate. A pack that carries no section at
        // all gets neither, rather than a line ruled under nothing.
        const reader = READER.reader(item, state, handlers);
        return h('section', {
            class: 'market-detail', id: 'market-detail', tabindex: '-1',
            'aria-label': COPY.detailLabel,
        },
        bar(handlers),
        state.error ? h('p', { class: 'market-error', role: 'alert' }, state.error) : null,
        state.notice ? h('p', { class: 'market-notice', role: 'status' }, state.notice) : null,
        state.pendingUninstall ? confirmStrip({
            text: COPY.uninstallAsk, id: 'market-uninstall-confirm',
            label: COPY.remove, tone: 'danger',
            onConfirm: () => handlers.onUninstallConfirm(state.pendingUninstall),
            onCancel: () => handlers.onUninstallCancel(),
        }) : null,
        // WHO, then WHAT IT COSTS: the name and everything that identifies it
        // on the left, the one action on the right. The name is no longer
        // stranded under the mark on a line of its own with the button already
        // scrolled past it.
        h('div', { class: 'market-detail-hero' },
            h('div', { class: 'market-detail-ident' },
                // The pack's FAMILY, not its initial: the mark used to be the
                // first letter of the title, which said what the line beside
                // it already said. It is the same bubble the card carries, so
                // the two views identify a pack the same way.
                categoryMark(item.category, 'lg'),
                h('div', { class: 'market-detail-names' },
                    h('h3', { class: 'market-detail-title' }, item.title),
                    // Only when it says something the title did not: the
                    // projection drops a specialty that merely repeats it.
                    item.specialty
                        ? h('p', { class: 'market-detail-specialty' }, item.specialty) : null,
                    byline(item))),
            actions(item, state, handlers)),
        note ? h('p', { class: 'market-detail-note' }, note) : null,
        // Both opening paragraphs, in the order they are read: the author's
        // lead-in — the prose before the first heading, which used to render
        // nowhere at all — and then the mission. They are the projection's,
        // not a second read of `sections` here: the card opens on the same two
        // and deciding them twice is how the two views come to disagree.
        item.intro ? h('p', { class: 'market-detail-intro' }, item.intro) : null,
        item.mission ? h('p', { class: 'market-detail-lead' }, item.mission) : null,
        // The view's ONE rule, and the only one it is allowed: what the pack is
        // for, above; what it undertakes to do, below.
        reader ? h('hr', { class: 'market-detail-rule' }) : null,
        reader);
    }

    return { COPY, categoryMark, confirmStrip, dismissButton, render };
})();
