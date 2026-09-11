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
 * BOUND TO THE FORM, NEVER TO THE HOST. `container` is scaffolding:
 * context/agent-form-save.js builds onto a detached stage and publishes with
 * `replaceChildren(...stage.children)`, which MOVES the `<form>` out and
 * leaves the stage empty. A listener that re-queries the host it was handed
 * therefore searches an emptied node for the rest of its life — which is
 * exactly what silently killed "Set All": every `querySelector` came back
 * null, the guard swallowed it, and the quick create path wrote five null
 * connections while reporting success. The `<form>` is the node that travels,
 * so it is the node every binding here holds.
 *
 * MARKUP EXEMPTION: see the block comment in context/agent-form-fields.js.
 * The `<form>` wrapper below is the only markup this module owns.
 */
const BossModAgentForm = (() => {
    const { h } = BossModDom;

    /**
     * What each dependency is CALLED when the operator is told it failed.
     *
     * The path is what the console line needs; this is what the sentence in
     * front of the operator needs, and they are not the same string.
     */
    /** The one read that is not a list, and so is not named by a path. */
    const POLICY = 'the AI history policy';

    const DEPENDENCY_NAMES = Object.freeze({
        '/api/connections': 'your AI connections',
        '/api/personalities': 'your personalities',
        '/api/agents': 'the rest of your roster',
        [POLICY]: 'this agent’s AI history settings',
    });

    /**
     * Read one of the form's list dependencies.
     *
     * @param {Response} res
     * @param {string} what  The path, named in the error: four reads share
     *   this shape, and "which one" is the first thing a reader of the failure
     *   needs. `settledList` below is what catches it.
     * @returns {Promise<object[]>}
     * @throws {Error} On a non-2xx, or on a body that is not a list. Both used
     *   to be assigned as-is, and an error response carries a `{detail}`
     *   object — so a 500 from /api/connections reached the matrix, which
     *   iterates what it is given, and threw `connections.map is not a
     *   function` from inside the renderer. The operator was then told the
     *   whole editor had failed to load. The same reasoning
     *   context/agent-form-save.js's readConnections already applies, with the
     *   opposite CONSEQUENCE: `settledList` below degrades this to the
     *   documented empty list, because it decides what the form SHOWS.
     */
    async function readList(res, what) {
        if (!res.ok) throw new Error(`GET ${what} answered ${res.status}`);
        const body = await res.json();
        if (!Array.isArray(body)) {
            throw new Error(`GET ${what} did not answer with a list`);
        }
        return body;
    }

    /**
     * One dependency's outcome, degraded ON ITS OWN.
     *
     * `Promise.all` used to carry all four, so a single REJECTED request — a
     * network error, an abort — rejected the batch and the one catch below
     * left every list empty. An operator with two connections configured was
     * then shown "No connections configured. Add one in Settings", no matrix
     * to choose from, and a live primary, because `/api/personalities` was
     * down. A failing read must cost its own list and nothing else.
     *
     * @param {PromiseSettledResult<Response>} outcome  From Promise.allSettled.
     * @param {string} what  The path, named in the console line and collected
     *   in `failed`: four reads share this and "which one" is the first thing
     *   either the log or the operator needs.
     * @param {string[]} failed  Appended to when this read fails.
     * @returns {Promise<object[]|null>} null when it failed, which is NOT the
     *   empty list: empty means "you have none configured" and the form's own
     *   empty states say exactly that, which is a different and checkable
     *   fact. `failed` is what keeps the two apart.
     */
    async function settledList(outcome, what, failed) {
        try {
            if (outcome.status === 'rejected') throw outcome.reason;
            return await readList(outcome.value, what);
        } catch (err) {
            console.error(`[agent-form] ${what} could not be read:`, err);
            failed.push(what);
            return null;
        }
    }

    /**
     * Load the connections, personalities, roster, and history policy the form
     * renders from.
     *
     * @param {object|null} agent
     * @returns {Promise<{connections: object[], personalities: object[],
     *                    roster: object[], promptHistoryPolicy: object,
     *                    failed: string[]}>}
     *   Every read degrades alone, and a failed one leaves its list empty so
     *   the form still renders with its "no connections configured" and "no
     *   personalities configured" links to Settings rather than not rendering
     *   at all. `failed` names the reads that did not land, because an empty
     *   list the operator is shown as fact and an empty list standing in for a
     *   read that never returned are different things — and the empty states
     *   above cannot tell them apart on their own.
     */
    async function loadFormData(agent) {
        const DEFAULTS = BossModAgentFields.DEFAULT_PROMPT_HISTORY_POLICY;
        const failed = [];
        const requests = [
            apiFetch('/api/connections'),
            apiFetch('/api/personalities'),
            apiFetch('/api/agents'),
        ];
        if (agent?.id) {
            requests.push(BossModAgentApi.fetchPromptHistoryPolicy(agent.id));
        }
        const [connRes, persRes, rosterRes, policyRes] = await Promise.allSettled(requests);
        const connections = await settledList(connRes, '/api/connections', failed);
        const personalities = await settledList(persRes, '/api/personalities', failed);
        const roster = await settledList(rosterRes, '/api/agents', failed);
        let promptHistoryPolicy = { ...DEFAULTS };
        if (policyRes && policyRes.status === 'fulfilled' && policyRes.value) {
            promptHistoryPolicy = { ...DEFAULTS, ...policyRes.value };
        } else if (policyRes && policyRes.status === 'rejected') {
            console.error(`[agent-form] ${POLICY} could not be read:`, policyRes.reason);
            failed.push(POLICY);
        }
        // Rendered as empty, REPORTED as failed. The sections below take a
        // list and nothing else; `failed` is the half that stops an empty one
        // reaching the operator as a statement about their configuration.
        return {
            connections: connections || [],
            personalities: personalities || [],
            roster: roster || [],
            promptHistoryPolicy,
            failed,
        };
    }

    /**
     * The line that says which dependencies could not be read.
     *
     * NOT a live region, and deliberately: it is present when the form first
     * appears rather than announced into it, and the editor's one live region
     * is context/agent-recovery.js's feedback line, which the save path owns.
     *
     * @param {string[]} failed  From loadFormData; never empty here.
     * @returns {HTMLElement}
     * @throws {Error} On a path with no operator-facing name. A dependency
     *   added without one would otherwise be reported to the operator as a URL.
     */
    function degradedNotice(failed) {
        const names = failed.map((what) => {
            const name = DEPENDENCY_NAMES[what];
            if (!name) throw new Error(`[agent-form] ${what} has no name to report`);
            return name;
        });
        return h('p', { class: 'form-degraded', id: 'agent-form-degraded' },
            `Couldn’t read ${names.join(', ')}. What the form shows there is `
            + 'empty because the read failed, not because you have none. Close '
            + 'this dialog and open it again to retry.');
    }

    /**
     * Render the form into a container and bind everything inside it.
     *
     * @param {HTMLElement} container
     * @param {object|null} [agent]  null to hire, a roster row to edit.
     * @returns {Promise<void>}
     */
    async function buildFormHTML(container, agent = null) {
        const {
            connections, personalities, roster, promptHistoryPolicy, failed,
        } = await loadFormData(agent);

        // TWO COLUMNS, ONE FORM, and the same one whichever door was used.
        // Identity on the left, what the agent thinks with on the right, the
        // optional rest across the bottom. Picking a template used to sweep
        // everything from the role contract down behind a collapsed disclosure,
        // so the colour swatches and the whole connection matrix — the two
        // things an operator most needs to see before creating an agent — were
        // exactly what the template path hid. A template prefills these fields
        // now and adds a provenance chip above them; it changes nothing else.
        //
        // The section wrappers are HERE rather than inside each field group:
        // Identity spans two of them (`nameField` and `roleContractCard`), and
        // a <section> opened in one function and closed in another is a shape
        // no reader can check. Composition is the assembler's job, which is
        // what this module is.
        container.innerHTML = `
        <form id="agent-form">
            <div class="agent-form-grid">
                <section class="form-section">
                    <h3 class="form-section-title">Identity</h3>
                    ${BossModAgentFormFields.nameField(agent)}
                    ${BossModAgentFormFields.roleContractCard(agent, roster)}
                </section>
                ${BossModAgentFormConnections.connectionsSection(agent, connections)}
                <div class="agent-form-wide">
                    ${BossModAgentFormAdvanced.advancedSection(agent, {
                        personalities, roster, promptHistoryPolicy,
                    })}
                    ${BossModAgentFormFields.statusAndRecovery(agent)}
                    ${BossModAgentFormFields.actionsRow(agent)}
                </div>
            </div>
        </form>
        `;

        // The node every binding below holds — see the header. Absent means
        // the markup above did not parse, which is not something to bind
        // around: the caller's failure path is the honest answer.
        const form = container.querySelector('#agent-form');
        if (!form) throw new Error('[agent-form] the form markup did not build');
        // First thing in the form, so it is read before the empty state it is
        // there to correct. Built with h(), not folded into the markup above:
        // the exemption this module carries is for the <form> wrapper.
        if (failed.length) form.prepend(degradedNotice(failed));

        // Settings navigation links
        // Phase 2B: the dock-era app.js is no longer loaded, so the
        // BossModApp nav call that used to follow this threw
        // as soon as the form was hosted in the new shell. The shell's own gear
        // opens the takeover with SettingsView.open() and nothing else.
        const gotoSettings = () => {
            SettingsView.open();
        };
        const gotoConn = form.querySelector('#btn-goto-connections');
        if (gotoConn) gotoConn.addEventListener('click', gotoSettings);
        const gotoPers = form.querySelector('#btn-goto-personalities');
        if (gotoPers) gotoPers.addEventListener('click', gotoSettings);

        // WHICH SHAPE the connections section just rendered, written onto the
        // form for context/agent-form-save.js's refusal to read back. Set from
        // the same list the section was built from, immediately after it was
        // built, so the attribute and the markup cannot disagree — and by
        // agent-form-connections.js's own helper, so the vocabulary has one
        // owner. See that module's header for why the refusal has to ask.
        form.setAttribute(
            BossModAgentFormConnections.AI_QUESTION,
            BossModAgentFormConnections.shapeFor(connections),
        );

        // "Set All" connection convenience dropdown. Re-queried on each change
        // rather than captured: the nodes survive any rearrangement, their
        // place in the tree may not. The root it searches is the form for the
        // reason in the header.
        const setAllSelect = form.querySelector('select[name="model_all"]');
        if (setAllSelect) {
            setAllSelect.addEventListener('change', () => {
                if (!setAllSelect.value) return;
                BossModAgentFields.MODEL_TYPES.forEach(t => {
                    const sel = form.querySelector(`select[name="${t.key}"]`);
                    if (sel) sel.value = setAllSelect.value;
                });
            });
        }

        const advancedToggle = form.querySelector('#advanced-toggle');
        const advancedContent = form.querySelector('#advanced-content');
        if (advancedToggle && advancedContent) {
            // Painted BEFORE the chevron is looked up, and that order is the
            // whole point: painting REPLACES the `<i data-lucide>` placeholder
            // with an SVG, so a reference taken first would be to a node that
            // is no longer in the form and the rotation below would style a
            // detached element nobody can see.
            BossModIcons.paint(advancedToggle, 'agent-form.advanced');
            const advancedChevron = form.querySelector('#advanced-chevron');
            if (advancedChevron) {
                advancedToggle.addEventListener('click', () => {
                    advancedContent.classList.toggle('hidden');
                    advancedChevron.style.transform = advancedContent.classList.contains('hidden') ? '' : 'rotate(90deg)';
                });
            }
        }

        const BINDINGS = BossModAgentFormBindings;
        BINDINGS.bindFinishLineSuggestion(form, agent);
        BINDINGS.bindRuntimeCorePreview(form, agent);
        BINDINGS.bindDuplicateNameWarning(form, roster, agent);
        BINDINGS.bindColorSwatchInitial(form);
        BINDINGS.bindDescriptionAutoGrow(form);
        // LAST, and after the "Set All" fan-out above it — the ordering is
        // load-bearing. The fan-out writes the five per-type selects FROM
        // SCRIPT, and a value assigned that way fires no change event of its
        // own, so the guard has to be registered after it to read the five
        // once they have been written. Registered the other way round it would
        // simply stay armed one interaction longer, which is the safe side.
        BINDINGS.bindConnectionGuard(form, { creating: !agent });
    }

    return { buildFormHTML, loadFormData };
})();
