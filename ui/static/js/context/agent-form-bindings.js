/**
 * BossMod AI — the three behaviours bound to fields inside the agent form.
 *
 * Split out of agent-panel.js. Each takes the rendered container and wires one
 * field pair; none of them renders anything, which is why they are separable
 * from the markup at all.
 *
 * Every one returns early when its field is absent rather than throwing: the
 * Advanced disclosure and the recovery tools are conditional markup, and a
 * binding for a field that is legitimately not on this form is not an error.
 */
const BossModAgentFormBindings = (() => {

    /**
     * Warn when the typed name already belongs to someone.
     *
     * Advisory only — duplicate names are allowed, and the copy says so. The
     * warning is recomputed on every keystroke AND once immediately, so
     * editing an agent whose name already clashes shows it before any typing.
     *
     * @param {HTMLElement} container
     * @param {object[]} roster
     * @param {object|null} agent  The agent being EDITED, whose own name is
     *   not a clash — never a snapshot being recreated, which is a new agent
     *   and clashes with the one it was taken from like any other.
     * @returns {void}
     */
    function bindDuplicateNameWarning(container, roster, agent) {
        const nameInput = container.querySelector('input[name="name"]');
        const warnEl = container.querySelector('#agent-name-duplicate-warn');
        if (!nameInput || !warnEl) return;

        function refresh() {
            const typed = String(nameInput.value || '').trim().toLowerCase();
            const clash = Boolean(
                typed
                && (roster || []).some((item) => (
                    item
                    && item.id !== agent?.id
                    && String(item.name || '').trim().toLowerCase() === typed
                ))
            );
            warnEl.classList.toggle('hidden', !clash);
        }

        nameInput.addEventListener('input', refresh);
        refresh();
    }

    /**
     * Keep the runtime-core preview in step with name, specialty, and desk.
     *
     * The preview is what the agent will actually be told every turn, so it is
     * rendered by the server rather than guessed here.
     *
     * @param {HTMLElement} container
     * @param {object|null} values  What the form was built from — the agent
     *   being edited or the snapshot being recreated. Only its stored desk is
     *   read, and only when the form has no desk control.
     * @returns {void}
     */
    function bindRuntimeCorePreview(container, values) {
        const preview = container.querySelector('#runtime-core-preview');
        if (!preview) return;
        const nameInput = container.querySelector('input[name="name"]');
        const roleInput = container.querySelector('input[name="role"]');
        // The desk dropdown's form value (core/menu-select.js `name`): a
        // pick fires `change` on it, as a native select did.
        const deskSelect = container.querySelector('input[name="desk"]');

        async function refresh() {
            const params = new URLSearchParams();
            if (nameInput?.value) params.set('name', nameInput.value);
            if (roleInput?.value) params.set('role', roleInput.value);
            const deskValue = deskSelect?.value || '';
            if (deskValue) {
                const [x, y] = deskValue.split(',');
                if (x) params.set('desk_x', x);
                if (y) params.set('desk_y', y);
            } else if (values?.desk_x != null && values?.desk_y != null && !deskSelect) {
                params.set('desk_x', String(values.desk_x));
                params.set('desk_y', String(values.desk_y));
            }
            try {
                const res = await apiFetch(`/api/runtime/core?${params.toString()}`);
                if (!res.ok) return;
                const data = await res.json();
                if (data?.runtime_core) preview.textContent = data.runtime_core;
            } catch {
                // Keep the last preview if the request fails.
            }
        }

        nameInput?.addEventListener('input', refresh);
        roleInput?.addEventListener('input', refresh);
        deskSelect?.addEventListener('change', refresh);
        void refresh();
    }

    /**
     * Offer a done/fail bar derived from the specialty, without overwriting
     * one the operator wrote.
     *
     * A suggestion replaces the field only when it is empty or still holds the
     * previous suggestion; the Suggest button forces it. Typing a bar and
     * having it silently rewritten on the next keystroke would be worse than
     * offering nothing.
     *
     * @param {HTMLElement} container
     * @param {object|null} values  What the form was built from — the agent
     *   being edited or the snapshot being recreated; null for a blank form.
     * @returns {void}
     */
    function bindFinishLineSuggestion(container, values) {
        const specialtyInput = container.querySelector('input[name="role"]');
        const descriptionInput = container.querySelector('textarea[name="description"]');
        const finishLineInput = container.querySelector('textarea[name="done_fail_bar"]');
        const suggestBtn = container.querySelector('#btn-suggest-finish-line');
        if (!specialtyInput || !descriptionInput || !finishLineInput) return;

        let lastSuggested = values
            ? BossModSpecialty.suggestFinishLine(values.role || '', values.description || '')
            : '';

        function currentSuggestion() {
            return BossModSpecialty.suggestFinishLine(specialtyInput.value, descriptionInput.value);
        }

        function applySuggestion({ force = false } = {}) {
            const suggested = currentSuggestion();
            const current = finishLineInput.value.trim();
            const canReplace = force
                || !current
                || current === lastSuggested;
            if (canReplace) {
                finishLineInput.value = suggested;
                // A scripted value fires no `input`, so the box is resized here.
                growHireText(container);
            }
            lastSuggested = suggested;
        }

        specialtyInput.addEventListener('input', () => applySuggestion());
        descriptionInput.addEventListener('input', () => applySuggestion());
        if (suggestBtn) {
            suggestBtn.addEventListener('click', () => applySuggestion({ force: true }));
        }
        if (values && !(values.done_fail_bar || '').trim() && (values.role || values.description)) {
            applySuggestion();
        }
    }

    /**
     * Keep the four communication enums on the specialty default until the
     * operator changes one. A template hydrate writes the pack block first;
     * this only replaces values that still match the previous default.
     *
     * The four are context/agent-form-choices.js's dropdowns, found by their
     * form value's name and written through the control
     * (BossModMenuSelect.instanceFor), never through the input.
     *
     * @param {HTMLElement} container
     * @param {object|null} values  What the form was built from — the agent
     *   being edited or the snapshot being recreated; null for a blank form.
     * @returns {void}
     */
    function bindCommunicationDefaults(container, values) {
        const specialtyInput = container.querySelector('input[name="role"]');
        const inputs = BossModCommunication.KEYS.map((key) => (
            container.querySelector(`[name="communication_${key}"]`)
        ));
        if (!specialtyInput || inputs.some((node) => !node)) return;
        const selects = inputs.map((node) => BossModMenuSelect.instanceFor(node));

        let lastDefault = BossModCommunication.defaultFor(
            values ? (values.role || '') : specialtyInput.value,
        );

        function applyDefaults() {
            const next = BossModCommunication.defaultFor(specialtyInput.value);
            BossModCommunication.KEYS.forEach((key, index) => {
                const select = selects[index];
                if (select.getValue() === lastDefault[key]) {
                    select.setValue(next[key]);
                }
            });
            lastDefault = next;
        }

        specialtyInput.addEventListener('input', applyDefaults);
    }
    /**
     * Mount the AI Connection section's three dropdowns and keep them in step.
     *
     * One BossModMenuSelect for the connection, one per routed activation for
     * its thinking level, each carrying its own form value
     * (core/menu-select.js `name`: `connection_id` and the THINKING_MODES
     * keys) — what context/agent-submit.js reads. Picking another
     * connection re-lists each thinking control with what THAT connection
     * offers; a level it does not offer is reset to Server default in the
     * control itself, so the operator sees the change before saving it.
     *
     * @param {HTMLElement} form  The `<form>` itself, never the host it was
     *   published into (see the header of context/agent-form.js).
     * @param {object[]} connections  The list the section was rendered from.
     * @param {object|null} values  What the form was built from — the agent
     *   being edited or the snapshot being recreated; null for a blank form.
     *   Read through `startingValues`, the same reading the markup used.
     * @returns {void} Returns early when `connections` renders the
     *   unavailable shape (BossModAgentFormConnections.shapeFor): with no
     *   connection configured the section is a link to Settings.
     * @throws {Error} When the picker shape is missing a mount point. They are
     *   rendered by the same branch, so an absent one means the save would
     *   read no value at all.
     */
    function bindAiConnection(form, connections, values) {
        const CONNECTIONS = BossModAgentFormConnections;
        if (CONNECTIONS.shapeFor(connections) !== CONNECTIONS.PICKER) return;
        const start = CONNECTIONS.startingValues(values, connections);
        const byId = new Map(connections.map((c) => [c.id, c]));
        const mount = (key, select) => {
            const point = form.querySelector(`#${CONNECTIONS.mountId(key)}`);
            if (!point) throw new Error(`[agent-form-bindings] the AI section has no ${key} mount`);
            point.append(select.element);
            BossModIcons.paint(select.element, 'agent-form.ai');
        };

        const thinking = BossModAgentFields.THINKING_MODES.map((mode) => {
            const select = BossModMenuSelect.create({
                label: `${mode.label} thinking`,
                name: mode.key,
                options: CONNECTIONS.thinkingOptions(byId.get(start.connectionId) || null),
                value: start.thinking[mode.key],
                variant: 'field',
            });
            mount(mode.key, select);
            return select;
        });

        const connection = BossModMenuSelect.create({
            label: 'AI connection',
            name: 'connection_id',
            options: CONNECTIONS.connectionOptions(connections, start.connectionId),
            value: start.connectionId,
            variant: 'field',
            onChange: (id) => {
                // Once a connection is chosen, "Choose a connection" goes.
                connection.setOptions(CONNECTIONS.connectionOptions(connections, id), id);
                const options = CONNECTIONS.thinkingOptions(byId.get(id) || null);
                thinking.forEach((select) => {
                    const held = select.getValue();
                    const keep = options.some((option) => option.value === held) ? held : 'default';
                    select.setOptions(options, keep);
                });
            },
        });
        mount('connection_id', connection);
    }

    /**
     * Keep the colour swatches showing the initial the agent will actually
     * carry, as the operator types the name.
     *
     * The swatch IS the avatar — same classes, same derived tint/ink pair — so
     * it previews what every surface will render. What it cannot preview before
     * a name exists is the letter, and BossModAvatar answers a nameless agent
     * with `?`: correct on a roster row, where the circle still has to identify
     * someone, and wrong on eight swatches in a create form, where it painted
     * eight question marks and read as a control that had failed to load.
     *
     * @param {HTMLElement} container
     * @returns {void} Returns early when the form has no name field or no
     *   swatches — an edit dialog builds both, but the guard keeps this the
     *   same shape as the other bindings in this module.
     */
    function bindColorSwatchInitial(container) {
        const nameInput = container.querySelector('input[name="name"]');
        // Walked rather than asked for as `.color-choice .avatar`: one simple
        // selector per step, which is all the fake DOM the suite runs against
        // supports — and all this needs.
        const swatches = Array.from(container.querySelectorAll('.color-choice'))
            .map((choice) => choice.querySelector('.avatar'))
            .filter(Boolean);
        if (!nameInput || !swatches.length) return;
        const refresh = () => {
            const typed = String(nameInput.value || '').trim();
            const glyph = typed ? BossModAvatar.initial(typed) : '';
            swatches.forEach((swatch) => { swatch.textContent = glyph; });
        };
        nameInput.addEventListener('input', refresh);
        refresh();
    }

    /**
     * Size every auto-growing hire textarea (description, done bar) to what is
     * actually in it.
     *
     * A pack's description is a STRUCTURED DOCUMENT — mission, both scopes,
     * handoff — and it is saved verbatim because
     * core/agent_loop/role_contracts.py puts it in the role-contract block on
     * every turn. So it cannot be trimmed to fit; the box has to fit it. In a
     * fixed three-row field it arrived clipped mid-sentence with its own
     * scrollbar overlapping the hint underneath, which read as a broken control
     * rather than as a long value.
     *
     * The measuring is core/autogrow.js's (BossModAutoGrow.fit), shared with
     * every other growing field; the ceiling is CSS (`--field-grow-max` on
     * `.field-textarea[data-autogrow]`), so past it the box scrolls instead of
     * pushing the panel away.
     *
     * @param {HTMLElement} container
     * @returns {void} A node with no layout is skipped (BossModAutoGrow.fit):
     *   the suite's fake DOM has none, and a field inside the closed Advanced
     *   panel measures 0 until it is revealed and re-fitted here.
     */
    function growHireText(container) {
        container.querySelectorAll('textarea[data-autogrow]').forEach(BossModAutoGrow.fit);
    }

    /**
     * Keep the auto-growing hire textareas sized to their content as the
     * operator types (BossModAutoGrow.fit, core/autogrow.js).
     *
     * @param {HTMLElement} container
     * @returns {void}
     */
    function bindHireTextAutoGrow(container) {
        container.querySelectorAll('textarea[data-autogrow]').forEach((field) => {
            field.addEventListener('input', () => BossModAutoGrow.fit(field));
        });
        growHireText(container);
    }

    return {
        bindColorSwatchInitial,
        bindHireTextAutoGrow,
        growHireText,
        bindDuplicateNameWarning,
        bindRuntimeCorePreview,
        bindFinishLineSuggestion,
        bindCommunicationDefaults,
        bindAiConnection,
    };
})();
