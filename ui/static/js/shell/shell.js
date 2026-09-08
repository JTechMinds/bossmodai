/**
 * BossMod AI — application boot.
 *
 * Builds the store, the bus, and the persistent frame once, in the order that
 * guarantees every subscriber exists before the first broadcast arrives, then
 * hands the centre column to shell/navigator.js and connects the socket last.
 *
 * The frame is mounted for the life of the page and never swapped. Only the
 * centre place is, which is why the navigator is a separate module and the
 * disposers here are deliberately not retained.
 */
const BossModShell = (() => {

    // ─── Boot ───

    const WS_PATH = '/api/ws';

    /**
     * The full store shape (spec 1.3). Server collections that belong to one
     * component — transcripts, file listings, task lists — deliberately do not
     * live here.
     */
    const INITIAL_STATE = {
        place: 'chat',
        placeParams: {},
        conversationId: null,
        conversationKind: null,
        contextMode: 'office',
        deskAgentId: null,
        // Where the desk's file browser opens. Set from a note's desk_path so
        // "Open in Desk" lands on the file, not on the desk root.
        deskPath: null,
        roster: [],
        threads: [],
        rosterQuery: '',
        railCollapsed: false,
        runtimePaused: false,
        hasUsableModel: false,
        connection: 'connecting',
        needs: [],
        needsBarEnabled: true,
        needsBarDismissed: false,
    };

    function requireElement(id) {
        const element = document.getElementById(id);
        if (!element) throw new Error(`[shell] index.html is missing #${id}`);
        return element;
    }

    /**
     * Build the application: store, bus, frame, then the socket.
     *
     * The socket connects last, on purpose. Every subscriber must exist before
     * the first broadcast arrives, or the messages that land during boot are
     * lost the same way a reconnect loses them.
     *
     * @returns {Promise<void>}
     */
    async function boot() {
        const restored = BossModSession.load();
        const store = BossModStore.createStore(Object.assign({}, INITIAL_STATE));
        const bus = BossModBus.createBus(BossModBus.KNOWN_TOPICS);

        const layoutElement = requireElement('main-layout');
        const placeElement = requireElement('app-place');
        const rosterElement = requireElement('app-roster');
        const contextElement = requireElement('app-context');
        // Built before the frame so the bell, the bar and the toast all read a
        // queue that is already subscribed when the first broadcast lands.
        const needs = BossModNeeds.createNeedsStore({ store, bus, api: apiFetch });
        const shell = BossModNavigator.createNavigator({
            store,
            bus,
            container: placeElement,
            api: apiFetch,
            needs,
            contextEl: contextElement,
        });
        const navigate = (placeId, params) => shell.navigate(placeId, params);

        /** The gear and Escape share one entry point, so they cannot disagree. */
        function toggleSettings() {
            if (SettingsView.isOpen()) SettingsView.close();
            else SettingsView.open();
        }

        function closeSettings() {
            if (SettingsView.isOpen()) SettingsView.close();
        }

        // The frame is mounted for the lifetime of the page and never swapped,
        // so these disposers are deliberately not retained. Only places — which
        // the shell does swap — need their subscriptions drained.
        BossModHeader.mount(requireElement('app-header'), {
            store,
            apiFetch,
            navigate,
            needs,
            openSettings: toggleSettings,
        });
        BossModRoster.mount(rosterElement, {
            store,
            bus,
            apiFetch,
            navigate,
            // The create form lives in the Chat context column, so hiring is a
            // navigation rather than a second form owned by the rail.
            onHire: () => navigate('chat', { hire: true }),
        });
        BossModFooter.mount(requireElement('app-footer'), { store, bus });
        BossModBanners.mount({
            store,
            bus,
            apiFetch,
            pauseBanner: requireElement('runtime-pause-banner'),
            noModelBanner: requireElement('no-model-banner'),
        });
        // App-wide, not Chat-only: a need whose conversation is not open is
        // exactly the one the operator would otherwise never see.
        BossModNeedsToast.createToastHost({ store, needs, navigate });
        BossModShortcuts.bind({
            placeIds: BossModPlaces.PLACE_IDS,
            navigate,
            onEscape: closeSettings,
        });
        requireElement('no-model-banner-settings').addEventListener('click', toggleSettings);
        // Below 1200px the grid sheds a column; these are the buttons that
        // open what it shed (spec 10). Mounted after the header so they can be
        // inserted into it.
        BossModResponsive.mount({
            headerEl: requireElement('app-header'),
            layoutEl: layoutElement,
            rosterEl: rosterElement,
            placeEl: placeElement,
            contextEl: contextElement,
            store,
        });

        // The context column exists on Chat alone; every other place is two
        // columns wide (spec 3.1).
        function applyContextColumn(placeId) {
            const place = BossModPlaces.get(placeId);
            layoutElement.setAttribute('data-context', place.hasContext === true ? 'on' : 'off');
        }
        store.subscribe((s) => s.place, applyContextColumn);

        /**
         * Collapse or restore the roster track.
         *
         * An attribute on the grid rather than a class on the rail: the rail's
         * width IS a grid track, so narrowing the element inside it would leave
         * a 220px column with a 56px panel in it. The token override moves the
         * track, and shell.css hides what 56px cannot show.
         *
         * @param {boolean} collapsed
         */
        function applyRailCollapsed(collapsed) {
            layoutElement.setAttribute('data-rail', collapsed === true ? 'collapsed' : 'expanded');
        }
        store.subscribe((s) => s.railCollapsed, applyRailCollapsed);

        BossModSession.PERSISTED_KEYS.forEach((key) => {
            store.subscribe((s) => s[key], () => BossModSession.save(store.getState()));
        });

        // Stage one: the place can be checked against the registry with no
        // server data, so the operator lands where they left off immediately.
        const startup = BossModSession.validate(restored, {
            places: BossModPlaces.PLACE_IDS,
            agentIds: [],
            threadIds: [],
        });
        store.setState({
            contextMode: startup.contextMode,
            railCollapsed: startup.railCollapsed,
        });
        applyContextColumn(startup.place);
        // Applied, not left to the subscription: a session restored with the
        // rail already expanded writes the same value it started with, which
        // is not a change and would never fire.
        applyRailCollapsed(startup.railCollapsed);
        navigate(startup.place);

        // Stage two: a persisted conversation is only safe once the live agent
        // and thread ids are known. The first notification of each is the
        // first response, so nothing has to guess whether a list is empty or
        // merely unloaded.
        let sawRoster = false;
        let sawThreads = false;
        let sessionSettled = false;
        function settleSession() {
            if (sessionSettled || !sawRoster || !sawThreads) return;
            sessionSettled = true;
            const live = store.getState();
            const safe = BossModSession.validate(restored, {
                places: BossModPlaces.PLACE_IDS,
                agentIds: live.roster.map((agent) => agent.id),
                threadIds: live.threads.map((thread) => thread.id),
            });
            store.setState({
                conversationId: safe.conversationId,
                conversationKind: safe.conversationKind,
            });
        }
        store.subscribe((s) => s.roster, () => { sawRoster = true; settleSession(); });
        store.subscribe((s) => s.threads, () => { sawThreads = true; settleSession(); });

        /**
         * Load the runtime state and publish it, so the footer and the banners
         * take the boot value through the same path as every later broadcast.
         *
         * @returns {Promise<void>}
         */
        async function loadRuntimeState() {
            let response;
            try {
                response = await apiFetch('/api/runtime/state', { cache: 'no-store' });
            } catch (err) {
                console.error('[shell] could not load the runtime state', err);
                return;
            }
            if (!response.ok) {
                console.error(`[shell] runtime state request failed: HTTP ${response.status}`);
                return;
            }
            bus.publish('runtime_state', await response.json());
        }

        // After an outage the roster and the banners refresh themselves on the
        // same topic; the shell owns the runtime state and the footer notice.
        bus.subscribe('resync', () => {
            void (async () => {
                store.setState({ connection: 'resyncing' });
                await loadRuntimeState();
                store.setState({ connection: 'connected' });
            })();
        });

        await loadRuntimeState();

        const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const socket = BossModSocket.createSocket({
            bus,
            url: `${scheme}//${window.location.host}${WS_PATH}`,
            onStatus: (state) => store.setState({ connection: state }),
        });
        window.addEventListener('beforeunload', () => socket.close());
        socket.connect();
    }

    return { boot };
})();

document.addEventListener('DOMContentLoaded', () => { void BossModShell.boot(); });
