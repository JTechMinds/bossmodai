/**
 * BossMod AI — the assign sheet's result panels.
 *
 * The create-task route answers with one of five outcomes, two of which are
 * refusals that need the operator to choose. Turning that response into a panel
 * is pure rendering given callbacks, so it lives apart from the form that
 * submits it — and an outcome this build does not recognise is shown as itself
 * rather than being treated as a quiet success.
 */
const BossModAssignOutcomes = (() => {
    const { h } = BossModDom;

    /**
     * A titled panel in one of the four tones.
     *
     * @param {string} tone  'ok' | 'info' | 'warn' | 'error'.
     * @param {string} title
     * @param {...(Node|null)} children
     * @returns {HTMLElement}
     */
    function panel(tone, title, ...children) {
        return h('div', { class: 'assign-panel', 'data-tone': tone },
            h('p', { class: 'assign-panel-title' }, title), ...children);
    }

    function line(text) {
        return h('p', { class: 'assign-panel-body' }, text);
    }

    function mismatchPanel(body, handlers) {
        const reason = body.reason || body.specialty_warning
            || 'That assignee specialty does not match this work.';
        const suggested = Array.isArray(body.suggested_assignees) ? body.suggested_assignees : [];
        const box = panel('warn', 'Specialty mismatch — no new task was created',
            line(`${reason} Pick a matching teammate, or assign anyway.`));
        suggested.forEach((agent) => {
            box.append(h('button', {
                class: 'assign-pick', type: 'button',
                onclick: () => handlers.onPickAssignee(agent.id),
            }, `${agent.name || 'Teammate'} — ${agent.role || 'No specialty'}`));
        });
        if (suggested.length === 0) {
            box.append(line('No matching specialty is on the roster. Confirm only if this '
                + 'mismatch is intentional.'));
        }
        box.append(h('button', {
            class: 'assign-pick', type: 'button', onclick: handlers.onAssignAnyway,
        }, 'Assign anyway'));
        return box;
    }

    function clarifyPanel(body, handlers) {
        const candidates = Array.isArray(body.candidates) ? body.candidates : [];
        const box = panel('warn', 'Need a clarification — no new task was created',
            line(`${body.reason || 'Multiple open tasks match this title.'} Pick one to reuse, `
                + 'or change the title to create a distinct workstream.'));
        candidates.forEach((candidate) => {
            const row = h('div', { class: 'assign-candidate' },
                h('span', {}, `${candidate.title || 'Untitled'} · `
                    + `${candidate.assigned_to_name || 'Unassigned'} · ${candidate.status || ''}`),
                h('button', {
                    class: 'assign-pick', type: 'button',
                    onclick: () => handlers.onReuse(candidate.id),
                }, 'Reuse this'));
            // Rendered only when the caller can actually open a task. A control
            // that appears and does nothing is worse than one that is absent.
            if (handlers.onOpenTask) {
                row.append(h('button', {
                    class: 'assign-pick', type: 'button',
                    onclick: () => handlers.onOpenTask(candidate.id),
                }, 'View'));
            }
            box.append(row);
        });
        if (candidates.length === 0) {
            box.append(line('No candidate IDs were returned. Change the title to create a new '
                + 'workstream.'));
        }
        return box;
    }

    /**
     * Render one outcome.
     *
     * @param {object} body  The create-task response.
     * @param {object} handlers
     * @param {(agentId: string) => void} handlers.onPickAssignee
     * @param {() => void} handlers.onAssignAnyway
     * @param {(taskId: string) => void} handlers.onReuse
     * @param {(taskId: string) => void} [handlers.onOpenTask]  Optional.
     * @returns {HTMLElement}
     */
    function renderOutcome(body, handlers) {
        const task = body.task || {};
        if (body.outcome === 'create_new_task') {
            return panel('ok', 'Created', line(`${task.title || 'The task'} was added`
                + `${task.assigned_to ? ' and the assignee was notified.'
                    : ' to the unassigned backlog.'}`));
        }
        if (body.outcome === 'bind_existing_task') {
            return panel('info', 'Reused an open task',
                line(`${task.title || 'That workstream'} already exists, so nothing was duplicated.`));
        }
        if (body.outcome === 'specialty_mismatch') return mismatchPanel(body, handlers);
        if (body.outcome === 'clarify_ambiguous_match') return clarifyPanel(body, handlers);
        return panel('error', 'Unexpected outcome', line(String(body.outcome || 'unknown')));
    }

    /**
     * A standalone notice, for failures that never reached an outcome.
     *
     * @param {string} tone
     * @param {string} title
     * @param {string} [detail]
     * @returns {HTMLElement}
     */
    function renderNotice(tone, title, detail) {
        return panel(tone, title, detail ? line(detail) : null);
    }

    return { renderOutcome, renderNotice };
})();
