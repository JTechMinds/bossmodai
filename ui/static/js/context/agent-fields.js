/**
 * BossMod AI — the agent form's vocabulary.
 *
 * What fields the form has and what values they accept, with no markup and no
 * DOM. Three modules need it and would otherwise each keep a copy: the field
 * groups render it, the Advanced disclosure renders the desk half of it, and
 * context/agent-submit.js reads the same MODEL_TYPES and history defaults back
 * off the submitted form. A second list of activation types is a connection
 * that saves into a field nothing reads.
 */
const BossModAgentFields = (() => {

    const AGENT_COLOR_NAMES = {
        '#3b82f6': 'Blue',
        '#f59e0b': 'Amber',
        '#10b981': 'Emerald',
        '#f43f5e': 'Rose',
        '#8b5cf6': 'Purple',
        '#06b6d4': 'Cyan',
        '#f97316': 'Orange',
        '#ec4899': 'Pink',
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

    /** Desk assignment options, from the tilemap. */
    const DESK_OPTIONS = [
        { id: 'desk_1', x: 3,  y: 4,  label: 'Desk 1 — Main NW' },
        { id: 'desk_2', x: 7,  y: 4,  label: 'Desk 2 — Main N' },
        { id: 'desk_3', x: 11, y: 4,  label: 'Desk 3 — Main NE' },
        { id: 'desk_4', x: 3,  y: 6,  label: 'Desk 4 — Main SW' },
        { id: 'desk_5', x: 7,  y: 6,  label: 'Desk 5 — Main S' },
        { id: 'desk_6', x: 3,  y: 15, label: 'Desk 6 — South NW' },
        { id: 'desk_7', x: 7,  y: 15, label: 'Desk 7 — South N' },
        { id: 'desk_8', x: 11, y: 15, label: 'Desk 8 — South NE' },
    ];

    /** The five activation types an agent assigns a connection to. */
    const MODEL_TYPES = [
        { key: 'model_social',     label: 'Social (cheap)' },
        { key: 'model_work',       label: 'Work (routine)' },
        { key: 'model_reasoning',  label: 'Reasoning (deep)' },
        { key: 'model_extraction', label: 'Extraction' },
        { key: 'model_self_queue', label: 'Self-queue' },
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
     * @param {object|null} agent
     * @param {object[]} roster
     * @returns {{selectedDesk: object|null, noFreeDesk: boolean, deskOptions: string}}
     *   The agent's own desk wins; otherwise the first unoccupied one. When
     *   neither exists the caller says so rather than silently seating them on
     *   top of a teammate.
     */
    function deskChoice(agent, roster) {
        const occupiedChairs = new Set(
            (roster || [])
                .filter((item) => item.id !== agent?.id && item.desk_x != null && item.desk_y != null)
                .map((item) => `${item.desk_x},${item.desk_y}`)
        );
        const assignedDesk = agent?.desk_x != null && agent?.desk_y != null
            ? DESK_OPTIONS.find((d) => d.x === agent.desk_x && d.y === agent.desk_y)
            : null;
        const freeDesk = DESK_OPTIONS.find((d) => !occupiedChairs.has(`${d.x},${d.y}`)) || null;
        const selectedDesk = assignedDesk || freeDesk;
        const deskOptions = DESK_OPTIONS.map(d => {
            const selected = selectedDesk && selectedDesk.x === d.x && selectedDesk.y === d.y;
            const taken = occupiedChairs.has(`${d.x},${d.y}`);
            return `<option value="${d.x},${d.y}" ${selected ? 'selected' : ''}>${d.label}${taken ? ' (taken)' : ''}</option>`;
        }).join('');
        return { selectedDesk, noFreeDesk: !assignedDesk && !freeDesk, deskOptions };
    }

    return {
        AGENT_COLORS,
        DESK_OPTIONS,
        MODEL_TYPES,
        DEFAULT_PROMPT_HISTORY_POLICY,
        deskChoice,
    };
})();
