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
    return {
        bindDuplicateNameWarning,
        bindRuntimeCorePreview,
        bindFinishLineSuggestion,
    };
})();
