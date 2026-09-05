/**
 * BossMod AI — Attach the local API token to REST and WebSocket calls.
 *
 * The token is injected into the index page (same-origin desktop UI) and
 * sent as X-BossMod-Token on fetch, or ?token= on WebSocket.
 */
(function attachBossModApiAuth() {
    const TOKEN_HEADER = 'X-BossMod-Token';
    const meta = document.querySelector('meta[name="bossmod-api-token"]');
    const token = (meta && meta.getAttribute('content')) || '';
    window.BOSSMOD_API_TOKEN = token;
    window.BOSSMOD_API_TOKEN_HEADER = TOKEN_HEADER;

    if (!token) {
        console.warn('[BossMod] Local API token is missing from the page; /api calls will be rejected.');
    }

    const originalFetch = window.fetch.bind(window);
    window.fetch = function bossmodFetch(input, init) {
        const nextInit = init ? { ...init } : {};
        const headers = new Headers(nextInit.headers || {});
        if (token && !headers.has(TOKEN_HEADER) && !headers.has('Authorization')) {
            headers.set(TOKEN_HEADER, token);
        }
        nextInit.headers = headers;
        return originalFetch(input, nextInit);
    };

    const OriginalWebSocket = window.WebSocket;
    window.WebSocket = function BossModWebSocket(url, protocols) {
        let nextUrl = url;
        if (token && typeof url === 'string') {
            try {
                const parsed = new URL(url, window.location.href);
                if (parsed.pathname.startsWith('/api/') && !parsed.searchParams.has('token')) {
                    parsed.searchParams.set('token', token);
                    nextUrl = parsed.toString();
                }
            } catch {
                /* keep original URL */
            }
        }
        if (protocols === undefined) {
            return new OriginalWebSocket(nextUrl);
        }
        return new OriginalWebSocket(nextUrl, protocols);
    };
    window.WebSocket.prototype = OriginalWebSocket.prototype;
    window.WebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
    window.WebSocket.OPEN = OriginalWebSocket.OPEN;
    window.WebSocket.CLOSING = OriginalWebSocket.CLOSING;
    window.WebSocket.CLOSED = OriginalWebSocket.CLOSED;
})();
