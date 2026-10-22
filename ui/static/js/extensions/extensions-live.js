/**
 * BossMod AI — one agent's live view: the exact image its model was sent.
 *
 * Opened from a live-view button in that agent's chat header — one per
 * live-view extension, titled with the label the extension gives its view
 * (e.g. "Browser view"). A read-only debug surface for the operator: the
 * latest screenshot at full size under a pinned band of its facts (the
 * scroller pans, the image is never downscaled, and the band stays put). The
 * band is three quiet lines — the page (its title and its URL, each only when
 * the item has one: a desktop has no URL), the command, and the extension's
 * facts — and shows only what the agent saw, verbatim, plus the capture time.
 * The URL is a link when it can go somewhere: http(s) opens in the system
 * browser, a local file opens its folder in the file manager.
 *
 * It does not poll. It listens to BossModLiveStatus, the one reader, and
 * refetches the image only when the item's `taken_at` changes. Images come
 * from an authenticated route, so they are fetched as blob URLs (a bare
 * <img src> would not carry the API token) and revoked when replaced or when
 * the viewer closes.
 */
const BossModExtensionsLive = (() => {
    const { h, clear } = BossModDom;

    const COPY = Object.freeze({
        loading: 'Loading the latest screenshot…',
        failed: 'Couldn’t load the screenshot.',
        retry: 'Try again',
        ended: (agentName) => `${agentName}’s session ended.`,
        folderFailed: 'Couldn’t open the folder.',
    });

    /**
     * Open one extension's viewer for one agent.
     *
     * @param {string} extensionId  A live-view extension BossModLiveStatus lists.
     * @param {string} agentId
     * @param {string} agentName  For the title and the image's alt text.
     * @returns {void}
     * @throws {Error} When the extension is not a listed live view (from
     *   BossModLiveStatus.label), before anything is opened.
     */
    function openForAgent(extensionId, agentId, agentName) {
        // Read first: an unknown extension throws before a subscription leaks.
        const title = `${agentName} — ${BossModLiveStatus.label(extensionId)}`;
        const host = h('div', { class: 'ext-live' });
        // status: loading | ready | failed | ended
        const state = { status: 'loading', item: null, src: null, error: '', shownAt: null };
        let everSeen = false;
        let closed = false;

        const unsubscribe = BossModLiveStatus.subscribe(() => { void sync(); });

        BossModOverlays.createModal({
            title,
            body: host,
            actions: [],
            size: 'panel',
            // Focus lands on the layer's head (its ✕), not inside the image.
            focusBody: false,
            onClose: () => {
                closed = true;
                unsubscribe();
                if (state.src) URL.revokeObjectURL(state.src);
                state.src = null;
            },
        });

        async function sync() {
            if (closed) return;
            const item = BossModLiveStatus.latest(extensionId, agentId);
            if (!item) {
                // Once seen, a vanished view means the session ended (the agent
                // closed it, the extension turned off, the app restarted);
                // before that it is simply the first read still on its way.
                if (everSeen) {
                    replaceSrc(null);
                    state.status = 'ended';
                    render();
                }
                return;
            }
            everSeen = true;
            state.item = item;
            if (state.status === 'ready' && state.shownAt === item.taken_at) {
                render();
                return;
            }
            await loadImage(item);
        }

        async function loadImage(item) {
            try {
                const src = await apiFetchBlobUrl(item.image_url);
                if (closed) {
                    URL.revokeObjectURL(src);
                    return;
                }
                replaceSrc(src);
                state.shownAt = item.taken_at;
                state.status = 'ready';
                state.error = '';
            } catch (err) {
                state.status = 'failed';
                state.error = String((err && err.message) || err);
            }
            render();
        }

        function replaceSrc(src) {
            if (state.src) URL.revokeObjectURL(state.src);
            state.src = src;
        }

        function content() {
            if (state.status === 'loading') {
                return h('p', { class: 'market-status', role: 'status' }, COPY.loading);
            }
            if (state.status === 'ended') {
                return h('p', { class: 'market-status', role: 'status' }, COPY.ended(agentName));
            }
            if (state.status === 'failed') {
                return [
                    h('p', { class: 'market-failed', role: 'alert' }, state.error || COPY.failed),
                    h('button', {
                        class: 'market-action', id: 'ext-live-retry', type: 'button',
                        onclick: () => {
                            state.status = 'loading';
                            render();
                            void loadImage(state.item);
                        },
                    }, COPY.retry),
                ];
            }
            const item = state.item;
            return h('img', {
                class: 'ext-live-full', id: 'ext-live-image', src: state.src,
                alt: item.title
                    ? `Latest screenshot from ${agentName}: ${item.title}`
                    : `Latest screenshot from ${agentName}`,
            });
        }

        function render() {
            clear(host);
            // The band describes an item, so only a shown image gets one.
            if (state.status === 'ready') host.append(statBand(state.item, extensionId, agentId));
            const scroller = h('div', { class: 'ext-scroll' });
            [].concat(content()).forEach((node) => scroller.append(node));
            host.append(scroller);
        }

        render();
        void sync();
    }

    /**
     * The facts above the image, as three quiet lines: the page with the
     * capture time, the command, and the extension's facts inline. The page
     * is the title, then the URL in parentheses; either one alone when the
     * other is blank or absent (a desktop has no URL); and no page line at
     * all when the item has neither, rather than a stand-in.
     *
     * Every value goes in as text exactly as received — no trimming, casing
     * or rewording — so the band says what the agent saw and nothing else.
     * A failed folder open is shown at the end of the band; the next render
     * rebuilds the band and clears it.
     *
     * @param {{command: string, url: string|null, title: string, taken_at: string,
     *   facts: Array<{label: string|null, value: string}>}} item  One /live item.
     * @param {string} extensionId  Whose view this is, for the folder button.
     * @param {string} agentId  Whose view this is, for the folder button.
     * @returns {HTMLElement} A `header.ext-live-band`.
     * @throws {Error} When `item.url` is neither a string nor null, when
     *   `item.facts` is not an array, or when a fact's label is neither null
     *   nor a non-empty string (a server contract break).
     */
    function statBand(item, extensionId, agentId) {
        if (item.url !== null && typeof item.url !== 'string') {
            throw new Error('[extensions-live] item.url must be a string or null');
        }
        if (!Array.isArray(item.facts)) {
            throw new Error('[extensions-live] item.facts must be an array');
        }
        item.facts.forEach((fact) => {
            if (fact.label !== null && (typeof fact.label !== 'string' || fact.label === '')) {
                throw new Error('[extensions-live] a fact label must be null or a non-empty string');
            }
        });
        const band = h('header', { class: 'ext-live-band' });

        function showBandError(message) {
            const previous = band.querySelector('.market-failed');
            if (previous) previous.remove();
            band.append(h('p', { class: 'market-failed', role: 'alert' }, message));
        }

        const url = item.url ? urlNode(item, extensionId, agentId, showBandError) : null;
        let page = null;
        if (item.title && url) page = [item.title, ' (', url, ')'];
        else if (item.title || url) page = item.title || url;
        band.append(
            h('div', { class: 'ext-live-identity' },
                page ? h('p', { class: 'ext-live-page' }, page) : null,
                h('time', {
                    class: 'ext-live-taken', datetime: item.taken_at,
                    title: BossModFormat.formatDateTime(item.taken_at),
                }, BossModFormat.formatRelativeTime(item.taken_at))),
            h('p', { class: 'ext-live-line' }, labelled('command', item.command)),
            h('p', { class: 'ext-live-line ext-live-facts' }, item.facts.flatMap((fact, index) => [
                index ? h('span', { class: 'ext-live-sep', 'aria-hidden': 'true' }, '|') : null,
                h('span', { class: 'ext-live-fact' },
                    fact.label === null ? valueSpan(fact.value) : labelled(fact.label, fact.value)),
            ])));
        return band;
    }

    function valueSpan(value) {
        return h('span', { class: 'ext-live-value' }, value);
    }

    function labelled(label, value) {
        return [h('span', { class: 'ext-live-label' }, `${label}:`), ' ', valueSpan(value)];
    }

    /**
     * The page's URL, verbatim, as the control its scheme allows: a link
     * for http(s) (the document-level BossModExternalOpen interceptor opens
     * it in the system browser), a link that opens the containing folder
     * for `file:`, and plain text for anything else (`about:blank`, `data:`).
     *
     * @param {{url: string}} item  An item that has a URL.
     * @param {string} extensionId
     * @param {string} agentId
     * @param {(message: string) => void} onError  Shows a failed folder open.
     * @returns {HTMLElement} `a` or `span`, each `.ext-live-url`.
     */
    function urlNode(item, extensionId, agentId, onError) {
        if (BossModExternalOpen.httpUrlFrom(item.url)) {
            return h('a', { class: 'ext-live-url', href: item.url, title: 'Open in your browser' }, item.url);
        }
        if (item.url.toLowerCase().startsWith('file:')) {
            // A link, not a button: a button is one atomic box, so a long
            // path could not wrap inline beside its parentheses. The real
            // href keeps Enter and copy-link working; the click never
            // navigates (BossModExternalOpen ignores file: links).
            return h('a', {
                class: 'ext-live-url', href: item.url, title: 'Open containing folder',
                onclick: (event) => {
                    event.preventDefault();
                    BossModDeskOpener.reveal({
                        api: apiFetch,
                        request: () => BossModExtensionsApi.openLiveFolder(extensionId, agentId),
                        describeFailure: async (res) => (await BossModFileOps.readApiError(res)) || COPY.folderFailed,
                        onError,
                    }).catch((err) => {
                        // reveal reports its own failures; this is one it did
                        // not expect (e.g. an unreadable reply), so the click
                        // still says it failed rather than doing nothing.
                        console.error('[extensions-live] open folder failed', err);
                        onError(COPY.folderFailed);
                    });
                },
            }, item.url);
        }
        return h('span', { class: 'ext-live-url' }, item.url);
    }

    /**
     * The `liveViews` capability an agent conversation's header takes: the
     * views each agent has and change notifications (from the one status
     * reader), and this viewer to open.
     *
     * @returns {{views: (agentId: string) => Array<{extensionId: string, label: string, icon: string}>,
     *   subscribe: Function,
     *   open: (extensionId: string, agentId: string, agentName: string) => void}}
     */
    function headerCapability() {
        return {
            views: BossModLiveStatus.views,
            subscribe: BossModLiveStatus.subscribe,
            open: openForAgent,
        };
    }

    return { openForAgent, headerCapability };
})();
