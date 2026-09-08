/**
 * BossMod AI — the agent form's AI Connections matrix.
 *
 * The fourth section context/agent-form-fields.js hands the assembler, and the
 * second to leave it: the Advanced disclosure went first, on the grounds that
 * 120 lines of markup is a module rather than a paragraph, and round five's
 * three-column grid took this one past the same line. Its parent holds four
 * sections and no more room; splitting the fieldset that grew is the seam
 * that exists, not the one that makes a number go down.
 *
 * MARKUP, and why this module claims no exemption: it builds template strings
 * for the same reason its parent does — see the block comment in
 * context/agent-form-fields.js — and every value it interpolates is
 * operator-entered configuration passed through BossModFormat. It neither
 * writes nor reads the property test_ui_index.py's rule is actually about, so
 * it is absent from that exemption list and needs no entry on it. Whether the
 * list should also name string-markup modules that never touch that property
 * is a policy question for the operator, not one this file may answer.
 */
const BossModAgentFormConnections = (() => {
    const FIELDS = BossModAgentFields;

    /* The dom id joining one connection dropdown to its `<label for>`. */
    const connectionSelectId = (modelKey) => `agent-connection-${modelKey}`;

    /* `connection-select` is hand-authored (overlays.css) and carries the
       truncation; `w-full` not `flex-1` — a select fills a grid cell now. */
    const SELECT_CLASS = `connection-select w-full px-2 py-1.5 text-xs border border-bm-border rounded
                          bg-bm-bg focus:outline-none focus:ring-1 focus:ring-bm-accent/30`;

    /**
     * The `<option>` list both kinds of connection dropdown share.
     *
     * The full label goes in a `title` as well as in the text: three columns
     * buy ~225px per select and a label is arbitrary operator input, so the
     * visible text can clip. The title — and the native popup, which is not
     * bound to the control's width — is what keeps a clipped label readable.
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
            return `<option value="${c.id}" title="${BossModFormat.escapeAttribute(label)}"${selected ? ' selected' : ''}>${BossModFormat.escapeHtml(label)}</option>`;
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
        return `<select name="${modelKey}" id="${connectionSelectId(modelKey)}" class="${SELECT_CLASS}">
                <option value="">None</option>
                ${connectionOptions(connections, currentValue)}
            </select>`;
    }

    /**
     * The model matrix: one connection per activation type, plus "Set All".
     *
     * THREE COLUMNS, each label above its select. Five full-width rows cost
     * ~264px of a dialog that already scrolls, and a side label eats the width
     * the select needs. Five columns was rejected: ~140px truncates at ~18
     * characters and real labels already exceed that. The count and its two
     * narrower fallbacks are in overlays.css, which lays the grid out.
     *
     * Every label is a `<label for>`, never a `<span>`: an unassociated label
     * on a form control is a screen-reader dead end, and above is exactly
     * where one is easiest to ship by accident. "Set All" stays full width
     * above the rule that already separated it — it writes to the other five
     * rather than being a sixth value, and that is what stops it reading as
     * one of them.
     *
     * @param {object|null} agent
     * @param {object[]} connections
     * @returns {string} With no connections configured, a link to Settings —
     *   an empty matrix would look like a broken form.
     */
    function connectionsSection(agent, connections) {
        const noConnections = connections.length === 0;
        const MODEL_TYPES = FIELDS.MODEL_TYPES;
        return `
        <!-- AI Connections (Model Matrix) -->
        <div>
            <label class="block text-sm font-medium mb-2">AI Connections</label>
            ${noConnections
                ? `<p class="text-xs text-bm-muted">No connections configured.
                     <button type="button" id="btn-goto-connections" class="text-bm-accent hover:underline">Add one in Settings</button></p>`
                : `<p class="text-xs text-bm-muted mb-2">Assign an AI connection to each activation type.</p>
                   <div class="space-y-2">
                       <div>
                           <label for="${connectionSelectId('model_all')}"
                                  class="block text-xs font-medium text-bm-text mb-1">Set All</label>
                           <select name="model_all" id="${connectionSelectId('model_all')}" class="${SELECT_CLASS}">
                               <option value="">— Set all connections —</option>
                               ${connectionOptions(connections, null)}
                           </select>
                       </div>
                       <hr class="border-bm-border">
                       <div class="connection-grid">
                           ${MODEL_TYPES.map(t => `<div>
                               <label for="${connectionSelectId(t.key)}"
                                      class="block text-xs text-bm-muted mb-1">${t.label}</label>
                               ${connectionSelect(connections, t.key, agent?.[t.key])}
                           </div>`).join('')}
                       </div>
                   </div>`
            }
        </div>`;
    }

    return { connectionSelect, connectionsSection };
})();
