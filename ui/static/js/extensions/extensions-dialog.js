/**
 * BossMod AI — Add → Extensions: browse the extensions, then manage one.
 *
 * The controller, mirroring marketplace.js: it owns the state, the API calls,
 * the polling and the lifecycle of the live components, and draws nothing —
 * extensions-view.js does. A `takeover` from core/overlays.js (the
 * marketplace's size) holding TWO views: the grid of read-only cards and, once
 * one is picked, extension-detail.js's view of that extension, where it is
 * turned on, set up and managed. The open extension is a trail step
 * (`Extensions › Browser Vision`), so ‹, Esc and the crumb go back to the grid.
 *
 * THE HARD PART: the detail is rebuilt on every change, and two of its
 * sections hold live state a rebuild must not destroy — the Settings form
 * (half-typed values) and the Agents table (its own read, a switch
 * mid-request, focus). So those two are built ONCE per opened extension and
 * kept (`live`); every render hands them to the detail, which re-parents the
 * open one, and the view restores focus and the caret by id. They are
 * disposed when the detail closes (‹, Esc, the crumb, ✕, or opening another
 * extension), and the Agents table when the extension is turned off.
 *
 * A missing download asks first (the marketplace's confirm strip), then runs
 * with enable-on-success. While any download runs, or the open extension's
 * sign-in is pending, the list is re-read every 2 s. A successful toggle asks
 * BossModLiveStatus to re-read, so the chat headers' live-view buttons follow.
 */
