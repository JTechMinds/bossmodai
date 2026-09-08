/**
 * BossMod AI — reading the agent form back off the DOM.
 *
 * The inverse of context/agent-form-fields.js: that decides what fields exist,
 * this decides what the server is told they contain. They share
 * context/agent-fields.js so the two halves cannot drift — a model type only
 * one of them knows about is a connection the operator sets and the agent
 * never gets.
 *
 * Two resolutions happen here rather than server-side, both because the form
 * offers a friendlier thing than the agent stores: a personality is a prompt
 * template to copy, and a connection is a model name plus the base URL. A
 * connection with no explicit model identifier THROWS rather than saving an
 * agent that would fail on its first turn with no visible cause.
 */
const BossModAgentSubmit = (() => {

    /**
     * @param {HTMLFormElement} form
     * @param {object[]} connections
     * @returns {Promise<{agentData: object, promptHistoryPolicy: object}>}
     * @throws {Error} When a selected connection carries no model identifier.
     */
    async function buildSubmitData(form, connections) {
        const formData = new FormData(form);

        const deskValue = formData.get('desk');
        let desk_x = null, desk_y = null;
        if (deskValue) {
            [desk_x, desk_y] = deskValue.split(',').map(Number);
        }

        const agentData = {
            name: formData.get('name'),
            role: formData.get('role') || null,
            description: formData.get('description') || null,
            done_fail_bar: formData.get('done_fail_bar') || null,
            color: formData.get('agent-color') || '#3b82f6',
            desk_x,
            desk_y,
        };

        // Resolve personality → copy prompt_template
        const personalityId = formData.get('personality_id');
        if (personalityId) {
            try {
                const res = await apiFetch(`/api/personalities/${personalityId}`);
                if (res.ok) {
                    const personality = await res.json();
                    agentData.prompt_template = personality.prompt_template;
                }
            } catch { /* use null */ }
        }

        // Resolve connection IDs → copy model names; backend copies stored secrets.
        const connMap = {};
        for (const c of connections) connMap[c.id] = c;

        for (const t of BossModAgentFields.MODEL_TYPES) {
            const connId = formData.get(t.key);
            if (connId && connMap[connId]) {
                const conn = connMap[connId];
                const runtimeModel = (conn.model || '').trim();
                if (!runtimeModel) {
                    throw new Error(`Connection "${conn.name}" is missing an explicit model identifier`);
                }
                agentData[t.key] = runtimeModel;
                if (!agentData.connection_id) {
                    agentData.connection_id = conn.id;
                    agentData.api_base_url = conn.api_base_url;
                    agentData.extra_body = conn.extra_body || null;
                }
            } else {
                agentData[t.key] = null;
            }
        }

        const earliestTsRaw = String(formData.get('prompt_history_earliest_ts') || '').trim();
        const promptHistoryPolicy = {
            last_n_histories: Number(formData.get('prompt_history_last_n') || BossModAgentFields.DEFAULT_PROMPT_HISTORY_POLICY.last_n_histories),
            max_allowed_history_tokens: Number(formData.get('prompt_history_max_tokens') || BossModAgentFields.DEFAULT_PROMPT_HISTORY_POLICY.max_allowed_history_tokens),
            earliest_ts_allowed: earliestTsRaw ? new Date(earliestTsRaw).toISOString() : null,
            include_notifications: formData.get('prompt_history_include_notifications') === 'on',
        };

        return { agentData, promptHistoryPolicy };
    }

    return { buildSubmitData };
})();
