/**
 * BossMod AI — Focus chat live traffic on the one operator-invalidate bus.
 *
 * The conversation controller owns this registration for the life of the
 * surface. Sources expose onLiveEvent; this module fans bus topics into the
 * open source without re-subscribing on every roster click.
 */
const BossModConversationFocus = (() => {

    const FOCUS_TOPICS = Object.freeze([
        'chat_message',
        'chat_reset',
        'agent_presence',
        'channel_presence',
        'channel_message',
        'channel_updated',
        'meeting_message',
        'resync',
        'operator_invalidate',
    ]);

    /**
     * @param {object} deps
     * @param {() => { id: string|null, kind: string|null, source: object|null, pendingLive: object|null }} deps.getContext
     * @param {object} deps.cache
     * @param {(messages: object[]) => void} deps.paint
     * @param {() => void} deps.applyChrome
     * @param {() => void} deps.applyComposer
     * @param {object} deps.transcript
     * @param {(id: string, kind: string) => Promise<void>} deps.reopen
     * @returns {() => Promise<void>}
     */
    function createRefetch(deps) {
        const { getContext, cache, paint, applyChrome, applyComposer, transcript, reopen } = deps;
        return async function refetchOpenConversation() {
            const ctx = getContext();
            const { id, kind, source: src, pendingLive } = ctx;
            if (!src || !id) return;
            try {
                const messages = await src.load();
                const after = getContext();
                if (after.source !== src || after.id !== id || after.kind !== kind || after.pendingLive) return;
                cache.remember(id, messages);
                paint(messages);
                applyChrome();
                applyComposer();
            } catch (err) {
                console.error(`[conversation] live refetch failed for ${kind} ${id}`, err);
                const after = getContext();
                if (after.source === src && after.id === id) {
                    transcript.setStatus('error', {
                        message: (err && err.message) || 'This conversation could not be refreshed.',
                        onRetry: () => { void reopen(id, kind); },
                    });
                }
            }
        };
    }

    /**
     * @param {object} deps
     * @param {() => ((topic: string, data: any) => void)|null} deps.getLiveSink
     * @param {() => { id: string|null, kind: string|null }} deps.getOpenTarget
     * @param {() => Promise<void>} deps.refetchOpen
     * @returns {() => void} disposer
     */
    function attach({ getLiveSink, getOpenTarget, refetchOpen }) {
        return BossModOperatorInvalidate.register({
            id: 'conversation-focus',
            topics: FOCUS_TOPICS.slice(),
            onEvent(topic, data) {
                if (topic === 'operator_invalidate') {
                    if (!BossModOperatorInvalidate.touchesSurface(['chat', 'focus'], data)) return;
                    void refetchOpen();
                    return;
                }
                if (topic === 'resync') {
                    if (getOpenTarget().id) void refetchOpen();
                    return;
                }
                const sink = getLiveSink();
                if (sink) sink(topic, data);
            },
        });
    }

    return { attach, createRefetch, FOCUS_TOPICS };
})();
