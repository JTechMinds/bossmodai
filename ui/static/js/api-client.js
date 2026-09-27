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
     * The message an attachment endpoint refused with. Those endpoints send
     * `detail: {error, code}`; anything else falls back to the shared reader.
     * @param {Response} res
     * @returns {Promise<string>}
     */
    async function attachmentErrorMessage(res) {
        const payload = await res.json().catch(() => ({}));
        const detail = payload && payload.detail;
        if (detail && typeof detail === 'object' && typeof detail.error === 'string') return detail.error;
        return formatApiError(payload, res.status);
    }

    /**
     * Upload one file attachment. Returns the server metadata object.
     * @param {File} file
     * @param {{type: 'direct'|'thread', id: string}} context  The open
     *   conversation; required, because the server files the upload under it.
     * @returns {Promise<object>} `{id, file_name, file_size, mime_type, preview_tier}`.
     * @throws {Error} When no context is given, or the server refuses the file
     *   (`err.code` and `err.fileName` are set).
     */
    async function uploadAttachment(file, context) {
        if (!context || !context.type || !context.id) {
            throw new Error('[api] uploadAttachment needs the conversation context');
        }
        const form = new FormData();
        form.append('file', file);
        form.append('message_context', JSON.stringify(context));
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

    /**
     * Discard a pending upload the operator removed before sending.
     * @param {string} id
     * @returns {Promise<void>}
     * @throws {Error} With the server's reason (e.g. the file was already sent).
     */
    async function deleteAttachment(id) {
        if (!id) throw new Error('[api] deleteAttachment needs an attachment id');
        const res = await apiFetch(`/api/attachments/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(await attachmentErrorMessage(res));
    }

    /**
     * The operator-set upload limits.
     * @returns {Promise<{max_size_mb: number, max_per_message: number}>}
     * @throws {Error} On a failed request or a response without both limits.
     */
    async function getAttachmentLimits() {
        const res = await apiFetch('/api/attachments/limits', { cache: 'no-store' });
        if (!res.ok) throw new Error(await attachmentErrorMessage(res));
        const limits = await res.json();
        if (!limits || !Number.isInteger(limits.max_size_mb) || !Number.isInteger(limits.max_per_message)) {
            throw new Error('[api] attachment limits response is malformed');
        }
        return limits;
    }

    window.apiFetch = apiFetch;
    window.apiFetchOk = apiFetchOk;
    window.apiFetchBlobUrl = apiFetchBlobUrl;
    window.BossModApi = {
        fetch: apiFetch,
        fetchOk: apiFetchOk,
        fetchBlobUrl: apiFetchBlobUrl,
        uploadAttachment: uploadAttachment,
        deleteAttachment: deleteAttachment,
        getAttachmentLimits: getAttachmentLimits,
        formatError: formatApiError,
        tokenHeader: TOKEN_HEADER,
    };
})();
