/**
 * BossMod AI — the Extensions view's browse grid, and the swap between the two.
 *
 * The marketplace's browse pattern, spent on extensions: a rail of filters
 * (Show: All / On, then the extensions' own categories) beside a grid of
 * READ-ONLY cards — the marketplace's BossModPackCard, so a card is one
 * `<button>` that opens the extension and holds no control of its own. The
 * render() here owns `host`: it decides which of the two views is up, hands
 * the detail over to extension-detail.js, and puts back what a rebuild
 * destroyed — mirroring marketplace-view.js.
 *
 * A rebuild drops whatever node held focus, so render() puts it back: the
 * controller's `focusRequest` wins, and otherwise the element with the same id
 * gets it and its caret again — which is how a half-typed Settings value keeps
 * the keyboard through a poll. The rail and the cards NUMBER their rows rather
 * than naming them after an extension: an extension id is manifest data, and
 * manifest text has no business in a selector string.
 *
 * Scroll offsets are the operator's reading position, carried on the state:
 * the browse scroller always, and — because the detail now re-renders on a
 * timer while a download or a sign-in is pending — the detail column for the
 * same extension. The detail column is that view's one scroller: its section
 * panel has no scroller of its own (extensions.css). Geometry stays CSS's;
 * nothing here measures a box.
 *
 * Built with BossModDom.h: names, summaries and authors are manifest data.
 */
const BossModExtensionsView = (() => {
    const { h, clear } = BossModDom;
    const ITEMS = BossModExtensionItems;

    const COPY = Object.freeze({
        railLabel: 'Extension groups',
        loading: 'Loading extensions…',
        failed: 'Couldn’t load extensions.',
        retry: 'Try again',
        empty: 'No extensions installed. Drop an extension folder into extensions/ and restart BossMod to load it.',
        noneOn: 'No extension is on yet.',
        noMatch: 'No extension in this group.',
    });

    /** Host → which extension its detail last drew, for the scroll hand-back. */
    const SHOWN = new WeakMap();

    // What the last action said. Outside the scroller, so it is never
    // scrolled away from the operator who has to read it.
    function messages(state) {
        if (!state.actionError) return null;
        return h('div', { class: 'market-messages' },
            h('p', { class: 'market-error', role: 'alert' }, state.actionError));
    }

    // Loading, failed, empty and "nothing in this group" are four different
    // answers and look it: a failed read never renders as an empty grid.
    function gridInner(state, handlers) {
        if (state.status === 'loading') {
            return h('p', { class: 'market-status', role: 'status' }, COPY.loading);
        }
        if (state.status === 'failed') {
            return [
                h('p', { class: 'market-failed', role: 'alert' }, state.loadError || COPY.failed),
                // Lands the keyboard on the rail's first row: a read that works
                // deletes the very button that was pressed.
                h('button', {
                    class: 'market-action', id: 'ext-retry', type: 'button',
                    onclick: () => handlers.onRetry('#ext-rail-0'),
                }, COPY.retry),
            ];
        }
        if (state.status === 'empty') {
            return h('p', { class: 'market-status', role: 'status' }, COPY.empty);
        }
        const visible = ITEMS.visible(state.items, state.filter);
        if (!visible.length) {
            return h('p', { class: 'market-status', role: 'status' },
                state.filter === 'on' ? COPY.noneOn : COPY.noMatch);
        }
        return h('div', { class: 'market-cards' }, visible.map((item, index) => {
            const id = `ext-card-${index}`;
            return BossModPackCard.packCard(ITEMS.cardView(item), {
                id,
                selected: state.selectedId === item.id,
                onSelect: () => handlers.onSelect(item.id, `#${id}`),
            });
        }));
    }

    function browse(state, handlers) {
        return h('div', { class: 'market-body' },
            BossModFilterRail.createRail({
                label: COPY.railLabel,
                idPrefix: 'ext-rail',
                groups: ITEMS.railGroups(state.items),
                current: state.filter,
                onSelect: handlers.onFilter,
            }).element,
            h('div', { class: 'market-grid' }, gridInner(state, handlers)));
    }

    /**
     * Draw the whole Extensions view into `host`, replacing what was there.
     *
     * @param {HTMLElement} host  The `.market-host` the controller owns; emptied
     *   on every call.
     * @param {object} state  BossModExtensionsDialog's state plus the derived
     *   `selected` item. `detailOpen` chooses the view (a failed read always
     *   keeps the grid, where its message and Try again live); `focusRequest`
     *   is a selector focus is placed on; `browseScroll` and `detailScroll`
     *   are read and written here, because this is what destroys and
     *   rebuilds the scrollers they belong to.
     * @param {object} handlers  The browse view's onFilter, onSelect and
     *   onRetry, plus the detail's (see extension-detail.js).
     * @param {object|null} live  The controller's persistent Settings and
     *   Agents nodes for the open extension, handed to the detail.
     * @returns {void}
     */
    function render(host, state, handlers, live) {
        const active = document.activeElement;
        const keep = active && active.id && host.contains(active) ? active.id : '';
        const caret = keep && typeof active.selectionStart === 'number' ? active.selectionStart : null;
        const leavingBrowse = host.querySelector('.market-body');
        if (leavingBrowse) state.browseScroll = leavingBrowse.scrollTop;
        const leavingDetail = host.querySelector('.market-detail');
        const shown = SHOWN.has(host) ? SHOWN.get(host) : null;
        const sameItem = Boolean(leavingDetail && shown && shown.id === state.selectedId);
        state.detailScroll = sameItem ? leavingDetail.scrollTop : 0;
        clear(host);
        SHOWN.delete(host);

        const detail = Boolean(state.detailOpen && state.selected && state.status !== 'failed');
        if (detail) {
            host.append(BossModExtensionDetail.render(state, handlers, live));
            SHOWN.set(host, { id: state.selectedId });
        } else {
            const said = messages(state);
            if (said) host.append(said);
            host.append(browse(state, handlers));
        }

        const scroller = host.querySelector('.market-body');
        if (scroller) scroller.scrollTop = state.browseScroll;
        const read = host.querySelector('.market-detail');
        if (read) read.scrollTop = state.detailScroll;
        // The menu-select chevrons are placeholders until painted. Scoped to
        // this host, before the focus hand-back only because that half
        // returns early when there is nothing to hand focus to.
        BossModIcons.paint(host, 'extensions-view');
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
