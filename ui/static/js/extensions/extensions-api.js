/**
 * BossMod AI — the calls the Extensions dialog, its live view and the
 * per-agent desk surfaces (settings and view) make.
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

    const enc = encodeURIComponent;

    function agentBase(id, agentId) {
        return `/api/extensions/${enc(id)}/agents/${enc(agentId)}`;
    }

    /**
     * The desk's one read: enabled extensions with per-agent settings.
     *
     * @param {string} agentId
     * @returns {Promise<Array<{id: string, name: string, config_label: string,
     *   view_label: string|null, configured: boolean, summary: string|null,
     *   wakes: boolean, wake: {checked_at: string, ok: boolean, error: string|null,
     *     last_new_at: string|null, last_new_count: number|null}|null}>>}
     *   `wakes` says whether the extension wakes agents; `wake` is its last
     *   check, null when it does not wake agents or before the first check.
     * @throws {Error} On any non-2xx.
     */
    async function agentExtensions(agentId) {
        const res = await apiFetch(`/api/agents/${enc(agentId)}/extensions`, { cache: 'no-store' });
        if (!res.ok) throw await failure(res, 'Couldn’t load this agent’s extensions.');
        return res.json();
    }

    /**
     * One agent's settings for one extension; secret fields carry only `set`.
     *
     * @param {string} id
     * @param {string} agentId
     * @returns {Promise<object>} `{label, help, configured, updated_at, fields}`.
     * @throws {Error} `code` is `EXTENSION_DISABLED`, `INVALID_EXTENSION` or
     *   `NO_AGENT_CONFIG` for the server's refusals.
     */
    async function getAgentConfig(id, agentId) {
        const res = await apiFetch(`${agentBase(id, agentId)}/config`, { cache: 'no-store' });
        if (!res.ok) throw await failure(res, 'Couldn’t load these settings.');
        return res.json();
    }

    /**
     * Verify and store one agent's settings. A blank secret keeps the stored one.
     *
     * @param {string} id
     * @param {string} agentId
     * @param {Object<string, string>} values  Every field, secrets as typed ("" = keep).
     * @returns {Promise<object>} The GET shape plus `verified`.
     * @throws {Error} `code` is `CONFIG_INVALID` or `CONFIG_VERIFY_FAILED`
     *   (the message is the server's, e.g. an AADSTS error).
     */
    async function saveAgentConfig(id, agentId, values) {
        const res = await apiFetch(`${agentBase(id, agentId)}/config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ values }),
        });
        if (!res.ok) throw await failure(res, 'Couldn’t save these settings.');
        return res.json();
    }

    /**
     * Remove one agent's settings for one extension.
     *
     * @param {string} id
     * @param {string} agentId
     * @returns {Promise<void>}
     * @throws {Error} On any non-2xx (404 when none were stored).
     */
    async function deleteAgentConfig(id, agentId) {
        const res = await apiFetch(`${agentBase(id, agentId)}/config`, { method: 'DELETE' });
        if (!res.ok) throw await failure(res, 'Couldn’t remove these settings.');
    }

    /**
     * One page of one of an agent's record lists (e.g. its inbox).
     *
     * @param {string} id
     * @param {string} agentId
     * @param {string} view  One of the extension's `agent_view.views` keys.
     * @param {number} skip
     * @param {number} top
     * @returns {Promise<{columns: object[], rows: object[], has_more: boolean, caption: string}>}
     * @throws {Error} `code` is `NOT_CONFIGURED`, or the extension's own
     *   (e.g. `MAILBOX_ACCESS_DENIED`, with a plain sentence) on a 502.
     */
    async function agentView(id, agentId, view, skip, top) {
        const query = `view=${enc(view)}&skip=${enc(String(skip))}&top=${enc(String(top))}`;
        const res = await apiFetch(`${agentBase(id, agentId)}/view?${query}`, { cache: 'no-store' });
        if (!res.ok) throw await failure(res, 'Couldn’t load this list.');
        return res.json();
    }

    /**
     * One record in full.
     *
     * @param {string} id
     * @param {string} agentId
     * @param {string} view  The list the item was opened from.
     * @param {string} itemId
     * @returns {Promise<{title: string, facts: Array<[string, string]>, body_text: string}>}
     * @throws {Error} As agentView.
     */
    async function agentViewItem(id, agentId, view, itemId) {
        const res = await apiFetch(`${agentBase(id, agentId)}/view/${enc(itemId)}?view=${enc(view)}`, { cache: 'no-store' });
        if (!res.ok) throw await failure(res, 'Couldn’t open this item.');
        return res.json();
    }

    return {
        listExtensions, setEnabled, startSetup, liveView,
        agentExtensions, getAgentConfig, saveAgentConfig, deleteAgentConfig, agentView, agentViewItem,
    };
})();
