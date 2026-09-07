/**
 * BossMod AI — the Files place.
 *
 * The company-wide browser from company-files.js, re-hosted on the place
 * contract. It owns the state, the load generation, and the wiring;
 * files-toolbar.js owns the controls, file-grid.js everything painted, and
 * files-data.js the two reads.
 *
 * The toolbar is built once at mount and never replaced, so the caret stays in
 * the search box while results arrive — which is why this port needs no
 * "restore focus after render" step: nothing takes the focus away.
 *
 * Every fetch passes through one load generation, so a response for a folder
 * the operator has already left is dropped rather than painted, and no failure
 * is console-only: the error banner is the operator's copy of it.
 */
const BossModFilesPlace = (() => {
    const { clear } = BossModDom;
    const GRID = BossModFileGrid;
    const DATA = BossModFilesData;

    const EMPTY_STATE = { path: '/', query: '', mode: 'local',
        entries: [], crumbs: [], note: '', roots: [] };

    const disposers = [];
    const state = Object.assign({}, EMPTY_STATE);
    let ctxRef = null;
    let load = null;
    let toolbar = null;
    let rowMenu = null;
    let frame = null;

    /** Delegates to the frame, which owns the banner. @param {string} message */
    function setError(message) { frame.setError(message); }

    // ─── Painting ───

    function paint() {
        if (state.mode === 'global') {
            GRID.paintSearchBar(frame.crumbs, state.query, () => {
                toolbar.clearSearch();
                state.query = '';
                void navigateTo(state.path);
            });
        } else {
            GRID.paintCrumbs(frame.crumbs, state.crumbs, (path) => { void navigateTo(path); });
        }
        GRID.paintNotice(frame.notice, {
            note: state.note, roots: state.roots, hidden: state.mode === 'global',
        }, openHostRoots);

        const rows = GRID.visibleRows(state.entries, state.query, state.mode === 'global');
        frame.summary.textContent = GRID.summarise(rows);
        clear(frame.body);
        frame.body.append(GRID.renderList(rows, {
            query: state.query,
            showPath: state.mode === 'global',
            onOpen: openEntry,
            onMenu: showRowMenu,
        }));
    }

    function applyListing(listing) {
        Object.assign(state, {
            entries: listing.entries, crumbs: listing.crumbs, note: listing.note,
            roots: listing.roots, path: listing.path, mode: 'local',
        });
        paint();
    }

    // ─── Loading ───

    /**
     * Load `state.path`. Never rejects; a failure becomes the error state.
     * @returns {Promise<void>}
     */
    async function refresh() {
        const loadId = load.next();
        const requested = state.path;
        frame.setBody(GRID.renderSkeleton());
        let payload;
        try {
            payload = await DATA.loadPath(ctxRef.api, requested);
        } catch (err) {
            if (!load.isCurrent(loadId) || state.path !== requested) return;
            console.error('[files] load failed', err);
            frame.setBody(GRID.renderError((err && err.message) || 'The request failed.',
                state.path, () => { void refresh(); }));
            return;
        }
        if (!load.isCurrent(loadId) || state.path !== requested) return;
        if (payload.kind === 'file') {
            openViewer(payload.path || requested);
            state.path = DATA.parentVirtualPath(payload.path || requested);
            if (state.path !== requested) await refresh();
            return;
        }
        setError('');
        applyListing(DATA.toListing(payload, requested));
    }

    async function navigateTo(path) {
        state.path = path || '/';
        state.query = '';
        state.mode = 'local';
        setError('');
        await refresh();
    }

    /**
     * Search the whole workspace. Never rejects; a failure reaches the banner.
     * @param {string} query
     * @returns {Promise<void>}
     */
    async function searchAll(query) {
        const loadId = load.next();
        state.query = query;
        let rows;
        try {
            rows = await DATA.search(ctxRef.api, query);
        } catch (err) {
            if (!load.isCurrent(loadId)) return;
            console.error('[files] search failed', err);
            setError((err && err.message) || 'Search failed');
            return;
        }
        if (!load.isCurrent(loadId)) return;
        Object.assign(state, { entries: rows, crumbs: [], mode: 'global' });
        setError('');
        paint();
    }

    /**
     * Filter the folder already on screen.
     *
     * Bumping the generation invalidates a global search still in flight, so
     * its rows cannot land on the folder listing. Dropping BELOW the search
     * threshold also re-reads the folder: the rows on screen are hits from all
     * over the workspace, and filtering those under this folder's breadcrumbs
     * would be a listing that lies about where it is.
     *
     * @param {string} query
     * @returns {void}
     */
    function filterHere(query) {
        const wasGlobal = state.mode === 'global';
        load.next();
        state.query = query;
        state.mode = 'local';
        if (wasGlobal) {
            void refresh();
            return;
        }
        paint();
    }

    /**
     * Open a path the operator typed, using the server's own answer about what
     * it is rather than guessing from the name. Never rejects.
     *
     * @param {string} named
     * @returns {Promise<void>}
     */
    async function openNamedPath(named) {
        setError('');
        let payload;
        try {
            payload = await DATA.loadPath(ctxRef.api, named);
        } catch (err) {
            console.error('[files] named path open failed', err);
            setError((err && err.message) || 'Could not open that path');
            return;
        }
        if (payload.kind === 'file') {
            openViewer(payload.path || named);
            return;
        }
        applyListing(DATA.toListing(payload, named));
    }

    // ─── Actions ───

    function openViewer(path) {
        void BossModFileViewer.open(path, { api: ctxRef.api }).catch((err) => {
            setError(`Could not open ${path}: ${(err && err.message) || 'the request failed.'}`);
        });
    }

    function openEntry(entry) {
        if (entry.is_dir === true) void navigateTo(entry.path);
        else openViewer(entry.path);
    }
    function showRowMenu(entry, anchor, at) {
        if (rowMenu) rowMenu.close();
        rowMenu = BossModFileActions.openMenu({
            entry,
            anchor,
            at,
            onChoose: (action, chosen) => {
                rowMenu = null;
                BossModFileActions.run(action, chosen, {
                    api: ctxRef.api,
                    onChanged: () => { void refresh(); },
                    onError: setError,
                });
            },
        });
    }

    function openHostRoots() {
        BossModHostRoots.openHostRoots({
            api: ctxRef.api,
            roots: state.roots,
            onSaved: () => { void refresh(); },
        });
    }

    function buildToolbar(ctx) {
        return BossModFilesToolbar.createToolbar({
            onOpenPath: (named) => { void openNamedPath(named); },
            onSearchAll: (query) => { void searchAll(query); },
            onFilterHere: filterHere,
            onCreate: (kind) => BossModFileOps.showCreateDialog({
                api: ctx.api,
                parentPath: state.path,
                kind,
                onComplete: () => { void refresh(); },
            }),
            onHostRoots: openHostRoots,
            onOpenFolder: () => {
                void BossModFolderOpener.openFolder({
                    api: ctx.api, path: state.path, onError: setError,
                });
            },
            onRefresh: () => { void refresh(); },
        });
    }

    return {
        label: 'Files',
        icon: 'folder',

        /**
         * @param {HTMLElement} el
         * @param {object} ctx  `{ store, bus, api, needs, navigate }`.
         * @returns {void}
         */
        mount(el, ctx) {
            ctxRef = ctx;
            load = BossModGates.createLoadGeneration();
            Object.assign(state, EMPTY_STATE,
                { path: ctx.store.getState().placeParams.path || '/' });
            toolbar = buildToolbar(ctx);
            frame = GRID.createFrame(toolbar.element);

            clear(el);
            el.append(frame.element);

            disposers.push(ctx.bus.subscribe('resync', () => BossModFilesPlace.resync()));
            void refresh();
        },

        /**
         * Re-read the folder after an outage without remounting, so the search
         * text and the typed path survive the gap (spec 1.4).
         * @returns {void}
         */
        resync() {
            if (!ctxRef) return;
            void refresh();
        },

        /**
         * Drain every subscription, timer, and overlay.
         * @returns {void}
         */
        unmount() {
            disposers.splice(0).forEach((off) => off());
            if (load) load.next();
            if (rowMenu) rowMenu.close();
            if (toolbar) toolbar.destroy();
            BossModFileViewer.close();
            Object.assign(state, EMPTY_STATE);
            rowMenu = null;
            toolbar = null;
            frame = null;
            ctxRef = null;
        },

        openNamedPath,
    };
})();

BossModPlaces.register('files', BossModFilesPlace);
