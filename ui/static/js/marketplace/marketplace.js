/**
 * BossMod AI — the agent marketplace: browse the catalog, install, uninstall.
 *
 * The Agents dialog's Marketplace pane (context/agents-dialog.js), beside its
 * Add agent pane — not a nav place: browsing packs is a rare, deep-reading
 * task and BossModPlaces' six place ids are frozen, so widening the top-level
 * nav for a monthly action is the wrong trade. It is also the only route into
 * the local template library, which is why "Install from URL" lives here — as
 * an inline row in the header, and its trust question as an inline strip
 * beside it. Neither opens a second dialog: a focus trap stacked over a focus
 * trap is the failure the one-dialog guard exists for.
 *
 * NO MODAL OF ITS OWN. It used to be a takeover that Add agent closed itself
 * to open, and reopened itself behind when the takeover's `✕` was pressed —
 * the one `✕` in the app that meant "back" instead of "close everything", and
 * a marketplace opened from the rail menu had no way to Add agent at all. The
 * dialog owns the frame, its `✕` and the tabs now; this owns the host element
 * and everything drawn in it. Two things cross that seam, both as callbacks
 * the dialog hands in: a template the operator wants to start an agent from,
 * and "the library just changed".
 *
 * The pane holds TWO views and this owns which one is up: the browse grid,
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
        loadFailed: 'Couldn’t load the catalog.',
        installFailed: 'Install failed.',
        uninstallFailed: 'Uninstall failed.',
        urlRequired: 'Paste a GitHub URL to a pack file first.',
        removed: 'Template removed.',
    });

    /**
     * Build the Marketplace pane.
     *
     * Renders its browse view at once — so the dialog it is placed in finds
     * the Find box to put the keyboard on — but READS NOTHING until the first
     * `activate()`: the catalog is a remote read, and an operator who only
     * opened Add agent must not pay for it. From then on it loads the remote
     * catalog and the local library in parallel and renders from one state
     * object on every change.
     *
     * @param {object} deps
     * @param {(template: object) => void} deps.onUseTemplate  "Add agent from
     *   this": called with the installed `AgentTemplate` row of the pack being
     *   read (`item.template`), for the dialog to start a create form from.
     * @param {() => void} deps.onLibraryChanged  Called after a successful
     *   install or uninstall, once the local library has been re-read, so the
     *   Add agent picker can re-read it too. Never after a failure.
     * @returns {{element: HTMLElement, activate: () => void}} `element` is the
     *   `.market-host` to place. `activate` is "this pane is on screen": the
     *   first call starts the first load, and every later call does nothing —
     *   the pane keeps its catalog, its scroll and its open pack across a tab
     *   switch rather than reading them all again.
     * @throws {Error} When either callback is missing. A bridge that answers
     *   to nobody is a button that does nothing, and a silent one.
     */
    function createPane(deps) {
        if (!deps || typeof deps.onUseTemplate !== 'function') {
            throw new Error('[marketplace] deps.onUseTemplate is required');
        }
        if (typeof deps.onLibraryChanged !== 'function') {
            throw new Error('[marketplace] deps.onLibraryChanged is required');
        }
        const host = BossModDom.h('div', { class: 'market-host' });
        /** Set by the first activate(); the catalog is read once, lazily. */
        let started = false;
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
            // Whether the library changed AND was re-read. The dialog is told
            // after the finally rather than from inside the try, so a callback
            // that threw could never be reported as the install failing.
            let changed = false;
            try {
                const template = await API.installTemplate(body);
                state.templates = await API.listTemplates();
                changed = true;
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
            // The Add agent picker reads the same library; the template it
            // just gained is there when the operator switches back.
            if (changed) deps.onLibraryChanged();
        }

        async function runUninstall(templateId) {
            Object.assign(state, {
                busyId: templateId, error: null, notice: '', pendingUninstall: null,
            });
            rerender();
            let changed = false;
            try {
                await API.uninstallTemplate(templateId);
                state.templates = await API.listTemplates();
                changed = true;
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
            if (changed) deps.onLibraryChanged();
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
            // "Add agent from this". Only drawn for a pack the library holds,
            // so a pack without its installed row here is a detail view that
            // offered a button it had no template for — a bug, said as one.
            onUseTemplate(item) {
                if (!item.template) {
                    throw new Error(`[marketplace] "${item.key}" has no installed template to start from`);
                }
                deps.onUseTemplate(item.template);
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

        // Drawn now, read later: the browse view is up (in its loading state)
        // before the dialog places focus, so the Find box is there to take it.
        rerender();
        return {
            element: host,
            activate() {
                if (started) return;
                started = true;
                void load();
            },
        };
    }

    return { COPY, createPane };
})();
