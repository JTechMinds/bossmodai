/**
 * BossMod AI — the calls the Extensions dialog and its live view make.
 *
 * The routes answer a refusal as `HTTPException(409, {error, message})`, so
 * the useful part is nested under `detail`; FastAPI's own validation errors
 * are a string or a list there instead. One reader for all of them, so the
 * dialog can branch on `err.code` (`SETUP_REQUIRED`, `SETUP_RUNNING`, …) and
 * always has a sentence to show.
 *
 * No DOM and no state.
 */
const BossModExtensionsApi = (() => {

    /**
     * Turn a failed response into an Error that keeps the server's code.
     *
     * @param {Response} res
     * @param {string} fallback  Used only when the body carries no message.
     * @returns {Promise<Error>} With `code` and `status` attached.
     */
    async function failure(res, fallback) {
        const data = await res.json().catch(() => ({}));
        const detail = data && data.detail;
        const message = (detail && typeof detail.message === 'string' && detail.message)
            || (typeof detail === 'string' ? detail : '')
            || fallback;
        const error = new Error(message);
        error.code = (detail && detail.error) || '';
        error.status = res.status;
        return error;
    }

    /**
     * Every extension folder the app found at start, valid or not.
     *
     * @returns {Promise<object[]>} Items as `GET /api/extensions` returns them.
     * @throws {Error} On any non-2xx, so an empty list is never shown over a
     *   failed read.
     */
    async function listExtensions() {
        const res = await apiFetch('/api/extensions', { cache: 'no-store' });
        if (!res.ok) throw await failure(res, 'Couldn’t load extensions.');
        return res.json();
    }

    /**
     * Turn one extension on or off.
     *
     * @param {string} id
     * @param {boolean} enabled
     * @returns {Promise<object>} The updated item.
     * @throws {Error} `code` is `SETUP_REQUIRED` or `INVALID_EXTENSION` for the
     *   server's refusals.
     */
    async function setEnabled(id, enabled) {
        const res = await apiFetch(`/api/extensions/${encodeURIComponent(id)}/enabled`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled }),
        });
        if (!res.ok) throw await failure(res, enabled ? 'Couldn’t turn it on.' : 'Couldn’t turn it off.');
        return res.json();
    }

    /**
     * Start the extension's one-click setup (a download) in the background.
     *
     * @param {string} id
     * @param {boolean} enableOnSuccess  Turn it on once setup is ready.
     * @returns {Promise<object>} The item, its setup now `installing`.
     * @throws {Error} `code` is `SETUP_RUNNING` when one is already going.
     */
    async function startSetup(id, enableOnSuccess) {
        const res = await apiFetch(`/api/extensions/${encodeURIComponent(id)}/setup`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enable_on_success: enableOnSuccess }),
        });
        if (!res.ok) throw await failure(res, 'Couldn’t start setup.');
        return res.json();
    }

    /**
     * Each agent's latest output for an extension's live view, newest first.
     *
     * @param {string} id
     * @returns {Promise<{items: object[]}>}
     * @throws {Error} `code` is `EXTENSION_DISABLED`, `LIVE_VIEW_UNSUPPORTED`
     *   or `INVALID_EXTENSION` for the server's refusals.
     */
    async function liveView(id) {
        const res = await apiFetch(`/api/extensions/${encodeURIComponent(id)}/live`, { cache: 'no-store' });
        if (!res.ok) throw await failure(res, 'Couldn’t load the live view.');
        return res.json();
    }

    return { listExtensions, setEnabled, startSetup, liveView };
})();
