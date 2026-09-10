/**
 * BossMod AI — the agent marketplace: browse the catalog, install, uninstall.
 *
 * A full-screen takeover rather than a nav place. Browsing packs is a rare,
 * deep-reading task and BossModPlaces' six place ids are frozen, so widening
 * the top-level nav for a monthly action is the wrong trade. It is also the
 * only route into the local template library, which is why "Install from URL"
 * lives here — as an inline row in the header, and its trust question as an
 * inline strip beside it. Neither opens a second dialog: a focus trap stacked
 * over a focus trap is the failure the one-dialog guard exists for.
 *
 * The takeover holds TWO views and this owns which one is up: the browse grid,
 * and — once a card is picked — marketplace-detail.js's full-width reading
 * view of one pack. State and the three calls that change it live here; the
 * items both views read are marketplace-items.js's, and every node either puts
 * on screen is marketplace-view.js's.
 */
const BossModMarketplace = (() => {
    const API = BossModAgentTemplatesApi;
    const ITEMS = BossModMarketplaceItems;
    const VIEW = BossModMarketplaceView;

    const COPY = Object.freeze({
        title: 'Agent Marketplace',
        loadFailed: 'Couldn’t load the catalog.',
        installFailed: 'Install failed.',
        uninstallFailed: 'Uninstall failed.',
        urlRequired: 'Paste a GitHub URL to a pack file first.',
        removed: 'Template removed.',
    });

    /**
     * Open the marketplace takeover.
     *
     * Loads the remote catalog and the local library in parallel, then renders
     * from one state object on every change. Returns immediately; the surface
     * shows its loading state until both reads land.
     *
     * @param {object} [options]
     * @param {() => void} [options.onClosed] Called once after the takeover
     *   closes, however it closed — Esc, the `✕` in whichever view is up, or
     *   `close()`. The add-agent flow reopens its own dialog from here.
     * @returns {{close: () => void}} `close` is idempotent, as createModal's is.
     */
    function open(options) {
        const onClosed = (options && options.onClosed) || null;
        const host = BossModDom.h('div', { class: 'market-host' });
        // Assigned once createModal has run. `onDismiss` fires only from a
        // control inside the panel, which cannot exist before then.
        let handle = null;
        const state = {
            status: 'loading', categories: [], templates: [],
            // Every catalog row that did not become a card, flat and keyed by
            // pack id. The grid hides them; nothing here hides that it did.
            withheld: [], withheldByPackId: {},
            installedByPackId: {}, installedExtras: [],
            category: 'all', query: '',
            // `selectedId` is the pack being read, and survives `‹ Back`
            // so the grid can mark the card it was opened from. `detailOpen`
            // is which of the two views is up; `cardFocus` is that card's own
            // selector, and `browseScroll` the grid's reading position.
            selectedId: null, detailOpen: false, cardFocus: null, browseScroll: 0,
            // Which of the open pack's sections is being read. Null is "the
            // one it opens on", and it is cleared with every change of pack:
            // a section id held across packs would name a section the next one
            // may not carry.
            sectionKey: null,
            busyId: null, error: null, notice: '',
            trustPrompt: null, pendingUninstall: null,
            urlOpen: false, urlValue: '', pin: '',
            // Spent by the view, which places focus and hands it back: a
            // rebuilt tree has dropped whichever node was holding it.
            focusRequest: null,
        };

        function rerender() {
            state.visible = ITEMS.visible(state);
            state.selected = ITEMS.allItems(state)
                .find((item) => item.key === state.selectedId) || null;
            VIEW.render(host, state, handlers);
            state.focusRequest = null;
        }

        async function load() {
            state.status = 'loading';
            state.error = null;
            // A read that fails must not leave the last read's withheld rows
            // under its alert, nor go on labelling installed rows from a
            // catalog this app no longer holds.
            Object.assign(state, { withheld: [], withheldByPackId: {} });
            rerender();
            let catalog;
            let templates;
            try {
                [catalog, templates] = await Promise.all([
                    BossModAgentApi.fetchCatalog(),
                    API.listTemplates(),
                ]);
            } catch (err) {
                // Never an empty grid: an empty catalog and an unreachable one
                // are different answers and must not look the same. The browse
                // view is forced back up, because the message and its Try again
                // live in the grid and must not end up behind the detail.
                state.status = 'failed';
                state.detailOpen = false;
                // And the last read's catalog goes with it. Cleared HERE and
                // not beside the withheld rows above, because a reload still in
                // flight has lost nothing yet and the rail must not empty
                // itself under an operator who has only just pressed Try again.
                // A read that FAILED has lost it: the rail went on offering
                // category rows out of a catalog this app no longer holds,
                // each one counting packs it could not show.
                state.categories = [];
                state.error = (err && err.message) || COPY.loadFailed;
                rerender();
                return;
            }
            state.categories = Array.isArray(catalog.categories) ? catalog.categories : [];
            state.withheld = Array.isArray(catalog.withheld) ? catalog.withheld : [];
            state.pin = catalog.commit_sha || '';
            state.templates = Array.isArray(templates) ? templates : [];
            Object.assign(state, ITEMS.indexInstalled(
                state.templates, state.categories, state.withheld,
            ));
            const cards = state.categories.reduce((n, group) => n + (group.packs || []).length, 0);
            state.status = cards || state.installedExtras.length ? 'ready' : 'empty';
            rerender();
        }

        // One install path whatever asked for it, so the trust answer is the
        // same body re-issued with `confirm` rather than a second call site.
        async function runInstall(body, busyKey) {
            Object.assign(state, { busyId: busyKey, error: null, notice: '', trustPrompt: null });
            rerender();
            try {
                const template = await API.installTemplate(body);
                state.templates = await API.listTemplates();
                Object.assign(state, ITEMS.indexInstalled(
                    state.templates, state.categories, state.withheld,
                ));
                const extra = state.installedExtras.some((row) => row.id === template.id);
                // A URL install has no catalog card, so Installed is the only
                // rail row it appears under. Land the operator where its row is,
                // reading what they just installed. No card was clicked to get
                // here, so there is none for `‹ Back` to go back to.
                if (extra) state.category = 'installed';
                state.selectedId = extra ? `tpl:${template.id}` : `pack:${template.pack_id}`;
                Object.assign(state, { detailOpen: true, cardFocus: null, sectionKey: null });
                state.notice = `${template.title} installed.`;
                // Only a URL install finishes the URL row; a catalog install
                // must not wipe a half-typed URL out from under the operator.
                if (body.url) Object.assign(state, { urlOpen: false, urlValue: '' });
                state.focusRequest = '#market-detail';
            } catch (err) {
                if (err && err.code === 'trust_required') {
                    // Asked beside the URL row that raised it, in the browse
                    // view: a question the operator cannot reach is not asked.
                    state.trustPrompt = { body, message: err.message || '' };
                    state.detailOpen = false;
                    state.focusRequest = '#market-trust-confirm';
                } else {
                    state.error = (err && err.message) || COPY.installFailed;
                    // The detail is replaced whole and needs focus placed; in
                    // the browse view the button pressed survives by its id.
                    state.focusRequest = state.detailOpen ? '#market-detail' : null;
                }
            } finally {
                state.busyId = null;
                rerender();
            }
        }

        async function runUninstall(templateId) {
            Object.assign(state, {
                busyId: templateId, error: null, notice: '', pendingUninstall: null,
            });
            rerender();
            try {
                await API.uninstallTemplate(templateId);
                state.templates = await API.listTemplates();
                Object.assign(state, ITEMS.indexInstalled(
                    state.templates, state.categories, state.withheld,
                ));
                if (state.selectedId === `tpl:${templateId}`) state.selectedId = null;
                state.notice = COPY.removed;
            } catch (err) {
                state.error = (err && err.message) || COPY.uninstallFailed;
            } finally {
                state.busyId = null;
                // The library just changed shape, so the card selector being
                // held may not point at that card any more — under Installed
                // the card is gone outright. Dropped rather than followed to
                // whatever now sits at that position: `‹ Back` lands on the
                // filter box, which is always there.
                state.cardFocus = null;
                // A URL install has no catalog card behind it, so removing one
                // leaves the detail with nothing to read at all.
                if (!state.selectedId) state.detailOpen = false;
                state.focusRequest = state.detailOpen ? '#market-detail' : '#market-find';
                rerender();
            }
        }

        const handlers = {
            // The clicked row is one of the nodes the rebuild destroys, and
            // the one the operator is standing on: without this, filtering by
            // category drops focus to <body>.
            onCategory(id, focus) {
                Object.assign(state, { category: id, selectedId: null, pendingUninstall: null });
                state.focusRequest = focus || null;
                rerender();
            },
            onQuery(value) { state.query = String(value || ''); rerender(); },
            // `focus` is the clicked card's own selector, kept so that going
            // back puts the keyboard on that card rather than at the top of a
            // grid the operator had scrolled down.
            onSelect(key, focus) {
                Object.assign(state, {
                    selectedId: key, detailOpen: true, cardFocus: focus || null,
                    sectionKey: null, pendingUninstall: null, error: null, notice: '',
                });
                state.focusRequest = '#market-detail';
                rerender();
            },
            // Which section of the open pack is being read. `focus` is the tab
            // to land on — its own id after a click, the arrived-at one after
            // an arrow key, and NULL for a hover, because a pointer must never
            // take the keyboard off what the operator left it on.
            onSection(id, focus) {
                state.sectionKey = id;
                state.focusRequest = focus || null;
                rerender();
            },
            // The selection survives: the grid marks the card that was read.
            onBack() {
                state.focusRequest = state.cardFocus || '#market-find';
                Object.assign(state, { detailOpen: false, pendingUninstall: null });
                rerender();
            },
            // Only reachable from a control inside the mounted panel, so a
            // null handle is an impossible state and is reported as one rather
            // than swallowed into a click that does nothing.
            onDismiss() {
                if (!handle) throw new Error('[marketplace] dismissed before it was mounted');
                handle.close();
            },
            // The withheld notice's retry passes a landing spot, because a
            // reload that works deletes the very button that was pressed.
            onRetry(focus) {
                state.focusRequest = focus || null;
                void load();
            },
            onToggleUrl() {
                state.urlOpen = !state.urlOpen;
                state.focusRequest = state.urlOpen ? '#market-url' : '#market-url-toggle';
                rerender();
            },
            // Deliberately does not re-render: the operator is mid-word in the
            // field a rebuild would replace, and nothing else reads it yet.
            onUrlChange(value) { state.urlValue = String(value || ''); },
            onInstallUrl() {
                const url = state.urlValue.trim();
                if (!url) {
                    state.error = COPY.urlRequired;
                    state.focusRequest = '#market-url';
                    rerender();
                    return;
                }
                void runInstall({ url }, 'url');
            },
            // The pin that was DISPLAYED, not the live one: what installs is
            // what was read.
            onInstall(item) {
                void runInstall({ id: item.packId, ref: item.commitSha }, item.key);
            },
            onUninstall(templateId) {
                state.pendingUninstall = templateId;
                state.focusRequest = '#market-uninstall-confirm';
                rerender();
            },
            onUninstallConfirm(templateId) { void runUninstall(templateId); },
            onUninstallCancel() {
                state.pendingUninstall = null;
                state.focusRequest = '#market-detail';
                rerender();
            },
            onTrustConfirm() {
                const prompt = state.trustPrompt;
                if (!prompt) throw new Error('[marketplace] trust confirmed with no prompt open');
                void runInstall({ ...prompt.body, confirm: true }, 'url');
            },
            onTrustCancel() {
                state.trustPrompt = null;
                state.focusRequest = '#market-url-toggle';
                rerender();
            },
        };

        rerender();
        handle = BossModOverlays.createModal({
            title: COPY.title,
            body: host,
            size: 'takeover',
            // NO footer. A full-screen takeover carries its exit in its own
            // top-right corner, and marketplace-detail.js's `dismissButton`
            // puts it there in both views; a footer `Close` under that was a
            // second control for one errand. createModal builds the empty row
            // and the stylesheet takes it out of the flow. Esc still dismisses
            // through the focus trap, and both views open on something
            // focusable — the Find box in browse, `‹ Back` in the detail.
            actions: [],
            onClose: () => { if (onClosed) onClosed(); },
        });
        void load();
        return { close: handle.close };
    }

    return { COPY, open };
})();
