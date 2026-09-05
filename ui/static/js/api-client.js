/**
 * BossMod AI — Shared REST client for /api calls.
 *
 * Call sites use apiFetch() (same signature as fetch). The local token is
 * attached here, and again by the api-auth.js window.fetch wrap, so a missed
 * migration still authenticates while this helper is the required call path.
 */
(function installBossModApiClient() {
    const TOKEN_HEADER = window.BOSSMOD_API_TOKEN_HEADER || 'X-BossMod-Token';

    function withAuthHeaders(input, init) {
        const nextInit = init ? { ...init } : {};
        const inherited = nextInit.headers
            || (input && typeof input === 'object' && input.headers)
            || undefined;
        const headers = new Headers(inherited || {});
        const token = window.BOSSMOD_API_TOKEN || '';
        if (token && !headers.has(TOKEN_HEADER) && !headers.has('Authorization')) {
            headers.set(TOKEN_HEADER, token);
        }
        nextInit.headers = headers;
        return nextInit;
    }

    function apiFetch(input, init) {
        return window.fetch(input, withAuthHeaders(input, init));
    }

    window.apiFetch = apiFetch;
    window.BossModApi = {
        fetch: apiFetch,
        tokenHeader: TOKEN_HEADER,
    };
})();
