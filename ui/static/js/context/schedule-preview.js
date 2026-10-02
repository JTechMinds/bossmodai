/**
 * BossMod AI — "Upcoming runs": the next few runs of the rule being edited.
 *
 * Mounted as the Next run fact's value by context/schedule-layer.js while a
 * schedule is created or edited (the fact's label names it, so it carries no
 * heading of its own), and fed the draft on every change. The
 * times come from the server (POST /api/schedules/preview), which computes
 * them with the same `next_occurrence` the runtime worker fires from, so the
 * preview cannot disagree with the real runs. Nothing is saved.
 *
 * Changes are debounced, and a load generation drops an answer for a draft
 * that has since changed. A draft the editor already knows is invalid shows
 * that sentence and sends nothing; the server's 422 sentence shows the same
 * way.
 */
const BossModSchedulePreview = (() => {
    const { h, clear } = BossModDom;

    /** Typing in a field fires a change per key; ask once it settles. */
    const DEBOUNCE_MS = 300;

    /**
     * Build the preview.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {number} deps.count  How many runs to list (the server allows 1..20).
     * @returns {{element: HTMLElement,
     *   update: (rule: object|null, clientError: string|null) => void,
     *   destroy: () => void}} `update` takes the draft rule, or null with the
     *   editor's own error sentence. `destroy` drops a pending or in-flight
     *   request.
     * @throws {Error} When api is missing or count is not a positive whole number.
     */
    function create(deps) {
        const { api, count } = deps || {};
        if (typeof api !== 'function') throw new Error('[schedule-preview] deps.api is required');
        if (!Number.isInteger(count) || count < 1) throw new Error('[schedule-preview] deps.count must be a positive integer');

        const load = BossModGates.createLoadGeneration();
        const body = h('div', { class: 'schedule-preview-body', role: 'status', 'aria-live': 'polite' });
        const element = h('section', { class: 'schedule-preview' }, body);
        let timer = null;
        let destroyed = false;

        /** Replace what the preview says. */
        function show(...nodes) {
            clear(body);
            body.append(...nodes);
        }

        async function ask(rule, loadId) {
            let answer;
            try {
                answer = await BossModScheduleApi.preview(api, rule, count);
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[schedule-preview] the preview was refused', err);
                show(h('p', { class: 'context-error schedule-preview-error' }, (err && err.message) || 'The preview failed.'));
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;
            show(
                h('p', { class: 'field-hint schedule-preview-summary' }, String(answer.summary)),
                h('ol', { class: 'schedule-preview-list' },
                    answer.next_runs.map((iso) => h('li', {}, BossModFormat.formatDateTime(iso)))));
        }

        /** See @returns. */
        function update(rule, clientError) {
            const loadId = load.next();
            clearTimeout(timer);
            if (!rule) {
                if (!clientError) throw new Error('[schedule-preview] update needs a rule or the reason there is none');
                show(h('p', { class: 'context-error schedule-preview-error' }, String(clientError)));
                return;
            }
            show(h('p', { class: 'context-skeleton' }, 'Working out the next runs…'));
            timer = setTimeout(() => { void ask(rule, loadId); }, DEBOUNCE_MS);
        }

        return {
            element,
            update,
            destroy() {
                destroyed = true;
                load.next();
                clearTimeout(timer);
            },
        };
    }

    return { create };
})();
