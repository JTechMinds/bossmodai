/**
 * BossMod AI — the marketplace's browsing view, and the swap between the two.
 *
 * Header, rail and card grid, plus the render() that owns `host`: it decides
 * which of the two views is on screen, hands the detail one over to
 * marketplace-detail.js, and puts back what a rebuild destroyed. Built with
 * BossModDom.h: catalog titles, specialties, missions and author names are
 * REMOTE data and must never reach a markup-string path.
 *
 * A rebuild drops whatever node held focus, so render() puts it back: the
 * caller's `focusRequest` wins, and otherwise the element with the same id gets
 * it and its caret again, which is what lets the filter box re-render the grid
 * on every keystroke without losing the word being typed. Every control that
 * can be clicked and then rebuilt therefore carries an id, and onCategory and
 * onSelect are each handed their own so the state module can ask for that node
 * back — which is how the detail's `‹` returns the keyboard to the card it
 * was opened from. The rail and the cards NUMBER their rows rather than naming
 * them after the pack: a category id and a pack id are both catalog data, and
 * remote text has no business in a selector string.
 *
 * The browse scroller is rebuilt on every render and destroyed for as long as
 * the detail is open, so its offset is carried on the state and put back here.
 * That is the operator's reading position, not a measurement of the layout —
 * geometry stays CSS's, which is why nothing here asks how tall anything is.
 */
