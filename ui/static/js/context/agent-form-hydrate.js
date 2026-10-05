/**
 * BossMod AI — write an installed template's fields onto the create form.
 *
 * One function, and it is the module's whole job. The pack-import half went
 * with the catalog door: browsing, importing and the trust prompt are the
 * marketplace's now, and what reaches the form is a row from the local
 * template library — never an import response, a ref or a URL.
 *
 * The fields a template may fill are Specialty, Description, What-done and the
 * four communication enums. NAME, COLOUR and the AI connections are never written:
 * they are the operator's answers, and a template that could overwrite them
 * would discard a draft the operator had already started.
 */
const BossModAgentFormHydrate = (() => {

    /**
     * Fill specialty / description / what-done and the communication enums.
     *
     * Also the way a template is UNDONE: called with the empty shape it clears
     * exactly the fields a template can write, which is why the provenance
     * chip's dismissal needs nothing of its own. Every field is written on
     * every call: a field that is only ever set is a field no dismissal can
     * clear.
     *
     * @param {HTMLElement} formRoot
     * @param {{role?: string, description?: string, done_fail_bar?: string,
     *   communication?: object|null}} fields
     * @returns {void}
     */
    function applyHireFields(formRoot, fields) {
        const role = formRoot.querySelector('input[name="role"]');
        const description = formRoot.querySelector('textarea[name="description"]');
        const done = formRoot.querySelector('[name="done_fail_bar"]');
        if (role) role.value = fields.role || '';
        if (description) description.value = fields.description || '';
        if (done) done.value = fields.done_fail_bar || '';
        // The communication controls are BossModMenuSelects
        // (context/agent-form-choices.js): found by their form value's name,
        // written through the control so the trigger shows what is saved.
        const comm = BossModCommunication.resolve(fields.communication, fields.role || '');
        BossModCommunication.KEYS.forEach((key) => {
            const node = formRoot.querySelector(`[name="communication_${key}"]`);
            if (node) BossModMenuSelect.instanceFor(node).setValue(comm[key]);
        });
    }

    return { applyHireFields };
})();
