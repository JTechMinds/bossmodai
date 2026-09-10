/**
 * BossMod AI — write an installed template's fields onto the create form.
 *
 * One function, and it is the module's whole job. The pack-import half went
 * with the catalog door: browsing, importing and the trust prompt are the
 * marketplace's now, and what reaches the form is a row from the local
 * template library — never an import response, a ref or a URL.
 *
 * The fields a template may fill are Specialty, Description, What-done and the
 * personality hint. NAME, COLOUR and the AI connections are never written:
 * they are the operator's answers, and a template that could overwrite them
 * would discard a draft the operator had already started.
 */
const BossModAgentFormHydrate = (() => {

    /**
     * Fill specialty / description / what-done, and match the personality by
     * its visible name when the template names one.
     *
     * Also the way a template is UNDONE: called with the empty shape it clears
     * exactly the fields a template can write — ALL FOUR of them, which is why
     * the provenance chip's dismissal needs nothing of its own. The personality
     * is therefore written on every call, not only when a hint is given: a
     * field that is only ever set is a field no dismissal can clear, and
     * "Remove template" used to leave the template's personality selected on an
     * otherwise emptied form.
     *
     * A hint no configured personality answers to leaves the form's own "No
     * personality" rather than a half-applied template, and says so on the
     * console: the operator is choosing from what Settings holds, and silently
     * keeping a stale selection would be a template half-written.
     *
     * @param {HTMLElement} formRoot
     * @param {{role?: string, description?: string, done_fail_bar?: string,
     *   personality_hint?: string|null}} fields
     * @returns {void}
     */
    function applyHireFields(formRoot, fields) {
        const role = formRoot.querySelector('input[name="role"]');
        const description = formRoot.querySelector('textarea[name="description"]');
        const done = formRoot.querySelector('[name="done_fail_bar"]');
        if (role) role.value = fields.role || '';
        if (description) description.value = fields.description || '';
        if (done) done.value = fields.done_fail_bar || '';
        const hint = fields.personality_hint;
        // Absent entirely when Settings holds no personality — the form renders
        // its own link to Settings there instead of a dropdown.
        const personality = formRoot.querySelector('select[name="personality_id"]');
        if (!personality) return;
        const options = personality.options
            ? Array.from(personality.options)
            : Array.from(personality.children || []).filter((node) => node.tagName === 'OPTION');
        const match = hint ? options.find((option) => (
            String(option.textContent || '').trim() === String(hint).trim()
        )) : null;
        if (hint && !match) {
            console.warn('[hydrate] no personality is configured under the name the template gives:', hint);
        }
        personality.value = match ? (match.value || match.getAttribute('value') || '') : '';
    }

    return { applyHireFields };
})();
