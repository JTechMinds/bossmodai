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
     * @param {object|null} agent
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
     * @param {object|null} agent
     * @returns {void}
     */
    function bindRuntimeCorePreview(container, agent) {
        const preview = container.querySelector('#runtime-core-preview');
        if (!preview) return;
        const nameInput = container.querySelector('input[name="name"]');
        const roleInput = container.querySelector('input[name="role"]');
        const deskSelect = container.querySelector('select[name="desk"]');

        async function refresh() {
            const params = new URLSearchParams();
            if (nameInput?.value) params.set('name', nameInput.value);
            if (roleInput?.value) params.set('role', roleInput.value);
            const deskValue = deskSelect?.value || '';
            if (deskValue) {
                const [x, y] = deskValue.split(',');
                if (x) params.set('desk_x', x);
                if (y) params.set('desk_y', y);
            } else if (agent?.desk_x != null && agent?.desk_y != null && !deskSelect) {
                params.set('desk_x', String(agent.desk_x));
                params.set('desk_y', String(agent.desk_y));
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
     * @param {object|null} agent
     * @returns {void}
     */
    function bindFinishLineSuggestion(container, agent) {
        const specialtyInput = container.querySelector('input[name="role"]');
        const descriptionInput = container.querySelector('textarea[name="description"]');
        const finishLineInput = container.querySelector('input[name="done_fail_bar"]');
        const suggestBtn = container.querySelector('#btn-suggest-finish-line');
        if (!specialtyInput || !descriptionInput || !finishLineInput) return;

        let lastSuggested = agent
            ? BossModSpecialty.suggestFinishLine(agent.role || '', agent.description || '')
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
            }
            lastSuggested = suggested;
        }

        specialtyInput.addEventListener('input', () => applySuggestion());
        descriptionInput.addEventListener('input', () => applySuggestion());
        if (suggestBtn) {
            suggestBtn.addEventListener('click', () => applySuggestion({ force: true }));
        }
        if (agent && !(agent.done_fail_bar || '').trim() && (agent.role || agent.description)) {
            applySuggestion();
        }
    }
    /**
     * Refuse a CREATE that would leave every activation type at `None`.
     *
     * THE RULE, and the one place it is enforced before the submit: the "Set
     * All" select stays `required` until at least one of the five `model_*`
     * selects holds a value, and it comes back the moment they are all cleared
     * to None. Live, on every change, in both directions — not a one-way flip.
     *
     * An agent created with no connection on any activation type fails on its
     * first turn, and five different UI routes have produced one; each was
     * fixed at the control that exposed it and the next appeared. This is the
     * cheap gate — native constraint validation, which the pinned primary
     * reaches because it carries `form="agent-form"`. It is NOT the only one:
     * context/agent-form-save.js refuses the same thing on what would actually
     * be SENT, and that is the check that cannot be bypassed by a layout.
     *
     * It reads THE FIVE and never the "Set All" select's own value, because
     * those five are the keys context/agent-submit.js sends: the guard asks
     * exactly the question the save will ask. A "Set All" that stopped reaching
     * them — the defect that shipped once already — therefore leaves the guard
     * armed and the submit refused, instead of letting a control the operator
     * answered stand in for an agent that has no connection at all.
     *
     * The guard used to live on a select LIFTED out of the matrix by the
     * template layout, and it was armed and disarmed by that layout's
     * disclosure — so opening the panel to read what the template had filled
     * in disarmed it, and a create then wrote five nulls over "Saved
     * successfully". Nothing is lifted or hidden now, and nothing but an ANSWER
     * in the matrix moves it.
     *
     * @param {HTMLElement} form  The `<form>` itself, never the host it was
     *   published into: publication MOVES the form out of that host and leaves
     *   it empty, so a binding rooted on the host searches nothing for the rest
     *   of its life (see the header of context/agent-form.js).
     * @param {{creating: boolean}} options  CREATE ONLY, deliberately. An
     *   existing agent may already have no connection, and refusing that save
     *   would trap the operator in a dialog they cannot leave with their other
     *   edits — the same scope context/agent-form-save.js's refusal carries.
     * @returns {void} Returns early, and arms nothing, when Settings holds no
     *   connection at all: that form has no "Set All" and no five to answer, so
     *   there is no control a `required` could sit on. The operator is told why
     *   inline by the section's own link to Settings, the dialog withholds its
     *   primary (context/agent-form-save.js), and the save refuses. A stand-in
     *   `required` select that submitted to nothing used to stand here; it was
     *   a control the operator could not answer and that wrote no value.
     * @throws {Error} When the form has a "Set All" but is missing one of the
     *   five. They are built by the same branch, so an absent one means the
     *   guard cannot see what the save would read — and a guard that cannot see
     *   that is the hole it exists to close.
     */
    function bindConnectionGuard(form, options) {
        if (!options || !options.creating) return;
        const setAll = form.querySelector('select[name="model_all"]');
        if (!setAll) return;
        const matrix = BossModAgentFields.MODEL_TYPES.map((type) => {
            const select = form.querySelector(`select[name="${type.key}"]`);
            if (!select) {
                throw new Error(`[agent-form-bindings] the matrix has no ${type.key} select`);
            }
            return select;
        });
        const sync = () => {
            if (matrix.some((select) => select.value)) setAll.removeAttribute('required');
            else setAll.setAttribute('required', '');
        };
        // "Set All" is watched as well as the five, because the fan-out
        // agent-form.js bound to it writes their values from script — see the
        // note at that call site for why this must be registered after it.
        setAll.addEventListener('change', sync);
        matrix.forEach((select) => select.addEventListener('change', sync));
        sync();
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

    /** How tall the description may grow before it scrolls instead. Past
     *  roughly ten lines the field would push the matrix beside it off the
     *  panel, and a document that long is being read rather than written. */
    const DESCRIPTION_MAX_PX = 240;

    /**
     * Size the description box to what is actually in it.
     *
     * A pack's description is a STRUCTURED DOCUMENT — mission, both scopes,
     * handoff — and it is saved verbatim because
     * core/agent_loop/role_contracts.py puts it in the role-contract block on
     * every turn. So it cannot be trimmed to fit; the box has to fit it. In a
     * fixed three-row field it arrived clipped mid-sentence with its own
     * scrollbar overlapping the hint underneath, which read as a broken control
     * rather than as a long value.
     *
     * @param {HTMLElement} container
     * @returns {void} Does nothing where the node cannot be measured — the
     *   suite's fake DOM has no layout, and a binding that threw there would
     *   take the whole form down with it.
     */
    function growDescription(container) {
        const field = container.querySelector('textarea[name="description"]');
        if (!field || !field.style || typeof field.scrollHeight !== 'number') return;
        // Reset first: scrollHeight reports the CONTENT height only while the
        // box is not already tall enough to hold it, so a field that has been
        // grown once would otherwise never shrink back.
        field.style.height = 'auto';
        field.style.height = `${Math.min(field.scrollHeight, DESCRIPTION_MAX_PX)}px`;
        field.style.overflowY = field.scrollHeight > DESCRIPTION_MAX_PX ? 'auto' : 'hidden';
    }

    /**
     * Keep the description box sized to its content as the operator types.
     *
     * @param {HTMLElement} container
     * @returns {void}
     */
    function bindDescriptionAutoGrow(container) {
        const field = container.querySelector('textarea[name="description"]');
        if (!field) return;
        field.addEventListener('input', () => growDescription(container));
        growDescription(container);
    }

    return {
        bindColorSwatchInitial,
        bindDescriptionAutoGrow,
        growDescription,
        bindDuplicateNameWarning,
        bindRuntimeCorePreview,
        bindFinishLineSuggestion,
        bindConnectionGuard,
    };
})();
