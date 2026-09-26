/**
 * BossMod AI — every request the agent editor makes.
 *
 * One of the modules agent-panel.js became. Requests only — no DOM, no state:
 * separating them from the form is what lets the form be tested for what it
 * renders and this be read for what it talks to. That includes the one read
 * Add agent's Recent makes, `listSnapshots`: a snapshot is an agent's setup,
 * so its client is the agent client's.
 *
 * Each mutating call throws the server's own message on a non-2xx rather than
 * returning a falsy value — the editor surfaces it in the save feedback, and a
 * swallowed failure here would tell the operator their agent had been saved
 * when it had not. `fetchAgent` is the one exception and is documented as such.
 */
const BossModAgentApi = (() => {

    /**
     * Read one agent.
     *
     * @param {string} id
     * @returns {Promise<object|null>} null when the agent is gone. The editor
     *   merges over the roster row it already has, so a missing detail record
     *   degrades to "edit what we know" rather than to an empty form.
     */
    async function fetchAgent(id) {
        const res = await apiFetch(`/api/agents/${id}`);
        if (!res.ok) return null;
        return res.json();
    }

    /**
     * @param {object} data
     * @returns {Promise<object>} The created agent.
     * @throws {Error} With the server's message on any non-2xx.
     */
    async function apiCreateAgent(data) {
        const res = await apiFetch('/api/agents', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    /**
     * @param {string} id
     * @param {object} data
     * @returns {Promise<object>} The updated agent.
     * @throws {Error} With the server's message on any non-2xx.
     */
    async function apiUpdateAgent(id, data) {
        const res = await apiFetch(`/api/agents/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    /**
     * @param {string} id
     * @returns {Promise<void>}
     * @throws {Error} With the server's message on any non-2xx.
     */
    async function apiDeleteAgent(id) {
        const res = await apiFetch(`/api/agents/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(await res.text());
    }

    // What a delete destroys, said once for every place that offers one — the
    // desk's Remove, the Edit role form's Delete, a floor delete's "Delete them",
    // and Settings' delete-all. Kept
    // beside apiDeleteAgent because it is a claim about what that call does: a
    // change to the server's delete must change this sentence with it, and four
    // copies is how one of them came to promise diagnostics were preserved.
    // `{name}` is filled by agentDeleteWarning, which is how callers read it.
    const AGENT_DELETE_WARNING = 'Deleting {name} permanently deletes their files '
        + '(/me workspace), chat and work history, standing prefs, and diagnostics, '
        + 'and cancels their open tasks. Messages they posted in shared threads stay. '
        + 'This cannot be undone — back up anything you need from their Desk first.';
    const ALL_AGENTS_DELETE_WARNING = 'Deleting all agents permanently deletes every '
        + 'agent’s files (/me workspace), chat and work history, standing prefs, and '
        + 'diagnostics, and cancels their open tasks. Messages they posted in shared '
        + 'threads stay. This cannot be undone — back up anything you need from their '
        + 'Desks first. Settings and /projects files stay.';
    // Subject-less: the floor dialog already says whose agents these are, and a
    // floor delete removes only that floor's residents, not "all agents".
    const FLOOR_AGENTS_DELETE_WARNING = 'Permanently deletes their files (/me workspace), '
        + 'chat and work history, standing prefs, and diagnostics, and cancels their open '
        + 'tasks. Messages they posted in shared threads stay. This cannot be undone — '
        + 'back up anything you need from their Desks first.';

    /**
     * The warning shown before one agent is deleted, naming them.
     *
     * @param {string} name  The agent's name as the operator knows it.
     * @returns {string}
     * @throws {Error} When `name` is not a non-empty string: a warning about
     *   "Deleting undefined" would ask the operator to confirm without saying
     *   whose files go, so the caller must have the name before it asks.
     */
    function agentDeleteWarning(name) {
        if (typeof name !== 'string' || !name.trim()) {
            throw new Error('[agent-api] agentDeleteWarning needs the agent’s name');
        }
        // A replacer function, so a `$&` or `$'` in a name is text, not a pattern.
        return AGENT_DELETE_WARNING.replace('{name}', () => name);
    }

    /**
     * The warning shown before every agent is deleted at once.
     *
     * @returns {string}
     */
    function allAgentsDeleteWarning() {
        return ALL_AGENTS_DELETE_WARNING;
    }

    /**
     * The warning shown as the "Delete them" choice when a floor with agents
     * is deleted. It names no subject, because the dialog above it already
     * says which floor's agents these are.
     *
     * @returns {string}
     */
    function floorAgentsDeleteWarning() {
        return FLOOR_AGENTS_DELETE_WARNING;
    }

    /**
     * @param {string} id
     * @returns {Promise<object>} The agent's prompt-history policy.
     * @throws {Error} With the server's message on any non-2xx.
     */
    async function fetchPromptHistoryPolicy(id) {
        const res = await apiFetch(`/api/agents/${id}/prompt-history-policy`, { cache: 'no-store' });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    /**
     * @param {string} id
     * @param {object} data
     * @returns {Promise<object>}
     * @throws {Error} With the server's message on any non-2xx.
     */
    async function apiUpdatePromptHistoryPolicy(id, data) {
        const res = await apiFetch(`/api/agents/${id}/prompt-history-policy`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    /**
     * @param {string} id
     * @returns {Promise<{deleted_messages: number}>}
     * @throws {Error} With the server's message on any non-2xx.
     */
    async function apiClearChatHistory(id) {
        const res = await apiFetch(`/api/agents/${id}/chat-history`, { method: 'DELETE' });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    /**
     * @param {string} id
     * @returns {Promise<{deleted_triggers: number}>}
     * @throws {Error} With the server's message on any non-2xx.
     */
    async function apiResetRuntime(id) {
        const res = await apiFetch(`/api/agents/${id}/reset-runtime`, { method: 'POST' });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    /**
     * Every agent snapshot — one per agent, deleted agents included — for
     * Add agent's Recent scope.
     *
     * @returns {Promise<object[]>} `AgentSnapshot` rows, newest `captured_at`
     *   first. None carries an API key, base URL or extra body.
     * @throws {Error} With the server's message on any non-2xx, so the picker
     *   can tell a failed read from an empty list.
     */
    async function listSnapshots() {
        const res = await apiFetch('/api/agent-snapshots', { cache: 'no-store' });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    /**
     * Read a failed pack-API body without assuming FastAPI's detail shape.
     *
     * @param {Response} res
     * @param {string} fallback
     * @returns {Promise<{error: Error, data: object}>}
     */
    async function packFailure(res, fallback) {
        const data = await res.json().catch(() => ({}));
        const detail = data && data.detail;
        const message = (detail && detail.message) || (typeof detail === 'string' ? detail : '') || fallback;
        const error = new Error(message);
        error.code = (detail && detail.code) || data.code || '';
        error.status = res.status;
        return { error, data };
    }

    /**
     * List catalog packs at the seeded pin. Does not hire.
     *
     * @param {string} [ref]
     * @returns {Promise<object>}
     */
    async function fetchCatalog(ref) {
        const qs = ref ? `?ref=${encodeURIComponent(ref)}` : '';
        const res = await apiFetch(`/api/agent-packs${qs}`);
        if (!res.ok) {
            const { error } = await packFailure(res, 'Couldn’t load packs.');
            throw error;
        }
        return res.json();
    }

    return {
        fetchAgent,
        apiCreateAgent,
        apiUpdateAgent,
        apiDeleteAgent,
        AGENT_DELETE_WARNING,
        agentDeleteWarning,
        allAgentsDeleteWarning,
        FLOOR_AGENTS_DELETE_WARNING,
        floorAgentsDeleteWarning,
        fetchPromptHistoryPolicy,
        apiUpdatePromptHistoryPolicy,
        apiClearChatHistory,
        apiResetRuntime,
        listSnapshots,
        fetchCatalog,
    };
})();
