/**
 * BossMod AI — every request the agent editor makes.
 *
 * One of the modules agent-panel.js became. Eight calls, no DOM, no state:
 * separating them from the form is what lets the form be tested for what it
 * renders and this be read for what it talks to.
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

    return {
        fetchAgent,
        apiCreateAgent,
        apiUpdateAgent,
        apiDeleteAgent,
        fetchPromptHistoryPolicy,
        apiUpdatePromptHistoryPolicy,
        apiClearChatHistory,
        apiResetRuntime,
    };
})();
