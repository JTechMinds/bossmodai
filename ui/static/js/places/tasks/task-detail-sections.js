/**
 * BossMod AI — the task detail's sections, as pure builders.
 *
 * Each function takes a task (and what it needs to act) and returns a node,
 * or null when the task has nothing to say there — the detail composes them
 * top to bottom and a null is simply left out. None of them fetches, holds
 * state or knows about the modal around it; task-detail.js owns that.
 *
 * The copy that tells an operator what done means is behaviourally
 * load-bearing and is kept verbatim from the panel this replaced:
 * test_role_contracts.py asserts it still reaches the detail.
 */
const BossModTaskDetailSections = (() => {
    const { h } = BossModDom;
    const COLUMNS = BossModTasksColumns;
    const DELIVERABLES = BossModTaskDeliverables;
    const FORMAT = BossModFormat;

    /** The operator's own id: HUMAN_SENDER_ID in core/models/message.py. */
    const HUMAN_ID = '__human__';

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

    /** A section with a label on the left and a count on the right. */
    function headed(label, count, ...children) {
        return h('section', { class: 'task-detail-section' },
            h('div', { class: 'task-detail-section-head' },
                h('span', {}, label),
                h('span', { class: 'task-detail-count' }, count)),
            ...children);
    }

    /** `a` or `an`, by the sound the role starts with as written. */
    function article(word) {
        return /^[aeiou]/i.test(word) ? 'an' : 'a';
    }

    /**
     * The status pill and how long the task has been in it.
     *
     * A Needs task shows how long it has gone without progress, on the
     * watchdog's own clock (core/agent_loop/watchdog.py): last progress, else
     * last activity, else creation.
     *
     * @param {object} task
     * @returns {HTMLElement} `div.task-detail-status`.
     */
    function statusLine(task) {
        let since;
        if (task.status === 'complete') {
            since = task.closed_at ? `Finished ${FORMAT.formatDateTime(task.closed_at)}` : '';
        } else if (COLUMNS.isClosedWithoutCompleting(task.status)) {
            since = task.closed_at ? `Closed ${FORMAT.formatDateTime(task.closed_at)}` : '';
        } else if (COLUMNS.columnFor(task.status) === 'needs') {
            const clock = task.last_progress_at || task.last_activity || task.created_at;
            since = `${FORMAT.formatDuration((Date.now() - Date.parse(clock)) / 1000)} without progress`;
        } else {
            const updated = FORMAT.formatRelativeTime(task.last_activity);
            since = updated ? `Updated ${updated}` : '';
        }
        // No time at all rather than a made-up one: a finished task without a
        // closed_at is already reported by the page it was opened from.
        return h('div', { class: 'task-detail-status' },
            h('span', { class: 'status-pill', 'data-status': task.status },
                COLUMNS.STATUS_LABELS[task.status] || task.status),
            since ? h('span', { class: 'task-detail-since' }, since) : null);
    }

    /**
     * Who, when, and what the task belongs to, two pairs to a row.
     *
     * @param {object} task
     * @param {object} deps
     * @param {object[]} deps.tasks  The page's list, to resolve the parent.
     * @param {(agentId: string) => (string|undefined)} deps.colorOf
     * @param {(taskId: string) => void} deps.onNavigate  Opens the parent.
     * @returns {HTMLElement} `dl.fact-list[data-pairs="2"]`.
     * @throws {Error} When the list, colorOf or onNavigate is missing — a
     *   parent that could not be looked up would read "Not in the current
     *   list", which is a different fact.
     */
    function facts(task, deps) {
        const { tasks, colorOf, onNavigate } = deps || {};
        if (!Array.isArray(tasks)) throw new Error('[task-detail] facts needs the task list');
        if (typeof colorOf !== 'function') throw new Error('[task-detail] facts needs colorOf');
        if (typeof onNavigate !== 'function') throw new Error('[task-detail] facts needs onNavigate');
        const person = (id, name) => [
            BossModAvatar.create({ name, color: colorOf(id), size: 'chip' }), name,
        ];
        let requester = 'Unknown';
        if (task.requester_id === HUMAN_ID) requester = 'You';
        else if (task.requester_name) requester = person(task.requester_id, task.requester_name);

        const list = [
            {
                label: 'Assignee',
                value: task.assigned_to
                    ? person(task.assigned_to, task.assigned_to_name || 'Unknown') : 'Unassigned',
            },
            { label: 'Requester', value: requester },
            { label: 'Created', value: FORMAT.formatDateTime(task.created_at) },
            { label: 'Updated', value: FORMAT.formatRelativeTime(task.last_activity) },
        ];
        // The owner is news only when it is somebody the two rows above do not
        // already name.
        if (task.owner_id && task.owner_id !== task.assigned_to && task.owner_id !== task.requester_id) {
            list.push({ label: 'Owner', value: person(task.owner_id, task.owner_name || 'Unknown') });
        }
        if (task.project) list.push({ label: 'Project', value: task.project });
        if (task.cost_ceiling != null) list.push({ label: 'Cost ceiling', value: String(task.cost_ceiling) });
        if (task.parent_task_id) {
            const parent = tasks.find((item) => item.id === task.parent_task_id);
            list.push({
                label: 'Part of',
                value: parent
                    ? h('button', {
                        class: 'btn-link', type: 'button', onclick: () => onNavigate(parent.id),
                    }, parent.title || 'Untitled task')
                    : 'Not in the current list',
            });
        }
        return BossModFactList.create(list, { pairsPerRow: 2 });
    }

    /**
     * The one box that says what state the task is in, when it needs saying.
     *
     * @param {object} task
     * @param {object} deps
     * @param {(task: object) => void} deps.onOpenChat  For a Needs task with
     *   somewhere to chat.
     * @returns {HTMLElement|null} null when the task has nothing to report.
     * @throws {Error} When a Needs task has a chat to offer and onOpenChat is
     *   missing — the button would render and do nothing.
     */
    function callout(task, deps) {
        const { onOpenChat } = deps || {};
        const label = COLUMNS.STATUS_LABELS[task.status] || task.status;
        const note = task.status_note || '';
        const body = (text) => h('p', { class: 'callout-body' }, text);

        if (COLUMNS.columnFor(task.status) === 'needs') {
            const target = BossModTasksData.chatTargetFor(task);
            if (target && typeof onOpenChat !== 'function') {
                throw new Error('[task-detail] callout needs onOpenChat');
            }
            return h('div', { class: 'callout', 'data-tone': 'alert' },
                h('p', { class: 'callout-title' },
                    h('i', { 'data-lucide': 'hand', 'aria-hidden': 'true' }),
                    label, note ? ` · ${note}` : ''),
                target
                    ? h('div', { class: 'callout-actions' },
                        h('button', {
                            class: 'btn btn-sm', type: 'button', onclick: () => onOpenChat(task),
                        }, 'Open chat'))
                    : null);
        }
        if (task.status === 'complete') {
            const claim = BossModSpecialty.formatDoneClaim(resolveDoneClaim(task));
            if (!task.completion_summary && !claim) return null;
            return h('div', { class: 'callout', 'data-tone': 'ok' },
                h('p', { class: 'callout-title' }, 'Done claim'),
                task.completion_summary ? body(task.completion_summary) : null,
                claim ? body(claim) : null);
        }
        if (COLUMNS.isClosedWithoutCompleting(task.status)) {
            return h('div', { class: 'callout' },
                h('p', { class: 'callout-title' }, label),
                note ? body(note) : null);
        }
        if (!note) return null;
        return h('div', { class: 'callout' },
            h('p', { class: 'callout-title' }, 'Status note'), body(note));
    }

    /**
     * What the task asks for, rendered as markdown and clamped to six lines
     * until measureClamp() has seen whether it actually overflows.
     *
     * @param {object} task
     * @returns {HTMLElement|null} null without a description.
     */
    function instructions(task) {
        if (!task.description) return null;
        const text = h('div', { class: 'task-detail-instructions md is-clamped' },
            BossModMarkdown.render(task.description));
        const more = h('button', {
            class: 'btn-link task-detail-more', type: 'button',
            onclick: () => {
                text.classList.remove('is-clamped');
                more.remove();
            },
        }, 'Show full instruction');
        // Hidden until measured: a toggle for text that fits would promise
        // more than there is.
        more.hidden = true;
        return h('section', { class: 'task-detail-section' },
            h('p', { class: 'task-detail-heading' }, 'What to do'), text, more);
    }

    /**
     * Decide the clamp once the section is on screen and has a height.
     *
     * @param {HTMLElement} sectionEl  From instructions().
     * @returns {void}
     * @throws {Error} When the section is not one instructions() built.
     */
    function measureClamp(sectionEl) {
        const text = sectionEl.querySelector('.task-detail-instructions');
        const more = sectionEl.querySelector('.task-detail-more');
        if (!text || !more) throw new Error('[task-detail] measureClamp needs an instructions section');
        // One pixel of slack: line boxes round, and a text that fits exactly
        // can report a scrollHeight a pixel over its box.
        if (text.scrollHeight <= text.clientHeight + 1) {
            text.classList.remove('is-clamped');
            more.remove();
            return;
        }
        more.hidden = false;
    }

    /**
     * This task's deliverables, then each child's, under its own subheading.
     *
     * @param {object} task
     * @param {object[]} children
     * @param {Function} api  Passed to each row, which opens its file.
     * @returns {HTMLElement|null} null when nothing was promised.
     */
    function deliverables(task, children, api) {
        const own = (task.work_contract && task.work_contract.deliverables) || [];
        const childRows = children
            .map((child) => ({ child, rows: (child.work_contract && child.work_contract.deliverables) || [] }))
            .filter((entry) => entry.rows.length > 0);
        const total = own.length + childRows.reduce((sum, entry) => sum + entry.rows.length, 0);
        if (total === 0) return null;
        const node = headed('Deliverables', String(total));
        own.forEach((item) => node.append(DELIVERABLES.renderDeliverable(item, task, api)));
        childRows.forEach((entry) => {
            node.append(h('p', { class: 'task-detail-meta' }, entry.child.title || 'Subtask'));
            entry.rows.forEach((item) =>
                node.append(DELIVERABLES.renderDeliverable(item, entry.child, api)));
        });
        return node;
    }

    /** A child's state as the checklist draws it. */
    function subtaskState(child) {
        if (child.status === 'complete') return { state: 'done', icon: 'check-circle-2' };
        if (COLUMNS.isClosedWithoutCompleting(child.status)) return { state: 'closed', icon: 'circle-slash' };
        return { state: 'open', icon: 'circle' };
    }

    /**
     * The subtasks as a checklist, each opening as a layer.
     *
     * @param {object[]} children
     * @param {(taskId: string) => void} onNavigate
     * @returns {HTMLElement|null} null without children.
     * @throws {Error} When onNavigate is missing.
     */
    function subtasks(children, onNavigate) {
        if (children.length === 0) return null;
        if (typeof onNavigate !== 'function') throw new Error('[task-detail] subtasks needs onNavigate');
        const done = children.filter((child) => child.status === 'complete').length;
        return headed('Subtasks', `${done} of ${children.length}`,
            ...children.map((child) => {
                const { state, icon } = subtaskState(child);
                return h('button', {
                    class: 'task-detail-subtask', type: 'button', 'data-state': state,
                    onclick: () => onNavigate(child.id),
                },
                    h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }),
                    h('span', { class: 'task-detail-subtask-title' }, child.title || 'Untitled task'),
                    // The glyph and the strike are visual; the state is said too.
                    h('span', { class: 'visually-hidden' },
                        ` — ${COLUMNS.STATUS_LABELS[child.status] || child.status}`));
            }));
    }

    /**
     * What done means for this assignee, folded away until asked for.
     *
     * Shown under the condition the panel it replaced used: the task is not
     * complete and has an assignee to hold to it.
     *
     * @param {object} task
     * @returns {HTMLElement|null} `details.task-detail-contract`, or null.
     */
    function doneContract(task) {
        if (task.status === 'complete' || !task.assigned_to) return null;
        const role = task.assigned_to_role || '';
        const doneBar = task.assigned_to_done_fail_bar || '';
        return h('details', { class: 'task-detail-contract' },
            h('summary', {},
                role ? `What counts as done for ${article(role)} ${role}` : 'What counts as done'),
            doneBar ? h('p', { class: 'task-detail-meta' }, `What done looks like: ${doneBar}`) : null,
            h('div', { class: 'callout', 'data-tone': 'warn' },
                h('p', { class: 'callout-title' }, BLOCKED_COPY),
                h('p', { class: 'callout-body' }, BossModSpecialty.doneClaimGuidance(task)),
                h('p', { class: 'callout-body' }, NEEDED_COPY)));
    }

    return {
        statusLine, facts, callout, instructions, measureClamp,
        deliverables, subtasks, doneContract,
    };
})();
