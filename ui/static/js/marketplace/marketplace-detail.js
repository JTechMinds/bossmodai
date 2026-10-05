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
 * It loads BEFORE marketplace-view.js and owns `confirmStrip` and
 * `categoryMark` because both views spend them — the trust question is asked
 * in the browse view beside the URL row that raised it, the uninstall question
 * is asked here, and the bubble that says which family a pack belongs to leads
 * its card and its hero alike. One builder each, two call sites, and no import
 * cycle for index.html's load order to fail on. Neither way out is built here:
 * the takeover's one `✕` is the modal frame's, and the way back to the grid is
 * the pane's `lead` (marketplace.js) — the frame's shared back chevron on the
 * dialog's title row, shown while this view is up.
 */
const BossModMarketplaceDetail = (() => {
    const { h } = BossModDom;
    const ITEMS = BossModMarketplaceItems;
    const WITHHELD = BossModMarketplaceWithheld;
    const READER = BossModMarketplaceSections;
    const { formatCalendarDay } = BossModFormat;

    const COPY = Object.freeze({
        by: 'By',
        version: 'version',
        install: 'Install',
        update: 'Update',
        installed: 'Installed',
        installing: 'Installing…',
        use: 'Add agent from this',
        uninstall: 'Uninstall',
        removing: 'Removing…',
        uninstallAsk: 'Remove this template from your library? Agents already '
            + 'created from it are unaffected.',
        // A local template is not an install to undo: there is no pack behind
        // it to reinstall from, so the word and the question both say so.
        deleteLocal: 'Delete',
        deleteLocalAsk: 'Delete this local template? It exists only on this '
            + 'machine; agents created from it are unaffected.',
        remove: 'Remove',
        cancel: 'Cancel',
        trustConfirm: 'Install anyway',
        detailLabel: 'Agent detail',
    });

    /**
     * The bubble that says which FAMILY a pack belongs to.
     *
     * The BUILDER moved to marketplace/pack-card.js when the Add agent picker
     * became a third caller — the grid, this hero and the picker must identify
     * a pack the same way, and the module that owns the card is where the mark
     * that leads it belongs. Re-exported here rather than re-pointed at every
     * call site: this module's `confirmStrip` is already what both views reach
     * through, and one of the two names moving would be the only thing that
     * changed.
     *
     * @param {string|null} slug  The catalog's category id.
     * @param {'chip'|'sm'|'md'|'lg'} size
     * @returns {HTMLElement}
     */
    function categoryMark(slug, size) {
        return BossModPackCard.categoryMark(slug, size);
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
    // -current pack has nothing to install — its state moved to the byline —
    // because the biggest, most prominent element on the screen must not be
    // the one thing that cannot be pressed. This leaves the slot EMPTY for such
    // a pack, and `use()` below is what fills it. `Uninstall` never inherits
    // it: it is the destructive action and stays a quiet secondary beside it.
    function primary(item, state, handlers) {
        if (item.state === 'installed') return null;
        const busy = state.busyId === item.key;
        return h('button', {
            class: 'market-action primary market-action-lead', id: 'market-install',
            type: 'button', disabled: busy, onclick: () => handlers.onInstall(item),
        }, busy ? COPY.installing : (item.state === 'update' ? COPY.update : COPY.install));
    }

    // "Add agent from this": the bridge from reading a pack to hiring from it,
    // on any pack the library already holds. On an installed-and-current pack
    // it IS the primary — the slot `primary()` leaves empty, and the one
    // thing left to do with that pack. Beside `Update` it is a quiet
    // secondary: bringing the template up to date is the better errand, but
    // the installed one is already usable. The dialog switches to its Add
    // agent tab and starts the create form from the installed row.
    //
    // WITHHELD while this pack is being written — its update or install
    // (`busyId` is the item's key) or its uninstall (the template's id). The
    // row it would hand over is the one that write is replacing or removing,
    // so a click mid-update started a form from the version being retired.
    function use(item, state, handlers) {
        const lead = item.state === 'installed';
        const writing = state.busyId === item.key || state.busyId === item.template.id;
        return h('button', {
            class: lead ? 'market-action primary market-action-lead' : 'market-action',
            id: 'market-use', type: 'button', disabled: writing,
            onclick: () => handlers.onUseTemplate(item),
        }, COPY.use);
    }

    function actions(item, state, handlers) {
        const template = item.template;
        const removing = Boolean(template) && state.busyId === template.id;
        const rows = [
            primary(item, state, handlers),
            template ? use(item, state, handlers) : null,
            template ? h('button', {
                class: 'market-action', id: 'market-uninstall', type: 'button',
                disabled: removing, onclick: () => handlers.onUninstall(template.id),
            }, removing ? COPY.removing : (item.local ? COPY.deleteLocal : COPY.uninstall)) : null,
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
    // the pack, the same kind of fact as who wrote it and which version is
    // installed, and it earned no more room than they get. The version is a
    // DATE (`Sep 14 version`), never a commit hash, and only an installed
    // template has one: an uninstalled card's would be the catalog pin's,
    // which is not shown.
    function byline(item) {
        const author = item.author && item.author.name ? item.author : null;
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
        if (item.commitDate) {
            parts.push(`${parts.length ? ' · ' : ''}${formatCalendarDay(item.commitDate)} ${COPY.version}`);
        }
        if (item.state === 'installed') {
            if (parts.length) parts.push(' · ');
            parts.push(h('span', { class: 'market-detail-installed' }, COPY.installed));
        }
        return parts.length ? h('p', { class: 'market-detail-by' }, parts) : null;
    }

    /**
     * Build the detail view for the item the state has selected.
     *
     * Every string it renders — title, specialty, author name, and each section
     * body — is remote pack-authored data and reaches the document through
     * BossModDom.h as a text node. There is no markup-string path in this file.
     *
     * @param {object} state  BossModMarketplace's state. `selected` is the item
     *   to read, `busyId` gates install and uninstall, `sectionKey` is which of the
     *   pack's sections is open, and `error`, `notice` and `pendingUninstall`
     *   are the three things that can sit above the hero.
     * @param {object} handlers  onInstall, onUseTemplate, onUninstall,
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
        state.error ? h('p', { class: 'market-error', role: 'alert' }, state.error) : null,
        state.notice ? h('p', { class: 'market-notice', role: 'status' }, state.notice) : null,
        state.pendingUninstall ? confirmStrip({
            text: item.local ? COPY.deleteLocalAsk : COPY.uninstallAsk,
            id: 'market-uninstall-confirm',
            label: item.local ? COPY.deleteLocal : COPY.remove, tone: 'danger',
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

    return { COPY, categoryMark, confirmStrip, render };
})();
