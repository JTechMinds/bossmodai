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

    function formatApiError(payload, status) {
        const detail = payload && payload.detail;
        if (typeof detail === 'string' && detail.trim()) return detail;
        if (Array.isArray(detail) && detail.length) {
            const parts = detail.map((item) => {
                if (typeof item === 'string') return item;
                if (item && typeof item.msg === 'string') return item.msg;
                return '';
            }).filter(Boolean);
            if (parts.length) return parts.join('; ');
        }
        if (payload && typeof payload.error === 'string' && payload.error.trim()) {
            return payload.error;
        }
        return `Request failed (${status})`;
    }

    async function apiErrorMessage(res) {
        const payload = await res.json().catch(() => ({}));
        return formatApiError(payload, res.status);
    }

    async function apiFetchOk(input, init) {
        const res = await apiFetch(input, init);
        if (!res.ok) {
            throw new Error(await apiErrorMessage(res));
        }
        return res;
    }

    async function apiFetchBlobUrl(input, init) {
        const res = await apiFetch(input, init);
        if (!res.ok) {
            throw new Error(await res.text() || `Request failed (${res.status})`);
        }
        const blob = await res.blob();
        return URL.createObjectURL(blob);
    }

    /**
     * Upload one file attachment. Returns the server metadata object.
     * @param {File} file
     * @param {{type: string, id: string}} context
     * @returns {Promise<object>}
     */
    async function uploadAttachment(file, context) {
        const form = new FormData();
        form.append('file', file);
        form.append('message_context', JSON.stringify(context || { type: 'unscoped', id: '' }));
        form.append('original_name', file.name);
        const res = await apiFetch('/api/attachments/upload', { method: 'POST', body: form });
        if (!res.ok) {
            const payload = await res.json().catch(() => ({}));
            const detail = (payload && payload.detail) || payload || {};
            const message = (typeof detail === 'object' && detail.error) || detail.error || formatApiError(payload, res.status);
            const err = new Error(message);
            err.code = (typeof detail === 'object' && detail.code) || detail.code || null;
            err.fileName = file.name;
            throw err;
        }
        return res.json();
    }

    window.apiFetch = apiFetch;
    window.apiFetchOk = apiFetchOk;
    window.apiFetchBlobUrl = apiFetchBlobUrl;
    window.BossModApi = {
        fetch: apiFetch,
        fetchOk: apiFetchOk,
        fetchBlobUrl: apiFetchBlobUrl,
        uploadAttachment: uploadAttachment,
        formatError: formatApiError,
        tokenHeader: TOKEN_HEADER,
    };
})();
