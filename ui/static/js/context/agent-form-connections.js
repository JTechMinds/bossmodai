/**
 * BossMod AI — the agent form's AI Connections matrix, and which shape it is in.
 *
 * The fourth section context/agent-form-fields.js hands the assembler, and the
 * second to leave it: the Advanced disclosure went first, on the grounds that
 * 120 lines of markup is a module rather than a paragraph, and round five's
 * multi-column grid took this one past the same line. Its parent holds four
 * sections and no more room; splitting the fieldset that grew is the seam that
 * exists, not the one that makes a number go down.
 *
 * TWO SHAPES, AND THIS MODULE NAMES THEM. With a connection configured the
 * section renders "Set all" over the five per-activation-type selects; with
 * none it renders a link to Settings and no select at all. Those are different
 * forms, and context/agent-form-save.js's refusal to create a connectionless
 * agent has to name a control the operator can actually see — one sentence
 * cannot be true of both. So the shape is written onto the form as an
 * attribute and read back through `aiQuestion`, and the module that RENDERS
 * the two is the one that says which was rendered. It used to be written by
 * the module that moved controls around after the fact; nothing moves them
 * now, and an attribute whose author is not its renderer is an attribute that
 * goes stale.
 *
 * MARKUP, and why this module claims no exemption: it builds template strings
 * for the same reason its parent does — see the block comment in
 * context/agent-form-fields.js — and every value it interpolates is
 * operator-entered configuration passed through BossModFormat. It neither
 * writes nor reads the property test_ui_index.py's rule is actually about, so
 * it is absent from that exemption list and needs no entry on it.
 */
