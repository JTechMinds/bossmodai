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
 * agent that would fail on its first turn with no visible cause. The one
 * personality that is not a personality — a recreated agent's prompt kept
 * because nothing configured matches it — sends the kept text itself.
 *
 * The colour is validated the same way, and for the same reason: an agent whose
 * seed is too light is drawn on the office floor as a sprite nobody can pick
 * out. core/avatar.js owns the bound; this is the gate that enforces it.
 */
const BossModAgentSubmit = (() => {

    /** Used when the form offered no colour. A palette-era value, kept legible. */
    const FALLBACK_COLOR = '#3b82f6';

    const PALE_COLOR_MESSAGE =
        'That colour is too light to see on the office floor. Pick a darker one.';

    /**
     * @param {HTMLFormElement} form
     * @param {object[]} connections
     * @returns {Promise<{agentData: object, promptHistoryPolicy: object}>}
     * @throws {Error} When a selected connection carries no model identifier,
     *   when the chosen colour is too light to render as a visible agent, or
     *   when the kept personality is chosen and its text is not on the form.
     *   Refused rather than darkened: silently saving a different colour than
     *   the operator picked is the behaviour this codebase forbids.
     */
    async function buildSubmitData(form, connections) {
        const formData = new FormData(form);

        const deskValue = formData.get('desk');
        let desk_x = null, desk_y = null;
        if (deskValue) {
            [desk_x, desk_y] = deskValue.split(',').map(Number);
        }

        const color = formData.get('agent-color') || FALLBACK_COLOR;
        if (!BossModAvatar.isSeedLegible(color)) {
            throw new Error(PALE_COLOR_MESSAGE);
        }

        const agentData = {
            name: formData.get('name'),
            role: formData.get('role') || null,
            description: formData.get('description') || null,
            done_fail_bar: formData.get('done_fail_bar') || null,
            communication: BossModCommunication.resolve({
                tone: formData.get('communication_tone'),
                density: formData.get('communication_density'),
                jargon: formData.get('communication_jargon'),
                audience: formData.get('communication_audience'),
            }, formData.get('role')),
            color,
            desk_x,
            desk_y,
        };

        // Resolve personality → copy prompt_template
        const personalityId = formData.get('personality_id');
        if (personalityId === BossModAgentFields.KEPT_PERSONALITY) {
            // A recreated agent's own prompt, which no personality holds now
            // (context/agent-form-advanced.js): the text rides in the form.
            const kept = formData.get('prompt_template_kept');
            if (kept === null) {
                throw new Error('The kept prompt template is missing from the form.');
            }
            agentData.prompt_template = kept;
        } else if (personalityId) {
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
