/**
 * BossMod AI — one agent's browser view: the exact image its model was sent.
 *
 * Opened from the screen button in that agent's chat header. A read-only
 * debug surface for the operator: the latest screenshot at full size (the
 * scroller pans, the image is never downscaled) and every caption line.
 *
 * It does not poll. It listens to BossModBrowserVisionStatus, the one poller,
 * and refetches the image only when the item's `taken_at` changes. Images come
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
        ended: (agentName) => `${agentName}’s browser session ended.`,
    });

    /**
     * Open the viewer for one agent.
     *
     * @param {string} agentId
     * @param {string} agentName  For the title and the image's alt text.
     * @returns {void}
     */
    function openForAgent(agentId, agentName) {
        const host = h('div', { class: 'ext-scroll' });
        // status: loading | ready | failed | ended
        const state = { status: 'loading', item: null, src: null, error: '', shownAt: null };
        let everSeen = false;
        let closed = false;

        const unsubscribe = BossModBrowserVisionStatus.subscribe(() => { void sync(); });

        BossModOverlays.createModal({
            title: `${agentName} — browser`,
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
            const item = BossModBrowserVisionStatus.latest(agentId);
            if (!item) {
                // Once seen, a vanished view means the session ended (bv close,
                // the extension turned off, the app restarted); before that it
                // is simply the first poll still on its way.
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
            const lines = [
                `command: ${item.command}`,
                `url: ${item.url}`,
                `title: ${item.title}`,
                ...item.caption_lines,
                `taken: ${BossModFormat.formatRelativeTime(item.taken_at)}`,
            ];
            return [
                h('img', {
                    class: 'ext-live-full', id: 'ext-live-image', src: state.src,
                    alt: `Latest screenshot from ${agentName}: ${item.title}`,
                }),
                h('ul', { class: 'ext-live-caption' }, lines.map((line) => h('li', {}, line))),
            ];
        }

        function render() {
            clear(host);
            [].concat(content()).forEach((node) => host.append(node));
        }

        render();
        void sync();
    }

    /**
     * The `browserView` capability an agent conversation's header takes:
     * which agents have a view and change notifications (from the one
     * status poller), and this viewer to open.
     *
     * @returns {{hasView: Function, subscribe: Function, open: Function}}
     */
    function headerCapability() {
        return {
            hasView: BossModBrowserVisionStatus.hasView,
            subscribe: BossModBrowserVisionStatus.subscribe,
            open: openForAgent,
        };
    }

    return { openForAgent, headerCapability };
})();
