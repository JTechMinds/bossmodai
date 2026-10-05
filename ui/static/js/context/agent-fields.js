/**
 * BossMod AI — the agent form's vocabulary.
 *
 * What fields the form has and what values they accept, with no markup and no
 * DOM. Three modules need it and would otherwise each keep a copy: the field
 * groups render it, the Advanced disclosure's choices (context/
 * agent-form-choices.js) build the desk half of it, and context/agent-submit.js
 * reads the same THINKING_MODES and history defaults back off the submitted
 * form. A second list of activation types is a choice that saves into a field
 * nothing reads.
 */
const BossModAgentFields = (() => {

    /**
     * The eight seeds, named. Keys are core/agent-status.js's palette values;
     * a key that drifts out of step with it falls back to the raw hex below,
     * which is the visible symptom of the two lists disagreeing.
     */
    const AGENT_COLOR_NAMES = {
        '#1d4ed8': 'Blue',
        '#92400e': 'Amber',
        '#065f46': 'Emerald',
        '#be123c': 'Rose',
        '#6d28d9': 'Violet',
        '#155e75': 'Cyan',
        '#9a3412': 'Orange',
        '#a21caf': 'Pink',
    };

    /**
     * The palette, named. The values are core/agent-status.js's; only the
     * labels are the form's, so the roster and the form cannot disagree about
     * which colours exist.
     */
    const AGENT_COLORS = (BossModAgentStatus.AGENT_COLOR_PALETTE || []).map(value => ({
        name: AGENT_COLOR_NAMES[value] || value,
        value,
    }));

    /**
     * The two activations the runtime routes (core/agent_loop/turn_context.py
     * `_determine_mode`), each with its own thinking level on the agent's one
     * connection. A third row belongs here only when the runtime routes it.
     */
    const THINKING_MODES = [
        { key: 'thinking_social', label: 'Social', hint: 'Idle chats with nearby teammates' },
        { key: 'thinking_work', label: 'Work', hint: 'Everything else: your DMs, threads and tasks' },
    ];

    /**
     * The thinking choices, in order. `default` sends nothing, so the
     * provider's own default applies; the rest are offered only when the
     * agent's connection defines them (core/models/thinking.py).
     */
    const THINKING_CHOICES = [
        { value: 'default', label: 'Server default' },
        { value: 'off', label: 'Off' },
        { value: 'low', label: 'Low' },
        { value: 'medium', label: 'Medium' },
        { value: 'high', label: 'High' },
        { value: 'xhigh', label: 'Extra high' },
    ];

    const DEFAULT_PROMPT_HISTORY_POLICY = {
        last_n_histories: 30,
        max_allowed_history_tokens: 2000,
        earliest_ts_allowed: null,
        include_notifications: true,
    };

    /**
     * Which desk is offered, and whether any is free.
     *
     * Data only: context/agent-form-choices.js turns `desks` into the desk
     * dropdown's options, and context/agent-form-advanced.js reads
     * `noFreeDesk` for the warning under it.
     *
     * The desks are the map endpoint's (core/world/tilemap.py DEFAULT_DESKS,
     * loaded by context/agent-form.js), never a copy kept here, so growing
     * the map cannot desync the form. Occupancy is per floor, as the server
     * enforces it: each floor draws its own office, so a teammate on another
     * floor does not take a desk on this one.
     *
     * @param {object|null} agent  The agent being edited, the snapshot being
     *   recreated, or null for a blank form.
     * @param {object[]} roster  Every agent; only rows on `floorId` count.
     * @param {Array<{label: string, chair_xy: number[]}>} desks  The map's.
     * @param {string|null} floorId  The floor the agent works on: its own
     *   when edited, the hire floor otherwise.
     * @returns {{selectedDesk: {value: string, label: string}|null,
     *   noFreeDesk: boolean,
     *   desks: Array<{value: string, label: string, taken: boolean}>}}
     *   The agent's own desk wins unless another agent on `floorId` holds
     *   it; otherwise the first unoccupied one. A null `floorId` (an agent on
     *   vacation) marks nothing taken. When neither exists the caller says
     *   so rather than silently seating them on top of a teammate. Each desk's `value` is `"x,y"` (its chair), and
     *   `taken` says a teammate on this floor already sits there.
     * @throws {Error} When `desks` is not a list: the dropdown would offer
     *   nothing and the form would claim no desk is free.
     */
    function deskChoice(agent, roster, desks, floorId) {
        if (!Array.isArray(desks)) throw new Error('[agent-fields] deskChoice needs the map\'s desks list');
        // No floor (an agent on vacation): the server stores its desk as-is
        // and reconciles it on return, so nothing is shown as taken.
        const occupiedChairs = new Set(
            floorId == null ? [] : (roster || [])
                .filter((item) => item.floor_id === floorId && item.id !== agent?.id
                    && item.desk_x != null && item.desk_y != null)
                .map((item) => `${item.desk_x},${item.desk_y}`)
        );
        const options = desks.map((desk) => {
            const value = `${desk.chair_xy[0]},${desk.chair_xy[1]}`;
            return { value, label: desk.label, taken: occupiedChairs.has(value) };
        });
        const ownValue = agent?.desk_x != null && agent?.desk_y != null
            ? `${agent.desk_x},${agent.desk_y}`
            : null;
        // Its own desk only while nobody else on this floor holds it: a
        // recreated snapshot's old chair may belong to someone now, and
        // preselecting it would only earn a 409 on save.
        const assignedDesk = ownValue
            ? options.find((d) => d.value === ownValue && !d.taken) || null
            : null;
        const freeDesk = options.find((d) => !d.taken) || null;
        const selectedDesk = assignedDesk || freeDesk;
        return { selectedDesk, noFreeDesk: !assignedDesk && !freeDesk, desks: options };
    }

    return {
        AGENT_COLORS,
        THINKING_MODES,
        THINKING_CHOICES,
        DEFAULT_PROMPT_HISTORY_POLICY,
        deskChoice,
    };
})();
