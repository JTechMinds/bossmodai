/**
 * BossMod AI — the single WebSocket.
 *
 * Owns connection, exponential backoff, the unload guard, and message
 * fan-out onto bus topics. It makes no routing decisions and knows about no
 * other module.
 *
 * The WebSocket is a fire-and-forget broadcast with no replay buffer, so a
 * dropped connection is silent data loss. `resync` exists to say "we may have
 * missed things" — distinct from world_update's "here is the current world",
 * because only the former justifies discarding a loaded transcript.
 */
const BossModSocket = (() => {
    const RECONNECT_MIN_MS = 1000;
    const RECONNECT_MAX_MS = 30000;

    /**
     * @param {object} options
     * @param {object} options.bus              Topic bus from BossModBus.
     * @param {string} options.url              WebSocket URL.
     * @param {Function} [options.WebSocketImpl] Injectable for tests.
     * @param {(state: string) => void} [options.onStatus]
     * @returns {{ connect: () => void, close: () => void, nextDelay: (attempt: number) => number }}
     */
    function createSocket({ bus, url, WebSocketImpl, onStatus }) {
        const Impl = WebSocketImpl || (typeof WebSocket !== 'undefined' ? WebSocket : null);
        if (!Impl) throw new Error('[socket] no WebSocket implementation available');

        let ws = null;
        let attempt = 0;
        let timer = null;
        let unloading = false;
        let hasConnectedOnce = false;
        let disconnectedAt = null;

        const status = (state) => { if (onStatus) onStatus(state); };

        /** Exponential backoff from 1s, capped at 30s. */
        function nextDelay(n) {
            return Math.min(RECONNECT_MIN_MS * (2 ** Math.max(0, n | 0)), RECONNECT_MAX_MS);
        }

        function connect() {
            status('connecting');
            ws = new Impl(url);

            ws.onopen = () => {
                clearTimeout(timer);
                timer = null;
                attempt = 0;
                status('connected');

                if (hasConnectedOnce) {
                    // We were disconnected and are now back. Anything broadcast
                    // during the gap is gone — consumers must re-fetch.
                    bus.publish('resync', {
                        downtimeMs: disconnectedAt ? Date.now() - disconnectedAt : 0,
                    });
                }
                hasConnectedOnce = true;
                disconnectedAt = null;
            };

            ws.onmessage = (event) => {
                let msg;
                try {
                    msg = JSON.parse(event.data);
                } catch (err) {
                    console.error('[socket] unparseable message', err);
                    return;
                }
                if (!msg || !msg.type) {
                    console.error('[socket] message with no type', msg);
                    return;
                }
                try {
                    bus.publish(msg.type, msg.data);
                } catch (err) {
                    // The server may ship a message type this client build does
                    // not know yet. Log it; do not take the socket down.
                    console.warn(`[socket] no topic for server message "${msg.type}"`, err);
                }
            };

            ws.onclose = () => {
                if (unloading) return;
                if (disconnectedAt === null) disconnectedAt = Date.now();
                status('disconnected');
                scheduleReconnect();
            };

            ws.onerror = (err) => {
                console.error('[socket] error', err);
                if (ws) ws.close();
            };
        }

        function scheduleReconnect() {
            if (unloading) return;
            clearTimeout(timer);
            const delay = nextDelay(attempt);
            attempt += 1;
            timer = setTimeout(connect, delay);
        }

        function close() {
            unloading = true;
            clearTimeout(timer);
            timer = null;
            if (ws) ws.close();
        }

        return { connect, close, nextDelay };
    }

    return { createSocket };
})();
