/**
 * BossMod AI — the create form, rearranged for a template.
 *
 * A template fills specialty, description and what-done. What it CANNOT fill
 * is a name and an AI connection: a pre-filled name is accepted by accident
 * and three agents from one template end up sharing it, and every connection
 * select defaults to `None`, so an agent created without one would fail on its
 * first turn. Those two are therefore the only fields this leaves in front of
 * the operator; everything the template already answered moves behind one
 * "Review & customise" disclosure.
 *
 * It REARRANGES the built form rather than building a second one. The
 * connection question and its two shapes — a select to answer, or the message
 * saying there is nothing to answer it with — are
 * context/agent-quick-connection.js's, and so is the rule keeping that field
 * required until the question has an answer. This decides where the field
 * goes. The disclosure below it relaxes nothing: it is where the template's
 * own answers are read, and treating a read as an answer is what let a quick
 * create write five null connections over "Saved successfully".
 *
 * Built with BossModDom.h. A template's title, author and specialty are
 * pack-authored remote text; the markup exemption the form's field groups
 * carry is theirs and does not extend here.
 */
const BossModAgentFormQuick = (() => {
    const { h } = BossModDom;
    const HYDRATE = BossModAgentFormHydrate;
    // The second of the two questions this layout leaves in front of the
    // operator, and the only one with a rule of its own to enforce:
    // context/agent-quick-connection.js.
    const { liftConnection } = BossModAgentQuickConnection;

    const COPY = Object.freeze({
        mark: '◆',
        pinned: 'pinned',
        clear: 'Remove template',
        review: 'Review & customise',
        tools: 'Tools',
    });

    /** The pinned commit, short enough to read on one chip line. */
    function shortSha(sha) {
        return String(sha || '').replace(/[^0-9a-f]/gi, '').slice(0, 7);
    }

    /**
     * The form fields one installed template answers.
     *
     * The template row's names and the form's are not the same names, and this
     * is the one place that knows both. Name, colour and the five connection
     * selects are absent on purpose: they are the operator's, and a template
     * that could write them would overwrite a draft.
     *
     * @param {object} template  An `AgentTemplate` row.
     * @returns {{role: string, description: string, done_fail_bar: string,
     *   personality_hint: string|null}} In `applyHireFields`' shape.
     */
    function templateFields(template) {
        return {
            role: template.specialty || '',
            description: template.description || '',
            done_fail_bar: template.what_done_looks_like || '',
            personality_hint: template.personality_hint || null,
        };
    }

    /**
     * The same mapping, emptied: what `applyHireFields` writes to UNDO a
     * template. DERIVED, because a hand-written clear goes stale the moment
     * the mapping gains a field — which is what happened to the personality
     * hint, leaving it selected after Remove template.
     *
     * @returns {object} Every field `templateFields` can name, empty.
     */
    function clearedFields() {
        return templateFields({});
    }

    /** `<specialty> · <clause> · Tools: <hints> · <colour name>`. The tool
     *  hints are part of the decision being made HERE, at the moment the agent
     *  is created — not a surface away in the marketplace's detail pane. */
    function summaryText(formRoot, template) {
        const clause = String(template.description || '').split(/[.;\n]/)[0].trim();
        const tools = Array.isArray(template.tools_hint) ? template.tools_hint : [];
        return [
            String(template.specialty || '').trim(),
            clause.length > 80 ? `${clause.slice(0, 80)}…` : clause,
            tools.length ? `${COPY.tools}: ${tools.join(', ')}` : '',
            colourName(formRoot),
        ].filter(Boolean).join(' · ');
    }

    /** The name of the swatch the form pre-checked, or '' if none is checked. */
    function colourName(formRoot) {
        const swatches = Array.from(formRoot.querySelectorAll('input[name="agent-color"]'));
        const checked = swatches.filter((radio) => radio.checked)[0];
        if (!checked) return '';
        const value = checked.getAttribute('value') || checked.value || '';
        const match = BossModAgentFields.AGENT_COLORS
            .filter((colour) => colour.value === value)[0];
        return match ? match.name : '';
    }

    /**
     * Rearrange an already-built create form into its quick state.
     *
     * @param {HTMLElement} formRoot  The host `renderInline` filled.
     * @param {object} template  The `AgentTemplate` the form was hydrated from.
     * @returns {void}
     * @throws {Error} When the form or its role-contract card is missing.
     *   Both are built unconditionally by agent-form.js, so their absence
     *   means the form is not the form this was written against — and skipping
     *   the rearrangement would ship a dialog whose required connection select
     *   the operator cannot see.
     */
    function applyQuickLayout(formRoot, template) {
        const form = formRoot.querySelector('#agent-form');
        if (!form) throw new Error('[quick] no #agent-form to rearrange');
        const card = formRoot.querySelector('#role-contract-card');
        if (!card) throw new Error('[quick] the form has no #role-contract-card');

        const nameInput = formRoot.querySelector('input[name="name"]');
        const wasPlaceholder = nameInput ? nameInput.getAttribute('placeholder') : null;
        if (nameInput && template.title) {
            nameInput.setAttribute('placeholder', `e.g. ${template.title}`);
        }

        // The FORM, not the host: `formRoot` is what the form was published
        // into, and everything the lift moves — and everything the guard it
        // arms watches — lives inside the form itself.
        const lifted = liftConnection(form);

        // Read BEFORE the sweep below: the colour the summary names lives in
        // the role-contract card, and a moment later that card is sitting in a
        // detached div where no query from the form can reach it.
        const line = summaryText(formRoot, template);
        const summary = line ? h('p', { class: 'quick-summary' }, line) : null;

        // Everything from the role contract down is what the template
        // answered. The feedback line is NOT: a failed save must never be
        // reported from inside a collapsed disclosure, so it is pulled out of
        // the sweep and put back as the form's last child.
        const children = Array.from(form.children);
        const at = children.indexOf(card);
        if (at === -1) throw new Error('[quick] #role-contract-card is not the form\'s own child');
        const feedback = children.filter((node) => node.id === 'agent-save-feedback')[0] || null;
        const content = h('div', { class: 'quick-disclosure-content' });
        children.slice(at).forEach((node) => {
            if (node !== feedback) content.append(node);
        });

        const details = h('details', { class: 'quick-disclosure' },
            h('summary', { class: 'quick-disclosure-summary' }, COPY.review),
            content);
        // NO listener on the toggle, deliberately. This panel is where the
        // specialty, description and what-done the template filled in are
        // read, so "expanded" used to be taken as "the operator is setting the
        // matrix themselves" and dropped `required` from the lifted select —
        // one way, and never restored. Opening it to read, which is the
        // invited interaction, therefore disarmed the connection guard, and
        // Create then wrote five null connections and reported success. What
        // relaxes the guard is an ANSWER in the matrix, and
        // context/agent-quick-connection.js watches the five selects for one.

        const chip = provenanceChip(template, () => {
            // Drop to blank: every field the template wrote is cleared, the
            // chip and its summary go, the name placeholder returns to the
            // form's own, and the disclosure opens because there is nothing
            // left behind it worth hiding. The guard is RETIRED with the rest
            // of the quick layout — the matrix is in front of the operator now
            // and the full form's own semantics, which allow a null per
            // activation type, take over. The unconfigured shape carries no
            // guard to retire: its block is the absence of any connection at
            // all, not the template, and the notice beside it still says so.
            HYDRATE.applyHireFields(formRoot, clearedFields());
            chip.remove();
            if (summary) summary.remove();
            if (nameInput && wasPlaceholder !== null) {
                nameInput.setAttribute('placeholder', wasPlaceholder);
            }
            details.setAttribute('open', '');
            if (lifted.guard) lifted.guard.release();
        });

        form.append(lifted.field);
        if (summary) form.append(summary);
        form.append(details);
        if (feedback) form.append(feedback);
        form.prepend(chip);
    }

    /**
     * `◆ <title> · <author> · pinned <sha>`, with the control that undoes it.
     *
     * @param {object} template
     * @param {() => void} onClear
     * @returns {HTMLElement}
     */
    function provenanceChip(template, onClear) {
        const parts = [template.title || ''];
        if (template.author_name) parts.push(template.author_name);
        const sha = shortSha(template.commit_sha);
        if (sha) parts.push(`${COPY.pinned} ${sha}`);
        return h('p', { class: 'quick-provenance', id: 'quick-provenance' },
            h('span', { class: 'quick-provenance-mark', 'aria-hidden': 'true' }, COPY.mark),
            h('span', { class: 'quick-provenance-text' }, parts.filter(Boolean).join(' · ')),
            h('button', {
                class: 'quick-provenance-clear', type: 'button',
                id: 'quick-provenance-clear', 'aria-label': COPY.clear, title: COPY.clear,
                onclick: () => onClear(),
            }, '×'));
    }

    return { COPY, shortSha, templateFields, applyQuickLayout };
})();
