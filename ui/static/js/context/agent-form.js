/**
 * BossMod AI — builds the agent form and wires the controls inside it.
 *
 * The composition half of what `buildFormHTML` used to be: it fetches what the
 * form needs, asks each field group for its section in reading order, and
 * binds the three controls that only make sense once the whole form exists —
 * the two Settings links, the "Set All" connection convenience, and the
 * Advanced disclosure. The per-field behaviours are
 * context/agent-form-bindings.js.
 *
 * MARKUP EXEMPTION: see the block comment in context/agent-form-fields.js.
 * The `<form>` wrapper below is the only markup this module owns.
 */
const BossModAgentForm = (() => {

    /**
     * Load the connections, personalities, roster, and history policy the form
     * renders from.
     *
     * @param {object|null} agent
     * @returns {Promise<{connections: object[], personalities: object[],
     *                    roster: object[], promptHistoryPolicy: object}>}
     *   A failed load returns empty lists and the default policy, so the form
     *   still renders with its "no connections configured" and "no
     *   personalities configured" links to Settings rather than not rendering
     *   at all. The failure is logged; it is never presented as success,
     *   because those two empty states say exactly what is missing.
     */
    async function loadFormData(agent) {
        const DEFAULTS = BossModAgentFields.DEFAULT_PROMPT_HISTORY_POLICY;
        let connections = [];
        let personalities = [];
        let roster = [];
        let promptHistoryPolicy = { ...DEFAULTS };
        try {
            const requests = [
                apiFetch('/api/connections'),
                apiFetch('/api/personalities'),
                apiFetch('/api/agents'),
            ];
            if (agent?.id) {
                requests.push(BossModAgentApi.fetchPromptHistoryPolicy(agent.id));
            }
            const [connRes, persRes, rosterRes, policyRes] = await Promise.all(requests);
            connections = await connRes.json();
            personalities = await persRes.json();
            roster = await rosterRes.json();
            if (policyRes) {
                promptHistoryPolicy = { ...DEFAULTS, ...policyRes };
            }
        } catch (err) {
            console.error('[agent-form] Failed to load agent editor dependencies:', err);
        }
        return { connections, personalities, roster, promptHistoryPolicy };
    }

    /**
     * Render the form into a container and bind everything inside it.
     *
     * @param {HTMLElement} container
     * @param {object|null} [agent]  null to hire, a roster row to edit.
     * @returns {Promise<void>}
     */
    async function buildFormHTML(container, agent = null) {
        const { connections, personalities, roster, promptHistoryPolicy } = await loadFormData(agent);

        container.innerHTML = `
        <form id="agent-form" class="space-y-4">
            ${BossModAgentFormFields.nameField(agent)}
            ${BossModAgentFormFields.roleContractCard(agent, roster)}
            ${BossModAgentFormFields.connectionsSection(agent, connections)}
            ${BossModAgentFormAdvanced.advancedSection(agent, {
                personalities, roster, promptHistoryPolicy,
            })}
            ${BossModAgentFormFields.statusAndRecovery(agent)}
            ${BossModAgentFormFields.actionsRow(agent)}
        </form>
        `;

        // Settings navigation links
        // Phase 2B: the dock-era app.js is no longer loaded, so the
        // BossModApp nav call that used to follow this threw
        // as soon as the form was hosted in the new shell. The shell's own gear
        // opens the takeover with SettingsView.open() and nothing else.
        const gotoSettings = () => {
            SettingsView.open();
        };
        const gotoConn = container.querySelector('#btn-goto-connections');
        if (gotoConn) gotoConn.addEventListener('click', gotoSettings);
        const gotoPers = container.querySelector('#btn-goto-personalities');
        if (gotoPers) gotoPers.addEventListener('click', gotoSettings);

        // "Set All" connection convenience dropdown
        const setAllSelect = container.querySelector('select[name="model_all"]');
        if (setAllSelect) {
            setAllSelect.addEventListener('change', () => {
                if (!setAllSelect.value) return;
                BossModAgentFields.MODEL_TYPES.forEach(t => {
                    const sel = container.querySelector(`select[name="${t.key}"]`);
                    if (sel) sel.value = setAllSelect.value;
                });
            });
        }

        const advancedToggle = container.querySelector('#advanced-toggle');
        const advancedContent = container.querySelector('#advanced-content');
        const advancedChevron = container.querySelector('#advanced-chevron');
        if (advancedToggle && advancedContent && advancedChevron) {
            advancedToggle.addEventListener('click', () => {
                advancedContent.classList.toggle('hidden');
                advancedChevron.style.transform = advancedContent.classList.contains('hidden') ? '' : 'rotate(90deg)';
            });
            if (window.lucide) lucide.createIcons({ nodes: [advancedToggle] });
        }

        const BINDINGS = BossModAgentFormBindings;
        BINDINGS.bindFinishLineSuggestion(container, agent);
        BINDINGS.bindRuntimeCorePreview(container, agent);
        BINDINGS.bindDuplicateNameWarning(container, roster, agent);
    }

    return { buildFormHTML, loadFormData };
})();
