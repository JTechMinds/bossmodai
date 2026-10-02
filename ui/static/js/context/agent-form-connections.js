/**
 * BossMod AI — the agent form's AI Connection section, and which shape it is in.
 *
 * The fourth section context/agent-form-fields.js hands the assembler. ONE
 * connection per agent, read live by the runtime on every turn, so the model
 * this section names is the model the agent sends — there is no second copy
 * for it to drift from. What varies by activation is only the thinking level,
 * and only for the two activations the runtime routes (Social and Work, see
 * BossModAgentFields.THINKING_MODES). A level is offered when the chosen
 * connection defines it (Settings → Connections → Thinking levels).
 *
 * MARKUP HERE, CONTROLS IN THE BINDINGS. This module renders the section's
 * frame: labels, hints, the hidden inputs the submit reads, and an empty mount
 * point per control. The three dropdowns are BossModMenuSelect (never a native
 * <select>), which are DOM nodes, so context/agent-form-bindings.js
 * (`bindAiConnection`) mounts them once the form exists. The hidden inputs are
 * what context/agent-submit.js reads: `startingValues` decides what they start
 * at — the markup and the bindings both take it from there — and the menus
 * write them on change.
 *
 * TWO SHAPES, AND THIS MODULE NAMES THEM. With a connection configured the
 * section renders the picker; with none it renders a link to Settings and no
 * control at all. context/agent-form-save.js's refusal to create a
 * connectionless agent has to name a control the operator can actually see,
 * so the shape is written onto the form as an attribute and read back through
 * `aiQuestion`, by the module that rendered it.
 *
 * A STORED CHOICE MAY NO LONGER HOLD. An agent may be unlinked (the upgrade
 * could not tell which connection it used), its connection may be gone, or a
 * stored thinking level may no longer be offered. The control cannot show a
 * value it does not hold, so it shows "Choose a connection" or Server default,
 * and a note under the section says what was stored and that it needs a pick.
 *
 * MARKUP, and why this module claims no exemption: it builds template strings
 * for the same reason its parent does — see the block comment in
 * context/agent-form-fields.js — and every value it interpolates is
 * operator-entered configuration passed through BossModFormat.
 */
