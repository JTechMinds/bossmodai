/**
 * BossMod AI — the task detail slide-over.
 *
 * Ported from company-task-detail.js, which was the right panel of the dock-era
 * table. Everything load-bearing is preserved verbatim: the role-contract
 * section and its copy, the deliverable cards with their original path and
 * agent id, the parent and subtask links, and the cancel button.
 *
 * What changed is who cancels. The old panel took a callback through a setter
 * and could be re-pointed at any time; this one is handed `onCancel` at
 * construction and never calls the cancel route itself, so there is exactly one
 * place in the Board that can end a task.
 */
const BossModTaskDetail = (() => {
    const { h } = BossModDom;
    const COLUMNS = BossModBoardColumns;
    const DELIVERABLES = BossModTaskDeliverables;

    const BLOCKED_COPY = 'Blocked — checkable claim missing';
    const NEEDED_COPY = 'What’s needed: tests evidence, an artifact path that exists, '
        + 'or an allow/deny proof. Empty done is rejected.';

    /**
     * Recover a done claim the agent wrote into its completion event.
     *
     * @param {object} task
     * @returns {object|null} null when the task carries no claim — a real
     *   answer for work that is not complete.
     */
    function resolveDoneClaim(task) {
        if (task.done_claim && typeof task.done_claim === 'object') return task.done_claim;
        const latest = task.latest_event;
        if (!latest || latest.event_type !== 'completion') return null;
        const marker = ' Claim: ';
        const markerAt = String(latest.content || '').indexOf(marker);
        if (markerAt < 0) return null;
        const tail = String(latest.content).slice(markerAt + marker.length).trim();
        if (!tail) return null;
        const parts = tail.split('—').map((part) => part.trim()).filter(Boolean);
        const type = (parts[0] || '').toLowerCase();
        const claim = { type: ['artifact', 'tests', 'proof'].includes(type) ? type : 'proof' };
        if (parts[1] && (claim.type === 'artifact' || parts[1].startsWith('/'))) claim.path = parts[1];
        else if (parts[1]) claim.evidence = parts[1];
        if (parts[2]) claim.evidence = parts[2];
        return claim;
    }

    function section(heading, ...children) {
        return h('section', { class: 'task-detail-section' },
            heading ? h('p', { class: 'task-detail-heading' }, heading) : null,
            ...children);
    }

    /** Specialty, what done looks like, and whether this task has cleared it. */
    function roleContract(task) {
        const specialty = task.assigned_to_role || '';
        const doneBar = task.assigned_to_done_fail_bar || '';
        const guidance = BossModSpecialty.doneClaimGuidance(task);
        const claimLabel = BossModSpecialty.formatDoneClaim(resolveDoneClaim(task));
        const showOpenGuidance = task.status !== 'complete' && Boolean(task.assigned_to);
        const showCompleteClaim = task.status === 'complete' && Boolean(claimLabel);
        if (!specialty && !doneBar && !showOpenGuidance && !showCompleteClaim) return null;

        return section('Role contract',
            specialty ? h('p', { class: 'task-detail-meta' }, `Specialty: ${specialty}`) : null,
            doneBar ? h('p', { class: 'task-detail-meta' }, `What done looks like: ${doneBar}`) : null,
            showOpenGuidance
                ? h('div', { class: 'task-detail-panel', 'data-tone': 'warn' },
                    h('p', { class: 'task-detail-heading' }, BLOCKED_COPY),
                    h('p', { class: 'task-detail-body' }, guidance),
                    h('p', { class: 'task-detail-body' }, NEEDED_COPY))
                : null,
            showCompleteClaim
                ? h('div', { class: 'task-detail-panel', 'data-tone': 'ok' },
                    h('p', { class: 'task-detail-heading' }, 'Done claim'),
                    h('p', { class: 'task-detail-body' },
                        claimLabel || 'Completed with a checkable claim.'))
                : null);
    }

    function people(task) {
        const lines = [];
        if (task.assigned_to_name || task.assigned_to) {
            lines.push(`Assigned: ${task.assigned_to_name || 'Unassigned'}`);
        }
        if (task.owner_name || task.owner_id) lines.push(`Owner: ${task.owner_name || 'Unknown'}`);
        if (task.requester_name || task.requester_id) {
            const name = task.requester_name === '__human__' ? 'You' : (task.requester_name || 'Unknown');
            lines.push(`Requester: ${name}`);
        }
        if (task.project) lines.push(`Project: ${task.project}`);
        if (task.cost_ceiling != null) lines.push(`Cost ceiling: ${task.cost_ceiling}`);
        if (task.created_at) lines.push(`Created: ${new Date(task.created_at).toLocaleString()}`);
        if (task.last_activity) {
            lines.push(`Updated: ${BossModFormat.formatRelativeTime(task.last_activity)}`);
        }
        return lines.map((line) => h('p', { class: 'task-detail-meta' }, line));
    }

    function narrative(task) {
        if (!task.description && !task.completion_summary && !task.status_note) return null;
        return section(null,
            task.description ? h('p', { class: 'task-detail-body' }, task.description) : null,
            task.completion_summary
                ? h('div', { class: 'task-detail-panel', 'data-tone': 'ok' },
                    h('p', { class: 'task-detail-heading' }, 'Completion summary'),
                    h('p', { class: 'task-detail-body' }, task.completion_summary))
                : null,
            task.status_note
                ? h('div', { class: 'task-detail-panel' },
                    h('p', { class: 'task-detail-heading' }, 'Status note'),
                    h('p', { class: 'task-detail-body' }, task.status_note))
                : null);
    }

    /** This task's deliverables, then each child's, under its own subheading. */
    function deliverables(task, children, api) {
        const own = (task.work_contract && task.work_contract.deliverables) || [];
        const childRows = children
            .map((child) => ({ child, rows: (child.work_contract && child.work_contract.deliverables) || [] }))
            .filter((entry) => entry.rows.length > 0);
        const total = own.length + childRows.reduce((sum, entry) => sum + entry.rows.length, 0);
        if (total === 0) return null;
        const node = section(`Deliverables (${total})`);
        own.forEach((item) => node.append(DELIVERABLES.renderDeliverable(item, task, api)));
        childRows.forEach((entry) => {
            node.append(h('p', { class: 'task-detail-meta' }, entry.child.title || 'Subtask'));
            entry.rows.forEach((item) =>
                node.append(DELIVERABLES.renderDeliverable(item, entry.child, api)));
        });
        return node;
    }

    function links(task, allTasks, children, onNavigate) {
        const nodes = [];
        if (task.parent_task_id) {
            const parent = allTasks.find((item) => item.id === task.parent_task_id);
            nodes.push(parent
                ? h('button', {
                    class: 'task-detail-link', type: 'button',
                    onclick: () => onNavigate(parent.id),
                }, `Parent: ${parent.title}`)
                : h('p', { class: 'task-detail-meta' }, 'Parent task (not in the current view)'));
        }
        children.forEach((child) => {
            nodes.push(h('button', {
                class: 'task-detail-link', type: 'button',
                onclick: () => onNavigate(child.id),
            },
                h('span', {}, child.title || 'Subtask'),
                h('span', { class: 'status-pill', 'data-status': child.status }, child.status || '')));
        });
        if (nodes.length === 0) return null;
        return section(children.length ? `Subtasks (${children.length})` : 'Parent', ...nodes);
    }

    /**
     * Open one task in the shared slide-over.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string} deps.taskId
     * @param {object[]} deps.tasks  The board's current list, used to resolve
     *   the parent and children without a second request.
     * @param {(taskId: string) => void} deps.onNavigate
     * @param {(task: object) => void} deps.onCancel  The Board owns cancelling;
     *   this panel only asks for it.
     * @param {() => void} [deps.onClose]
     * @returns {{close: () => void}}
     * @throws {Error} When a dependency is missing, or when the task id is not
     *   in the list — opening a panel for a task nobody can name would show an
     *   empty slide-over with no explanation.
     */
    function openTaskDetail(deps) {
        const { api, taskId, tasks, onNavigate, onCancel, onClose } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-detail] deps.api is required');
        if (typeof onNavigate !== 'function') throw new Error('[task-detail] deps.onNavigate is required');
        if (typeof onCancel !== 'function') throw new Error('[task-detail] deps.onCancel is required');
        const task = (tasks || []).find((item) => item.id === taskId);
        if (!task) throw new Error(`[task-detail] no task "${taskId}" in the current board`);

        const children = tasks.filter((item) => item.parent_task_id === task.id);
        const events = BossModTaskEvents.createTaskEvents({ api, taskId: task.id });

        const body = h('div', { class: 'task-detail' },
            section(null,
                h('p', { class: 'task-detail-title' }, task.title || 'Untitled task'),
                h('span', { class: 'status-pill', 'data-status': task.status }, task.status || ''),
                ...people(task),
                COLUMNS.isTerminal(task.status)
                    ? null
                    : h('button', {
                        class: 'btn btn-sm board-danger',
                        id: 'ct-cancel-task-btn',
                        type: 'button',
                        onclick: () => onCancel(task),
                    }, 'Cancel task')),
            roleContract(task),
            narrative(task),
            deliverables(task, children, api),
            links(task, tasks, children, onNavigate),
            events.element);

        const panel = BossModOverlays.slideOver({
            title: task.title || 'Task',
            body,
            onClose: () => {
                events.destroy();
                if (onClose) onClose();
            },
        });

        return { close: panel.close };
    }

    return { openTaskDetail };
})();
