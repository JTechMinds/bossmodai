/**
 * BossMod AI — the agent form's choice controls: desk and the four
 * communication enums.
 *
 * Split from context/agent-form-bindings.js, which binds text fields to each
 * other; these are dropdowns, a separate responsibility. They are
 * BossModMenuSelect's field look, never a native <select> (WebKitGTK paints
 * one as a GTK combo box that no stylesheet can match to the fields beside
 * it). context/agent-form-advanced.js renders an empty mount point per
 * control; `mount` builds the controls into them once the form exists.
 *
 * Each control carries its form value as a hidden `<input name>` (the form
 * contract in core/menu-select.js), so context/agent-submit.js keeps reading
 * `FormData`, and the bindings and the template hydrate reach a control
 * through `BossModMenuSelect.instanceFor` to write it.
 *
 * The option builders are pure and exported, so what a control offers and
 * which option it starts on have one owner.
 */
const BossModAgentFormChoices = (() => {
    const FIELDS = BossModAgentFields;
    const COMM = BossModCommunication;

    /** The desk control's "none" row. */
    const UNASSIGNED_DESK = Object.freeze({ value: '', label: 'Unassigned' });

    /**
     * The desk options, and which one the form starts on.
     *
     * @param {object|null} values  The agent being edited or the snapshot
     *   being recreated; null for a blank form.
     * @param {object[]} roster  Peers, for desk occupancy.
     * @returns {{options: Array<{value: string, label: string}>, value: string,
     *   noFreeDesk: boolean}} "Unassigned" first, then every desk, an occupied
     *   one marked " (taken)". The value is BossModAgentFields.deskChoice's
     *   pick, or "Unassigned" when there is none.
     */
    function deskOptions(values, roster) {
        const { selectedDesk, noFreeDesk, desks } = FIELDS.deskChoice(values, roster);
        const options = [
            UNASSIGNED_DESK,
            ...desks.map((desk) => ({
                value: desk.value,
                label: desk.taken ? `${desk.label} (taken)` : desk.label,
            })),
        ];
        const value = selectedDesk ? `${selectedDesk.x},${selectedDesk.y}` : UNASSIGNED_DESK.value;
        return { options, value, noFreeDesk };
    }

    /**
     * One communication enum's options, and which one the form starts on.
     *
     * @param {string} key  One of BossModCommunication.KEYS.
     * @param {object|null} values  As `deskOptions`; its
     *   `communication` block is resolved against its specialty, the way the
     *   save resolves it.
     * @returns {{options: Array<{value: string, label: string}>, value: string}}
     * @throws {Error} On a key the vocabulary does not hold.
     */
    function communicationOptions(key, values) {
        if (!COMM.KEYS.includes(key)) throw new Error(`[agent-form-choices] unknown communication key "${key}"`);
        const resolved = COMM.resolve(values?.communication, values?.role || '');
        return {
            options: COMM.ENUMS[key].map((token) => ({ value: token, label: token })),
            value: resolved[key],
        };
    }

    /**
     * Build the choice controls into the form's mount points.
     *
     * Called before the bindings that look these controls up
     * (`bindCommunicationDefaults`, `bindRuntimeCorePreview`) and before any
     * template hydrate writes them.
     *
     * @param {HTMLElement} form  The `<form>` itself (see context/agent-form.js).
     * @param {object} view
     * @param {object[]} view.roster
     * @param {object|null} view.values
     * @returns {void}
     * @throws {Error} When a mount point the markup should have rendered is
     *   missing: the save would read a value no control can change.
     */
    function mount(form, view) {
        const { roster, values } = view || {};
        if (!form) throw new Error('[agent-form-choices] mount needs the form');
        if (!Array.isArray(roster)) {
            throw new Error('[agent-form-choices] mount needs the roster list');
        }
        const place = (pointId, menu) => {
            const point = form.querySelector(`#${pointId}`);
            if (!point) throw new Error(`[agent-form-choices] the form has no #${pointId}`);
            point.append(menu.element);
            BossModIcons.paint(menu.element, 'agent-form.choices');
        };
        const build = (pointId, deps, { options, value }) => place(pointId, BossModMenuSelect.create({
            ...deps, options, value, variant: 'field',
        }));

        build('agent-desk-mount', {
            label: 'Desk assignment', name: 'desk', id: 'agent-desk',
        }, deskOptions(values, roster));
        COMM.KEYS.forEach((key) => {
            build(`agent-communication-${key}-mount`, {
                label: COMM.LABELS[key], name: `communication_${key}`, id: `agent-communication-${key}`,
            }, communicationOptions(key, values));
        });
    }

    return { deskOptions, communicationOptions, mount };
})();