const BossModAgentFormConnections = (() => {
    const FIELDS = BossModAgentFields;

    /**
     * WHERE this form's AI connection question ended up, written on the form:
     * the picker, or — when Settings holds nothing to choose from — the notice
     * pointing at Settings. The save handler asks the form, and learns no
     * layout.
     */
    const AI_QUESTION = 'data-ai-question';
    const PICKER = 'picker';
    const UNAVAILABLE = 'unavailable';

    /** The connection picker's value while nothing is chosen. */
    const NONE = '';

    /**
     * Which shape `connectionsSection` will render for these connections.
     *
     * @param {object[]} connections
     * @returns {'picker'|'unavailable'}
     */
    function shapeFor(connections) {
        return (connections || []).length ? PICKER : UNAVAILABLE;
    }

    /**
     * Which of the two shapes a form's AI question is in.
     *
     * @param {HTMLElement} form  The `<form>` itself.
     * @returns {'picker'|'unavailable'} A form carrying no attribute is the
     *   picker: that is every form's shape unless the list was empty.
     * @throws {Error} On an attribute this module did not write: a shape
     *   nobody has copy for would be refused with a sentence chosen for a
     *   different layout.
     */
    function aiQuestion(form) {
        const shape = form.getAttribute(AI_QUESTION);
        if (shape === null) return PICKER;
        if (shape !== PICKER && shape !== UNAVAILABLE) {
            throw new Error(`[agent-form-connections] the form claims AI question "${shape}"`);
        }
        return shape;
    }

    /**
     * The dom id of the empty node a dropdown is mounted into.
     *
     * @param {string} key  `connection_id`, or a THINKING_MODES key.
     * @returns {string}
     */
    const mountId = (key) => `agent-ai-mount-${key}`;

    /** `"<name> (<model>)"`, the one label a connection is shown under. */
    function connectionLabel(connection) {
        return `${connection.name} (${connection.model || 'no model'})`;
    }

    /**
     * The connection picker's options.
     *
     * @param {object[]} connections  Never empty: only the picker shape asks.
     * @param {string} current  The chosen id, or '' while none is.
     * @returns {Array<{value: string, label: string}>} With a "Choose a
     *   connection" row first only while nothing is chosen: once a connection
     *   is picked there is no unlinking from here, so the row would offer a
     *   choice the save cannot make.
     */
    function connectionOptions(connections, current) {
        const rows = connections.map((c) => ({ value: c.id, label: connectionLabel(c) }));
        return current === NONE ? [{ value: NONE, label: 'Choose a connection' }, ...rows] : rows;
    }

    /**
     * The thinking choices a connection offers: Server default, then each
     * level its map defines, in the vocabulary's order.
     *
     * @param {object|null} connection  null while none is chosen.
     * @returns {Array<{value: string, label: string}>}
     */
    function thinkingOptions(connection) {
        const offered = (connection && connection.thinking_levels) || {};
        return FIELDS.THINKING_CHOICES
            .filter((choice) => choice.value === 'default' || Object.hasOwn(offered, choice.value))
            .map(({ value, label }) => ({ value, label }));
    }

    /**
     * The values the section starts from, and what could not be kept.
     *
     * @param {object|null} values  The agent being edited or the snapshot
     *   being recreated; null for a blank form.
     * @param {object[]} connections
     * @returns {{connectionId: string, thinking: object, notes: string[]}}
     *   `thinking` maps each THINKING_MODES key to a choice the chosen
     *   connection offers. `notes` is one plain-text line per stored choice
     *   that did not hold; a blank form has nothing stored, so none.
     */
    function startingValues(values, connections) {
        const stored = values?.connection_id || NONE;
        const connection = connections.find((c) => c.id === stored) || null;
        const connectionId = connection ? connection.id : NONE;
        const notes = [];
        if (values && !connection) notes.push('This agent has no AI connection — choose one.');
        const offered = new Set(thinkingOptions(connection).map((option) => option.value));
        const thinking = {};
        for (const mode of FIELDS.THINKING_MODES) {
            const choice = values?.[mode.key] || 'default';
            thinking[mode.key] = offered.has(choice) ? choice : 'default';
            if (connection && !offered.has(choice)) {
                notes.push(`${mode.label}: thinking “${choice}” — this connection doesn't offer it; pick one.`);
            }
        }
        return { connectionId, thinking, notes };
    }

    /**
     * The AI Connection section: one connection, then a thinking level per
     * routed activation.
     *
     * Every control is labelled by visible text the menu's trigger repeats in
     * its accessible name (`${label}: ${choice}`), since a `<label for>`
     * cannot point at a mount point.
     *
     * @param {object|null} values  The agent being edited, or the snapshot
     *   being recreated. null for a blank form.
     * @param {object[]} connections
     * @returns {string} With no connections configured, a link to Settings.
     */
    function connectionsSection(values, connections) {
        if (connections.length === 0) {
            return `
        <section class="form-section">
            <h3 class="form-section-title">AI Connection</h3>
            <p class="field-hint">No connections configured.
                <button type="button" id="btn-goto-connections" class="btn-link">Add one in Settings</button></p>
        </section>`;
        }
        const start = startingValues(values, connections);
        const notes = start.notes.length
            ? `<ul class="connection-missing" id="agent-connection-missing">${start.notes
                .map((line) => `<li>${BossModFormat.escapeHtml(line)}</li>`).join('')}</ul>`
            : '';
        return `
        <section class="form-section">
            <h3 class="form-section-title">AI Connection</h3>
            <p class="field-hint">The connection, and its model, this agent uses for everything it does.</p>
            <div class="field">
                <span class="field-label">Connection</span>
                <div id="${BossModFormat.escapeAttribute(mountId('connection_id'))}"></div>
                <input type="hidden" name="connection_id" value="${BossModFormat.escapeAttribute(start.connectionId)}">
            </div>
            <hr class="form-rule">
            <div class="connection-grid">
                ${FIELDS.THINKING_MODES.map((mode) => `<div class="field">
                    <span class="field-label field-label-sm">${BossModFormat.escapeHtml(mode.label)} thinking</span>
                    <div id="${BossModFormat.escapeAttribute(mountId(mode.key))}"></div>
                    <input type="hidden" name="${BossModFormat.escapeAttribute(mode.key)}"
                           value="${BossModFormat.escapeAttribute(start.thinking[mode.key])}">
                    <p class="field-hint">${BossModFormat.escapeHtml(mode.hint)}</p>
                </div>`).join('')}
            </div>
            ${notes}
        </section>`;
    }

    return {
        AI_QUESTION, PICKER, UNAVAILABLE, shapeFor, aiQuestion, mountId,
        connectionOptions, thinkingOptions, startingValues, connectionsSection,
    };
})();