const BossModExtensionsDialog = (() => {
    const { h } = BossModDom;
    const API = BossModExtensionsApi;
    const VIEW = BossModExtensionsView;
    const TITLE = 'Extensions';
    const POLL_MS = 2000;
    // The server's code for a settings save whose values were stored but
    // whose running extension could not apply them.
    const APPLY_FAILED = 'SERVICE_START_FAILED';

    const COPY = Object.freeze({
        notInstalled: 'That extension isn’t installed.',
    });

    const message = (err) => String((err && err.message) || err);

    /**
     * Open the Extensions dialog.
     *
     * @param {object} [options]
     * @param {string} [options.extensionId]  Open straight onto this
     *   extension's detail once the list has loaded (Settings → Connections'
     *   "Manage extension"). An id the list does not hold leaves the grid up
     *   and says so.
     * @returns {{close: () => void}}
     * @throws {Error} When `extensionId` is given but is not a non-empty string.
     */
    function open(options) {
        const { extensionId } = options || {};
        if (extensionId !== undefined && (typeof extensionId !== 'string' || !extensionId)) {
            throw new Error('[extensions-dialog] options.extensionId must be a non-empty string');
        }
        const host = h('div', { class: 'market-host' });
        const state = {
            // loading | failed | empty | ready; `actionError` is the last refusal.
            status: 'loading', items: [], loadError: '', actionError: '',
            // The rail row: 'all', 'on' or 'category:<slug>'.
            filter: 'all',
            // The extension being read survives ‹, so the grid marks its card;
            // `cardFocus` is that card's selector, `sectionKey` the open section.
            selectedId: null, detailOpen: false, cardFocus: null, sectionKey: null,
            // The "download first?" strip; a request in flight (controls disabled).
            confirming: false, busy: false,
            // The open extension's live reads, and why the last one failed.
            panel: null, provider: null, liveError: '',
            browseScroll: 0, detailScroll: 0, panelScroll: 0,
            // Spent by the view: a rebuilt tree has dropped the focused node.
            focusRequest: null,
        };
        /** The open extension's persistent nodes; see the header. */
        let live = null;
        let timer = null;
        let closed = false;
        /** Consumed by the first successful load. */
        let deepLink = extensionId || null;

        const modal = BossModOverlays.createModal({
            title: TITLE,
            body: host,
            actions: [],
            size: 'takeover',
            onClose: () => {
                closed = true;
                clearTimeout(timer);
                disposeLive();
            },
        });

        function selectedItem() {
            if (!state.selectedId) return null;
            const found = state.items.find((item) => item.id === state.selectedId);
            return found === undefined ? null : found;
        }

        function render() {
            state.selected = selectedItem();
            VIEW.render(host, state, handlers, live);
            state.focusRequest = null;
            syncSteps();
        }

        // The open extension as a crumb back to the grid (the view's test: a
        // failed read keeps the grid whatever `detailOpen` says).
        function syncSteps() {
            const showing = state.detailOpen && state.selected && state.status !== 'failed';
            modal.setSteps(showing ? [{ title: state.selected.name, onBack: () => handlers.onBack() }] : []);
        }

        function replaceItem(item) {
            state.items = state.items.map((existing) => (existing.id === item.id ? item : existing));
        }

        function disposeLive() {
            if (!live) return;
            if (live.settings) live.settings.form.dispose();
            if (live.agents) live.agents.dispose();
            live = null;
        }

        // The Settings form, built once for the opened extension. Its adapter
        // re-reads the item after a save, and after a save that stored the
        // values but could not apply them to the running extension — the
        // server has changed its state either way.
        function createLive(item) {
            live = { id: item.id, settings: null, agents: null };
            if (item.valid && item.settings) {
                const body = h('div', { class: 'ext-config' });
                const form = BossModConfigForm.create({
                    placement: 'inline',
                    body,
                    idPrefix: 'ext-settings',
                    adapter: {
                        load: () => API.getSettings(item.id),
                        save: async (values) => {
                            try {
                                return await API.putSettings(item.id, values);
                            } catch (err) {
                                if (err && err.code === APPLY_FAILED) void reloadItem();
                                throw err;
                            }
                        },
                    },
                    onSaved: () => { void reloadItem(); },
                });
                live.settings = { element: body, form };
                void form.load();
            }
            syncAgents(item);
        }

        // The Agents table exists exactly while the open extension is on and
        // has per-agent settings. No live components means the detail closed
        // while a request ran, and there is nothing to sync.
        function syncAgents(item) {
            if (!live || live.id !== item.id) return;
            const wanted = item.valid && item.enabled && Boolean(item.agent_config);
            if (wanted && !live.agents) {
                live.agents = BossModExtensionAgents.create({ extensionId: item.id, extensionName: item.name });
            } else if (!wanted && live.agents) {
                live.agents.dispose();
                live.agents = null;
            }
        }

        // The open extension's panel and provider reads. Each is read only
        // while it is on; a failure is said in its section, never shown as
        // the last good read. A reply for an extension no longer open is
        // dropped.
        async function loadLive(item) {
            let panel = null;
            let provider = null;
            let error = '';
            try {
                if (item.panel && item.enabled) panel = await API.panelRequest(item.id);
                if (item.model_provider && item.enabled) provider = await API.providerRequest(item.id);
            } catch (err) {
                error = message(err);
            }
            if (closed || state.selectedId !== item.id) return;
            Object.assign(state, { panel, provider, liveError: error });
        }

        function schedulePoll() {
            clearTimeout(timer);
            if (closed || state.busy) return;
            const installing = state.items.some((item) => item.setup && item.setup.state === 'installing');
            const pending = Boolean(state.detailOpen && state.provider && state.provider.pending);
            if (installing || pending) timer = setTimeout(() => { void load(false); }, POLL_MS);
        }

        async function load(showLoading) {
            clearTimeout(timer);
            if (showLoading) {
                state.status = 'loading';
                render();
            }
            let items;
            try {
                items = await API.listExtensions();
            } catch (err) {
                if (closed) return;
                // The message and its Try again live in the grid, so a failed
                // read always brings the grid back, and nothing from the last
                // read stays on screen under it.
                disposeLive();
                Object.assign(state, {
                    status: 'failed', loadError: message(err), items: [], detailOpen: false,
                });
                render();
                return;
            }
            if (closed) return;
            Object.assign(state, { items, status: items.length ? 'ready' : 'empty', loadError: '' });
            if (deepLink !== null) {
                const wanted = deepLink;
                deepLink = null;
                if (items.some((item) => item.id === wanted)) {
                    await handlers.onSelect(wanted, null);
                    return;
                }
                state.actionError = COPY.notInstalled;
            }
            const item = selectedItem();
            if (state.detailOpen && item) {
                // A download that just finished turns the extension on.
                syncAgents(item);
                if (item.enabled) await loadLive(item);
                if (closed) return;
            }
            render();
            schedulePoll();
        }

        // After a settings save: the item may have changed (a service
        // restarted, or could not start).
        async function reloadItem() {
            let items;
            try {
                items = await API.listExtensions();
            } catch (err) {
                if (closed) return;
                state.actionError = message(err);
                render();
                return;
            }
            if (closed) return;
            state.items = items;
            const item = selectedItem();
            if (state.detailOpen && item) {
                syncAgents(item);
                await loadLive(item);
                if (closed) return;
            }
            render();
            schedulePoll();
        }

        // One request for the open extension: its controls held disabled
        // while it runs, a refusal said above the hero, nothing the server
        // refused shown as done. The pressed control is rebuilt (and disabled
        // meanwhile), so the keyboard is held on `focusWhile` and then put on
        // `focusAfter`.
        async function runAction(focusWhile, focusAfter, work) {
            clearTimeout(timer);
            Object.assign(state, { busy: true, actionError: '' });
            state.focusRequest = focusWhile;
            render();
            try {
                await work();
            } catch (err) {
                if (!closed) state.actionError = message(err);
            }
            state.busy = false;
            if (closed) return;
            state.focusRequest = focusAfter;
            render();
            schedulePoll();
        }

        const handlers = {
            onFilter(id, focus) {
                state.filter = id;
                state.focusRequest = focus || null;
                render();
            },
            async onSelect(id, focus) {
                disposeLive();
                Object.assign(state, {
                    selectedId: id, detailOpen: true, cardFocus: focus || null, sectionKey: null,
                    confirming: false, actionError: '', panel: null, provider: null, liveError: '',
                });
                const item = selectedItem();
                if (!item) throw new Error(`[extensions-dialog] no extension "${id}" to open`);
                createLive(item);
                state.focusRequest = '#ext-detail';
                render();
                if (item.enabled) {
                    await loadLive(item);
                    if (closed || state.selectedId !== id || !state.detailOpen) return;
                    render();
                }
                schedulePoll();
            },
            // The selection survives: the grid marks the card that was read.
            // Reached from the frame's ‹, Esc or crumb, through the step.
            onBack() {
                disposeLive();
                Object.assign(state, { detailOpen: false, confirming: false, actionError: '' });
                state.focusRequest = state.cardFocus || '#ext-rail-0';
                render();
            },
            // `focus` is the tab to land on, and null for a hover.
            onSection(id, focus) {
                state.sectionKey = id;
                state.focusRequest = focus || null;
                render();
            },
            onToggle(pressed) {
                const id = state.selectedId;
                // A refusal leaves the item unchanged, so the re-render puts
                // the switch back where the server still has it.
                void runAction('#ext-detail', '#ext-enabled', async () => {
                    const updated = await API.setEnabled(id, pressed);
                    replaceItem(updated);
                    syncAgents(updated);
                    await loadLive(updated);
                    void BossModLiveStatus.refresh();
                });
            },
            onSetupAsk() {
                state.confirming = true;
                state.focusRequest = '#ext-setup-confirm';
                render();
            },
            onSetupConfirm() {
                const id = state.selectedId;
                state.confirming = false;
                void runAction('#ext-detail', '#ext-detail', async () => {
                    replaceItem(await API.startSetup(id, true));
                });
            },
            onSetupCancel() {
                state.confirming = false;
                state.focusRequest = '#ext-enabled';
                render();
            },
            onPanelAction(action) {
                const id = state.selectedId;
                void runAction('#ext-section-panel', '#ext-section-panel', async () => {
                    const panel = await API.panelRequest(id, action.key, action.values);
                    if (state.selectedId === id) state.panel = panel;
                });
            },
            onProviderAction(key, values) {
                const id = state.selectedId;
                void runAction('#ext-section-panel', '#ext-section-panel', async () => {
                    const provider = await API.providerRequest(id, key, values);
                    if (state.selectedId === id) state.provider = provider;
                });
            },
            async onRetryLive() {
                const item = selectedItem();
                state.liveError = '';
                state.focusRequest = '#ext-section-panel';
                render();
                await loadLive(item);
                if (closed) return;
                state.focusRequest = '#ext-section-panel';
                render();
                schedulePoll();
            },
            // A read that works deletes the very button that was pressed, so
            // the caller names where the keyboard lands.
            onRetry(focus) {
                state.focusRequest = focus || null;
                void load(true);
            },
        };

        void load(true);
        return { close: () => modal.close() };
    }

    return { open };
})();
