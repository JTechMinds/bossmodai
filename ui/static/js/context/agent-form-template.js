/**
 * BossMod AI — what an installed template adds to the create form.
 *
 * Renamed and reduced from agent-form-quick.js, which built a SECOND LAYOUT.
 * That module swept everything from the role contract down into a collapsed
 * "Review & customise" disclosure and lifted one connection select out of the
 * matrix, so the template path and the blank path were two different forms —
 * different fields, different order, different controls — over one submit
 * handler. The two things it hid were the colour swatches and the whole AI
 * Connections matrix, which are the two an operator most needs to see before
 * creating an agent, and the disclosure it hid them behind is where the guard
 * that stopped a connectionless create was armed and disarmed.
 *
 * WHAT IS LEFT IS ADDITIVE, and that is the whole design. The form is the same
 * form either way; a template fills four of its fields, says on the form where
 * those values came from, and offers the way back out. Nothing is moved,
 * nothing is hidden, and no rule is relaxed:
 *
 *   the fields    `templateFields` -> context/agent-form-hydrate.js
 *   the source    the provenance chip, and the tools line under it
 *   the way out   `Remove template`, which clears exactly what was written
 *
 * The connection guard is not this module's business any more, in either
 * direction. It sits on the visible matrix
 * (context/agent-form-bindings.js#bindConnectionGuard) and tracks the ANSWER,
 * so removing a template does not release it — an agent with no connection
 * fails on its first turn whether or not a template was involved.
 *
 * Built with BossModDom.h. A template's title, author and specialty are
 * pack-authored remote text; the markup exemption the form's field groups carry
 * is theirs and does not extend here.
 */
const BossModAgentFormTemplate = (() => {
    const { h } = BossModDom;
    const HYDRATE = BossModAgentFormHydrate;

    const COPY = Object.freeze({
        mark: '◆',
        pinned: 'pinned',
        clear: 'Remove template',
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
     * template. DERIVED, because a hand-written clear goes stale the moment the
     * mapping gains a field — which is what happened to the personality hint,
     * leaving it selected after Remove template.
     *
     * @returns {object} Every field `templateFields` can name, empty.
     */
    function clearedFields() {
        return templateFields({});
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
        return h('p', { class: 'template-chip', id: 'quick-provenance' },
            h('span', { class: 'template-chip-mark', 'aria-hidden': 'true' }, COPY.mark),
            h('span', { class: 'template-chip-text' }, parts.filter(Boolean).join(' · ')),
            h('button', {
                class: 'template-chip-clear', type: 'button',
                id: 'quick-provenance-clear', 'aria-label': COPY.clear, title: COPY.clear,
                onclick: () => onClear(),
            }, '×'));
    }

    /**
     * `Tools: gh, rg` — the one template fact with no field to live in.
     *
     * Part of the decision being made HERE, at the moment the agent is created,
     * rather than a surface away in the marketplace's detail pane. Everything
     * else the old summary line carried — the specialty, the first clause of
     * the description, the colour — is now a visible field on the form, so
     * printing it again above those fields would be the form saying the same
     * thing twice.
     *
     * @param {object} template
     * @returns {HTMLElement|null} null when the pack lists no tools, so an
     *   empty line is never drawn.
     */
    function toolsLine(template) {
        const tools = Array.isArray(template.tools_hint) ? template.tools_hint : [];
        if (!tools.length) return null;
        return h('p', { class: 'template-tools', id: 'template-tools' },
            `${COPY.tools}: ${tools.join(', ')}`);
    }

    /**
     * Mark an already-built create form as filled from a template.
     *
     * ADDITIVE ONLY: it prepends the chip and the tools line and points the
     * name placeholder at the template's title. It moves no field, hides no
     * section and changes no control's validity — which is the difference
     * between this and the `applyQuickLayout` it replaces.
     *
     * @param {HTMLElement} formRoot  The host `renderInline` filled.
     * @param {object} template  The `AgentTemplate` the form was hydrated from.
     * @returns {void}
     * @throws {Error} When the form is missing. It is built unconditionally by
     *   agent-form.js, so its absence means this is not the form this was
     *   written against, and marking a form that is not there would leave the
     *   operator creating an agent from a template with nothing on screen
     *   saying so.
     */
    function applyTemplate(formRoot, template) {
        const form = formRoot.querySelector('#agent-form');
        if (!form) throw new Error('[template] no #agent-form to mark');

        const nameInput = formRoot.querySelector('input[name="name"]');
        const wasPlaceholder = nameInput ? nameInput.getAttribute('placeholder') : null;
        if (nameInput && template.title) {
            nameInput.setAttribute('placeholder', `e.g. ${template.title}`);
        }

        // The template has just written a structured document into a box sized
        // for a sentence. Values assigned from script fire no `input`, so the
        // binding that sizes it has to be told — and told again when the chip's
        // dismissal empties the field.
        BossModAgentFormBindings.growDescription(formRoot);

        const tools = toolsLine(template);
        const chip = provenanceChip(template, () => {
            // Drop to blank: every field the template wrote is cleared, the
            // chip and its tools line go, and the name placeholder returns to
            // the form's own. Nothing else has to be undone, because nothing
            // else was done — the form the operator is looking at is already
            // the blank one's layout.
            HYDRATE.applyHireFields(formRoot, clearedFields());
            BossModAgentFormBindings.growDescription(formRoot);
            chip.remove();
            if (tools) tools.remove();
            if (nameInput && wasPlaceholder !== null) {
                nameInput.setAttribute('placeholder', wasPlaceholder);
            }
        });

        // Prepended in reverse, so the chip ends up above its own tools line.
        if (tools) form.prepend(tools);
        form.prepend(chip);
    }

    return { COPY, shortSha, templateFields, applyTemplate };
})();
