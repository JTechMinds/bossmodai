/**
 * BossMod AI — the four calls the local agent template library makes.
 *
 * A template is a locally-installed, pinned snapshot of a pack, or the
 * operator's own — a role contract saved from an agent form. These are the
 * only requests against the library, and they are deliberately shaped like
 * context/agent-api.js's pack calls: the marketplace branches on
 * `err.code === 'trust_required'` to raise its inline confirm strip, and Save
 * as template on `err.code === 'local_title_taken'` to ask Replace, and both
 * only work while every reader takes the server's `{code, message}` detail the
 * same way. A second, differently-shaped reader is how those branches would rot.
 *
 * No DOM and no state, so the marketplace can be read for what it renders and
 * this for what it talks to.
 */
const BossModAgentTemplatesApi = (() => {

    /**
     * Turn a failed template-API response into an Error that keeps its code.
     *
     * The routes raise `HTTPException(status, {code, message})`, so the useful
     * part is nested under `detail`. A plain-string detail (FastAPI's own
     * validation shape) and a body with neither are both handled, because a
     * message the operator cannot read is the same as no message at all.
     *
     * @param {Response} res
     * @param {string} fallback  Used only when the body carries no message.
     * @returns {Promise<Error>} With `code` and `status` attached.
     */
    async function failure(res, fallback) {
        const data = await res.json().catch(() => ({}));
        const detail = data && data.detail;
        const message = (detail && detail.message)
            || (typeof detail === 'string' ? detail : '')
            || fallback;
        const error = new Error(message);
        error.code = (detail && detail.code) || (data && data.code) || '';
        error.status = res.status;
        return error;
    }

    /**
     * List every installed template, ordered by category then title.
     *
     * @returns {Promise<object[]>} Rows of `AgentTemplate`. An empty library is
     *   an empty array; a failed read throws, so the caller can tell the two
     *   apart and never render "nothing installed" over a broken request.
     * @throws {Error} With the server's message on any non-2xx.
     */
    async function listTemplates() {
        const res = await apiFetch('/api/agent-templates', { cache: 'no-store' });
        if (!res.ok) throw await failure(res, 'Couldn’t read your template library.');
        return res.json();
    }

    /**
     * Install (or re-install) one pack into the library.
     *
     * @param {object} body  `{id, ref}` for a catalog pack — `ref` is the
     *   commit the browse list was rendered from, so what installs is what was
     *   displayed — or `{url}` for a GitHub file URL, plus `confirm: true` to
     *   answer the trust gate on a repo outside the allowlist.
     * @returns {Promise<object>} The stored `AgentTemplate` row.
     * @throws {Error} With the server's message and `code` intact. `code` is
     *   `trust_required` for the confirmable case; everything else is terminal.
     */
    async function installTemplate(body) {
        // Stripped rather than trusted: installing must never patch a live
        // hire, and the route only rejects `agent_id` because it declares it.
        const payload = { ...body };
        delete payload.agent_id;
        const res = await apiFetch('/api/agent-templates', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw await failure(res, 'Install failed.');
        return res.json();
    }

    /**
     * Save the operator's own role contract as a local template.
     *
     * @param {{title: string, category: string, specialty: string,
     *   description: string, what_done_looks_like: string,
     *   personality_hint: string|null, communication: object|null,
     *   replace?: boolean}} body  What POST /api/agent-templates/local takes.
     * @returns {Promise<object>} The stored `AgentTemplate` row
     *   (`source: 'local'`).
     * @throws {Error} With the server's message and `code` intact, exactly as
     *   `installTemplate` throws. `code` is `local_title_taken` when a local
     *   template already has that title and `replace` was not set — the one
     *   the caller answers by asking; everything else is terminal.
     */
    async function saveLocalTemplate(body) {
        const res = await apiFetch('/api/agent-templates/local', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw await failure(res, 'Save failed.');
        return res.json();
    }

    /**
     * Remove one template from the library — an uninstall, or a local
     * template's delete. Agents already created from it are untouched — a
     * template is a snapshot, not a live link.
     *
     * @param {string} id
     * @returns {Promise<void>} Resolves on 204.
     * @throws {Error} With the server's message on any non-2xx, including the
     *   404 that says the row was already gone.
     */
    async function uninstallTemplate(id) {
        const res = await apiFetch(`/api/agent-templates/${encodeURIComponent(id)}`, {
            method: 'DELETE',
        });
        if (!res.ok) throw await failure(res, 'Uninstall failed.');
    }

    return { listTemplates, installTemplate, saveLocalTemplate, uninstallTemplate };
})();
