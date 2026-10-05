/**
 * BossMod AI — reading the agent form back off the DOM.
 *
 * The inverse of context/agent-form-fields.js: that decides what fields exist,
 * this decides what the server is told they contain. They share
 * context/agent-fields.js so the two halves cannot drift — an activation only
 * one of them knows about is a thinking level the operator sets and the agent
 * never gets.
 *
 * The AI connection is sent as its id, with the two thinking levels: the
 * server checks that the connection exists, has a model and offers the
 * levels, and the runtime reads the connection live.
 *
 * `template_id` is the installed template the hire form was filled from
 * (context/agent-form-template.js adds it as a hidden input). The server links
 * a hire from a pack template to its pack so the agent can be updated later;
 * the edit form never carries the input, so an edit sends null.
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
     * @returns {Promise<{agentData: object, promptHistoryPolicy: object}>}
     *   `agentData.connection_id` is null while no connection is chosen.
     * @throws {Error} When the chosen colour is too light to render as a
     *   visible agent. Refused rather than darkened: silently saving a
     *   different colour than the operator picked is the behaviour this
     *   codebase forbids.
     */
    async function buildSubmitData(form) {
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
        agentData.template_id = formData.get('template_id') || null;

        // The connection's id and the thinking levels, as the section's hidden
        // inputs hold them (context/agent-form-connections.js). The server
        // refuses a connection that is gone or has no model, and a level the
        // connection does not offer.
        agentData.connection_id = formData.get('connection_id') || null;
        for (const mode of BossModAgentFields.THINKING_MODES) {
            const choice = formData.get(mode.key);
            if (choice) agentData[mode.key] = choice;
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