const BossModMarketplaceView = (() => {
    const { h, clear } = BossModDom;
    const ITEMS = BossModMarketplaceItems;
    const DETAIL = BossModMarketplaceDetail;
    const WITHHELD = BossModMarketplaceWithheld;

    const COPY = Object.freeze({
        find: 'Find agents',
        findHint: 'Filter by name, specialty or description',
        urlToggle: 'Install from URL',
        urlLabel: 'GitHub URL to a pack file',
        urlInstall: 'Install',
        railLabel: 'Catalog sections',
        // Two group headings, because the rail holds two different KINDS of
        // row. `Show` rather than `Scopes`: this same takeover reads a pack's
        // `In scope` and `Out of scope` a screen away, and one word meaning
        // two things on one surface is a word that means neither.
        scopeGroup: 'Show',
        categoryGroup: 'Categories',
        all: 'All',
        installed: 'Installed',
        loading: 'Loading the catalog…',
        failed: 'Couldn’t load the catalog.',
        empty: 'The catalog has no packs at this pin.',
        // Not the same sentence: the catalog DOES list packs, and the notice
        // under this line names every one of them and says why it is not here.
        emptyWithheld: 'No pack in this catalog can be shown at this pin.',
        retry: 'Try again',
        noMatch: 'No agent matches that filter.',
        installing: 'Installing…',
    });

    // A card's chip is a STATE, never a control. `Install` used to sit here on
    // an uninstalled card and invited a click that did nothing — the card
    // selects, and installing is the detail view's primary — so an uninstalled
    // card now carries no chip at all and the two that mean something keep it.
    const STATE_CHIP = Object.freeze({
        installed: 'Installed', update: 'Update available',
    });

    // "code-review" -> "Code Review". The DERIVATION moved to the projection
    // when the detail view came to need the same string — it cannot call this
    // file without a cycle — and this is the local name the rail and the card
    // spend it under. One definition, two views, no drift.
    function categoryLabel(slug) {
        return ITEMS.categoryLabel(slug);
    }

    // The filter box, the quiet URL door, and the takeover's one exit. The
    // door stays available while the catalog is down: a URL install does not
    // need the catalog. The `✕` is last on the line and so sits top-right,
    // which is both where the reference puts it and where the detail view's
    // own copy of it is — and it is the browse view's ONLY visible exit now
    // that the modal footer is gone, so it is never conditional.
    function head(state, handlers) {
        const find = h('input', {
            class: 'market-find', id: 'market-find', type: 'search',
            placeholder: COPY.findHint,
            oninput: (event) => handlers.onQuery(event.target.value),
        });
        find.value = state.query;
        const url = h('input', {
            class: 'market-url-input', id: 'market-url', type: 'url',
            placeholder: 'https://github.com/owner/repo/blob/<sha>/packs/name.yaml',
            oninput: (event) => handlers.onUrlChange(event.target.value),
        });
        url.value = state.urlValue;
        const busy = state.busyId === 'url';
        return h('div', { class: 'market-head' },
            h('div', { class: 'market-find-row' },
                h('label', { class: 'market-find-label', for: 'market-find' }, COPY.find),
                find),
            h('button', {
                class: 'market-url-toggle', id: 'market-url-toggle', type: 'button',
                'aria-expanded': state.urlOpen ? 'true' : 'false',
                onclick: () => handlers.onToggleUrl(),
            }, COPY.urlToggle),
            DETAIL.dismissButton(handlers),
            state.urlOpen ? h('div', { class: 'market-url-row' },
                h('label', { class: 'market-url-label', for: 'market-url' }, COPY.urlLabel),
                url,
                h('button', {
                    class: 'market-action primary', id: 'market-url-install',
                    type: 'button', disabled: busy,
                    onclick: () => handlers.onInstallUrl(),
                }, busy ? COPY.installing : COPY.urlInstall)) : null);
    }

    // What an install just said, and the trust question a URL install can
    // raise. Both belong to the browse view because the URL row that causes
    // them is here; the detail view carries its own copy of the same two for
    // the install and uninstall it owns. Outside .market-body, so neither is
    // scrolled away from the operator who has to answer it.
    function messages(state, handlers) {
        // A failed CATALOG read keeps its message and its Try again inside the
        // grid. Repeating it here would put two alerts on one failure.
        const error = state.status === 'failed' ? null : state.error;
        const trust = state.trustPrompt;
        if (!error && !state.notice && !trust) return null;
        return h('div', { class: 'market-messages' },
            error ? h('p', { class: 'market-error', role: 'alert' }, error) : null,
            state.notice
                ? h('p', { class: 'market-notice', role: 'status' }, state.notice) : null,
            trust ? DETAIL.confirmStrip({
                text: trust.message, id: 'market-trust-confirm',
                label: DETAIL.COPY.trustConfirm, tone: 'primary',
                onConfirm: () => handlers.onTrustConfirm(),
                onCancel: () => handlers.onTrustCancel(),
            }) : null);
    }

    // One row of the rail. `at` is its index across the WHOLE rail rather than
    // within its group: the id is what render() hands focus back to after the
    // click that destroyed the button, and a per-group number would name two
    // different rows.
    function railRow(row, at, state, handlers) {
        return h('li', {},
            h('button', {
                class: 'market-rail-item', type: 'button', id: `market-rail-${at}`,
                'aria-current': state.category === row.id ? 'true' : null,
                onclick: () => handlers.onCategory(row.id, `#market-rail-${at}`),
            },
            h('span', { class: 'market-rail-label' }, row.label),
            h('span', { class: 'market-rail-count' }, String(row.count))));
    }

    // A heading over a list, which is the treatment the roster rail already
    // gives `PEOPLE` and `THREADS` — one vocabulary for "these rows are a set"
    // rather than a second one invented here. Real structure and not a gap:
    // the list is NAMED by its heading, so a row announces which group it
    // belongs to. Every row stays a plain button, so the rail is still as many
    // tab stops as it has rows and Tab crosses both groups in order.
    function railGroup(id, title, rows, from, state, handlers) {
        return h('div', { class: 'market-rail-group' },
            h('h3', { class: 'market-rail-title', id }, title),
            h('ul', { class: 'market-rail-list', 'aria-labelledby': id },
                rows.map((row, at) => railRow(row, from + at, state, handlers))));
    }

    // Two groups, because one flat list said the catalog had four buckets and
    // one of them was the operator's own library. `All` and `Installed` are
    // SCOPES — which packs are on the table — and the rest are the catalog's
    // categories; they answer different questions and now look it.
    function rail(state, handlers) {
        const total = state.categories.reduce((n, group) => n + (group.packs || []).length, 0);
        const scopes = [
            { id: 'all', label: COPY.all, count: total },
            { id: 'installed', label: COPY.installed, count: state.templates.length },
        ];
        const categories = state.categories.map((group) => ({
            id: group.id, label: categoryLabel(group.id), count: (group.packs || []).length,
        }));
        return h('nav', { class: 'market-rail', 'aria-label': COPY.railLabel },
            railGroup('market-rail-scope', COPY.scopeGroup, scopes, 0, state, handlers),
            // Drawn only when the catalog has categories to put under it: a
            // failed read clears them, and a heading over no rows claims a
            // group that is not there.
            categories.length
                ? railGroup('market-rail-category', COPY.categoryGroup, categories,
                    scopes.length, state, handlers)
                : null);
    }

    // One button, phrasing content only — a control inside a control is not a
    // thing, which is why the author is plain here and a link in the detail.
    // `aria-current`, not `aria-pressed`: the card is not a toggle, it opens a
    // view, and the mark it keeps afterwards says "this is the one you were
    // reading" — the same word, and the same tint, the rail's live row uses.
    // The body is the projection's two opening paragraphs — the author's
    // lead-in, then the mission — never the raw string they were cut from:
    // "Mission: …" on a card is a parser's output, not a summary. Each is
    // clamped on its own, so a pack with a lead-in costs the card two lines
    // rather than pushing its mission off the bottom of the one clamp.
    //
    // Mark and name, then WHAT KIND, then what it does, then who wrote it. The
    // line under the name used to be the specialty and read "Code Auditor" under
    // "Code Auditor" on nearly every pack in the catalog — the projection drops
    // that echo, and the category takes the row it was wasting. It is the one
    // fact the grid sorts by that the card never showed, and it is title-cased
    // by the same helper the rail's rows are: one derivation, so a slug cannot
    // read two ways on one screen. Both routes always send a category, so an
    // empty label means an empty string, and an empty chip is a blue box that
    // says nothing.
    function card(item, state, handlers, at) {
        const id = `market-card-${at}`;
        const chip = STATE_CHIP[item.state] || null;
        const note = WITHHELD.installedNote(item.catalogStatus);
        const author = item.author && item.author.name ? item.author.name : null;
        const category = categoryLabel(item.category);
        return h('button', {
            class: 'market-card', type: 'button', id,
            'data-pack-id': item.packId || null,
            'data-installed': item.state === 'install' ? null : item.state,
            'aria-current': state.selectedId === item.key ? 'true' : null,
            onclick: () => handlers.onSelect(item.key, `#${id}`),
        },
        // The mark LEADS, the way it leads the detail hero: same builder, same
        // bubble, so a pack is identified the same way in the grid and in the
        // read. It does not replace the chip below it — a coloured `E` is a
        // scanning aid, not the word "Engineering", and colour may not be the
        // only carrier of a fact (SC 1.4.1). The two are on different bands
        // doing different jobs, so neither reads as the other said twice.
        h('span', { class: 'market-card-head' },
            DETAIL.categoryMark(item.category, 'md'),
            h('span', { class: 'market-card-title' }, item.title)),
        category || item.specialty ? h('span', { class: 'market-card-meta' },
            category ? h('span', { class: 'market-card-category' }, category) : null,
            item.specialty
                ? h('span', { class: 'market-card-specialty' }, item.specialty) : null) : null,
        item.intro ? h('span', { class: 'market-card-intro' }, item.intro) : null,
        item.mission ? h('span', { class: 'market-card-desc' }, item.mission) : null,
        // An installed row with no card behind it: gone from the repo, or
        // still listed and now refused. Two different facts, and the operator
        // is running agents built from this one either way.
        note ? h('span', { class: 'market-card-note' }, note) : null,
        author || chip ? h('span', { class: 'market-card-foot' },
            author ? h('span', { class: 'market-card-author' }, author) : null,
            chip ? h('span', { class: 'market-card-state' }, chip) : null) : null);
    }

    // Loading, failed and empty are three different answers and look it: a
    // failed read never renders as an empty grid.
    function gridInner(state, handlers) {
        if (state.status === 'loading') {
            return h('p', { class: 'market-status', role: 'status' }, COPY.loading);
        }
        if (state.status === 'failed') {
            return [
                h('p', { class: 'market-failed', role: 'alert' }, state.error || COPY.failed),
                // Lands the keyboard on the filter box for the same reason the
                // withheld notice's retry does: a read that works deletes the
                // very button that was pressed, and this one took focus to
                // <body> with it every time it succeeded.
                h('button', {
                    class: 'market-action', id: 'market-retry', type: 'button',
                    onclick: () => handlers.onRetry('#market-find'),
                }, COPY.retry),
            ];
        }
        if (!state.visible.length) {
            const bare = state.withheld.length ? COPY.emptyWithheld : COPY.empty;
            return h('p', { class: 'market-status', role: 'status' },
                state.status === 'empty' ? bare : COPY.noMatch);
        }
        return h('div', { class: 'market-cards' },
            state.visible.map((item, at) => card(item, state, handlers, at)));
    }

    // UNDER the grid, never instead of it. The packs above it loaded fine;
    // these are the rows the app declined to offer, and the point of reporting
    // them at all is that the operator owns the catalog they came from.
    function browse(state, handlers) {
        return h('div', { class: 'market-body' },
            rail(state, handlers),
            h('div', { class: 'market-grid' },
                gridInner(state, handlers),
                WITHHELD.notice(state.withheld, handlers)));
    }

    /**
     * Draw the whole marketplace into `host`, replacing what was there.
     *
     * @param {HTMLElement} host  Owned by the caller; emptied on every call.
     * @param {object} state  BossModMarketplace's state plus the derived
     *   `visible` items and `selected` item it computes before each render.
     *   `detailOpen` chooses the view; `focusRequest` is a selector this places
     *   focus on, set by the caller whenever a change removes the node that was
     *   holding it; `browseScroll` is the browse view's reading position, read
     *   and written here because this is what destroys and rebuilds it.
     * @param {object} handlers  onQuery, onCategory, onSelect, onSection,
     *   onBack, onDismiss, onRetry, onToggleUrl, onUrlChange, onInstallUrl,
     *   onInstall, onUninstall, onUninstallConfirm, onUninstallCancel,
     *   onTrustConfirm, onTrustCancel.
     * @returns {void}
     */
    function render(host, state, handlers) {
        const active = document.activeElement;
        const keep = active && active.id && host.contains(active) ? active.id : '';
        const caret = keep && typeof active.selectionStart === 'number'
            ? active.selectionStart : null;
        const leaving = host.querySelector('.market-body');
        if (leaving) state.browseScroll = leaving.scrollTop;
        clear(host);
        // A catalog that could not be READ keeps the grid: its message and its
        // Try again are in there, and the detail view has no room for either,
        // so a failed read is never allowed behind this door.
        const detail = Boolean(state.detailOpen && state.selected && state.status !== 'failed');
        if (detail) host.append(DETAIL.render(state, handlers));
        else {
            host.append(head(state, handlers));
            const said = messages(state, handlers);
            if (said) host.append(said);
            host.append(browse(state, handlers));
        }
        const scroller = host.querySelector('.market-body');
        if (scroller) scroller.scrollTop = state.browseScroll;
        const target = state.focusRequest
            ? host.querySelector(state.focusRequest)
            : (keep ? host.querySelector(`#${keep}`) : null);
        if (!target || !target.focus) return;
        target.focus();
        if (caret !== null && !state.focusRequest && target.setSelectionRange) {
            target.setSelectionRange(caret, caret);
        }
    }

    return { COPY, render };
})();