const BossModAgentFormConnections = (() => {
    const FIELDS = BossModAgentFields;

    /* The dom id joining one connection dropdown to its `<label for>`. */
    const connectionSelectId = (modelKey) => `agent-connection-${modelKey}`;

    /* `.field-select` is the app's shared control (controls.css) and carries
       both the --line-control border SC 1.4.11 asks of a control's only
       affordance and the truncation a narrow column needs. `connection-select`
       is kept beside it as the matrix's own hook. */
    const SELECT_CLASS = 'connection-select field-select';

    /**
     * WHERE this form's AI connection question ended up, written on the form.
     *
     * context/agent-form-save.js refuses a create that would carry no
     * connection, and that refusal has to name a control the operator can
     * actually see and reach. The question has two homes and they are not
     * interchangeable: the matrix, and — when Settings holds nothing to choose
     * from — the notice pointing at Settings, with no matrix at all. A sentence
     * naming "AI Connections" points at nothing on the second.
     *
     * So the form is asked. The save handler learns no layout, and the answer
     * comes from the only honest source there is: the form itself.
     */
    const AI_QUESTION = 'data-ai-question';
    const MATRIX = 'matrix';
    const UNAVAILABLE = 'unavailable';

    /**
     * Which shape `connectionsSection` will render for these connections.
     *
     * Called by context/agent-form.js immediately after it publishes the
     * markup, with the same list this module was handed — so the attribute and
     * the markup cannot disagree about which branch ran.
     *
     * @param {object[]} connections
     * @returns {'matrix'|'unavailable'}
     */
    function shapeFor(connections) {
        return (connections || []).length ? MATRIX : UNAVAILABLE;
    }

    /**
     * Which of the two shapes a form's AI question is in.
     *
     * @param {HTMLElement} form  The `<form>` itself.
     * @returns {'matrix'|'unavailable'} A form carrying no attribute is the
     *   matrix: that is the shape every form has unless the connections list
     *   was empty, and it is the one whose copy names a control that exists.
     * @throws {Error} On an attribute this module did not write. A shape nobody
     *   has copy for would otherwise be refused with a sentence chosen for a
     *   different layout, which is the defect this vocabulary closes.
     */
    function aiQuestion(form) {
        const shape = form.getAttribute(AI_QUESTION);
        if (shape === null) return MATRIX;
        if (shape !== MATRIX && shape !== UNAVAILABLE) {
            throw new Error(`[agent-form-connections] the form claims AI question "${shape}"`);
        }
        return shape;
    }

    /**
     * The `<option>` list both kinds of connection dropdown share.
     *
     * The full label goes in a `title` as well as in the text: two columns buy
     * ~215px per select and a label is arbitrary operator input, so the visible
     * text can clip. The title — and the native popup, which is not bound to
     * the control's width — is what keeps a clipped label readable.
     *
     * @param {object[]} connections
     * @param {string|null} currentValue  The model name stored on the agent.
     * @returns {string}
     */
    function connectionOptions(connections, currentValue) {
        return connections.map(c => {
            const label = c.model ? `${c.name} (${c.model})` : c.name;
            // Match by combining connection fields into what would have been stored
            const selected = currentValue && (
                currentValue === c.model || currentValue === c.name
            );
            return `<option value="${BossModFormat.escapeAttribute(c.id)}" title="${BossModFormat.escapeAttribute(label)}"${selected ? ' selected' : ''}>${BossModFormat.escapeHtml(label)}</option>`;
        }).join('');
    }

    /**
     * One connection dropdown for one activation type.
     *
     * THREE parameters, and it was called with two for an unknown period, so
     * the whole fieldset threw for any operator with a connection configured.
     *
     * @param {object[]} connections
     * @param {string} modelKey
     * @param {string|null} currentValue  The model name stored on the agent.
     * @returns {string}
     */
    function connectionSelect(connections, modelKey, currentValue) {
        return `<select name="${BossModFormat.escapeAttribute(modelKey)}"
                    id="${BossModFormat.escapeAttribute(connectionSelectId(modelKey))}" class="${SELECT_CLASS}">
                <option value="">None</option>
                ${connectionOptions(connections, currentValue)}
            </select>`;
    }

    /**
     * The model matrix: one connection per activation type, plus "Set All".
     *
     * ALWAYS VISIBLE, on both create paths. Picking a template used to sweep
     * this whole section behind a collapsed disclosure and lift a single select
     * out of it, so the one decision that decides whether a new agent can take
     * a turn at all was the one the template path hid.
     *
     * Every label is a `<label for>`, never a `<span>`: an unassociated label
     * on a form control is a screen-reader dead end, and above is exactly where
     * one is easiest to ship by accident. "Set All" stays full width above the
     * rule that already separated it — it writes to the other five rather than
     * being a sixth value, and that is what stops it reading as one of them.
     *
     * @param {object|null} agent
     * @param {object[]} connections
     * @returns {string} With no connections configured, a link to Settings —
     *   an empty matrix would look like a broken form. `shapeFor` above is what
     *   tells the rest of the dialog which of the two it built.
     */
    function connectionsSection(agent, connections) {
        const noConnections = connections.length === 0;
        const MODEL_TYPES = FIELDS.MODEL_TYPES;
        return `
        <!-- AI Connections (Model Matrix) -->
        <section class="form-section">
            <h3 class="form-section-title">AI Connections</h3>
            ${noConnections
                ? `<p class="field-hint">No connections configured.
                     <button type="button" id="btn-goto-connections" class="btn-link">Add one in Settings</button></p>`
                : `<p class="field-hint">Assign an AI connection to each activation type.</p>
                   <div class="field">
                       <label for="${BossModFormat.escapeAttribute(connectionSelectId('model_all'))}"
                              class="field-label">Set All</label>
                       <select name="model_all"
                               id="${BossModFormat.escapeAttribute(connectionSelectId('model_all'))}"
                               class="${SELECT_CLASS}">
                           <option value="">— Set all connections —</option>
                           ${connectionOptions(connections, null)}
                       </select>
                   </div>
                   <hr class="form-rule">
                   <div class="connection-grid">
                       ${MODEL_TYPES.map(t => `<div class="field">
                           <label for="${BossModFormat.escapeAttribute(connectionSelectId(t.key))}"
                                  class="field-label field-label-sm">${t.label}</label>
                           ${connectionSelect(connections, t.key, agent?.[t.key])}
                       </div>`).join('')}
                   </div>`
            }
        </section>`;
    }

    return {
        AI_QUESTION, MATRIX, UNAVAILABLE, shapeFor, aiQuestion,
        connectionSelect, connectionsSection,
    };
})();
