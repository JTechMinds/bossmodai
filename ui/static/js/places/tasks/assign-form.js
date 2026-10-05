/**
 * BossMod AI — the assign-task dialog.
 *
 * Ported from the assign section of company-tasks.js. It is the ONE task form
 * in the application: spec 4.4 deferred the composer's clipboard button to this
 * module rather than letting Phase 2 build a second one, so `openAssignForm` is
 * the entry point conversation/composer.js opens in Phase 3B.
 *
 * The assignee list is the roster by name. Whoever assigns decides who does
 * the work, so the dialog never ranks, labels or warns by specialty.
 */
const BossModAssignForm = (() => {
    const { h, clear } = BossModDom;

    const HINT_COPY = 'Same title + assignee reuses an open workstream instead of '
        + 'creating a duplicate.';

    /** The "nobody yet" choice; `short` is what a field trigger shows. */
    const BACKLOG = Object.freeze({ value: '', label: 'Unassigned backlog', short: 'Unassigned' });

    /**
     * The assignee dropdown's options. One shape for both task forms: this
     * dialog and the task detail's Edit mode (places/tasks/task-edit-mode.js).
     *
     * @param {object[]} agents
     * @returns {Array<{value: string, label: string, short: string,
     *   avatar?: {name: string, color: string}}>} The backlog first, then the
     *   roster sorted by name: each row's `label` is the full "name — role",
     *   its `short` the name alone, which is all a field trigger has room for.
     */
    function rosterOptions(agents) {
        const sorted = agents.slice().sort((a, b) => (a.name || '').localeCompare(b.name || ''));
        return [BACKLOG, ...sorted.map((agent) => ({
            value: agent.id,
            label: agent.role ? `${agent.name || 'Teammate'} — ${agent.role}` : (agent.name || 'Teammate'),
            short: agent.name || 'Teammate',
            avatar: { name: agent.name, color: agent.color },
        }))];
    }

    /**
     * Open the assign dialog.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.store  Read for the assignee to preselect: the
     *   Tasks agent filter, or the agent whose conversation is open. Both are
     *   "the person you were already looking at".
     * @param {string} [deps.taskId]  An existing workstream to bind to, sent as
     *   `bind_task_id`.
     * @param {boolean} [deps.bindOrigin]  Conversation assign stamps the open
     *   thread (or Focus) so Created/Accepted land there. Tasks omits this —
     *   leftover conversationId in the store is not a bind the operator asked for.
     * @param {(task: object) => void} [deps.onCreated]
     * @param {(taskId: string) => void} [deps.onOpenTask]  Optional capability.
     *   When supplied, an ambiguous-match candidate can be opened as well as
     *   reused; when not, that control is not rendered at all — a button that
     *   renders and does nothing is worse than one that is absent.
     * @returns {{close: () => void}}
     * @throws {Error} When api or store is missing.
     */
    function openAssignForm(deps) {
        const { api, store, taskId, onCreated, onOpenTask, bindOrigin } = deps || {};
        if (typeof api !== 'function') throw new Error('[assign-form] deps.api is required');
        if (!store) throw new Error('[assign-form] deps.store is required');

        const state = store.getState();
        let roster = [];
        // The teammate to preselect. It becomes the choice only once the
        // roster names them: the dropdown cannot show an id it has no row
        // for, and what it shows is what the submit sends.
        const preselect = state.placeParams.agentFilter
            || (state.conversationKind === 'agent' ? state.conversationId : '')
            || '';
        let chosen = BACKLOG.value;
        let submitting = false;

        const titleInput = h('input', {
            class: 'field-input', type: 'text', required: true, maxlength: '200',
            placeholder: 'What should they work on?',
        });
        const agentSelect = BossModMenuSelect.create({
            label: 'Assignee',
            options: rosterOptions([]),
            value: BACKLOG.value,
            variant: 'field',
            id: 'ct-assign-agent',
            onChange: (value) => { chosen = value; },
        });
        BossModIcons.paint(agentSelect.element, 'assign-form');
        const description = h('textarea', {
            class: 'field-textarea', id: 'ct-assign-description', 'data-size': 'long', 'data-autogrow': true,
            maxlength: '4000', placeholder: 'Context, constraints, or the expected deliverable',
        });
        const grow = BossModAutoGrow.bind(description);
        const result = h('div', { class: 'assign-result' });

        function field(label, control) {
            return h('label', { class: 'assign-field' },
                h('span', { class: 'assign-field-label' }, label), control);
        }

        const form = h('form', { class: 'assign-form', id: 'ct-assign-form', onsubmit: (event) => {
            event.preventDefault();
            void send({});
        } },
            field('Title', titleInput),
            field('Assignee', agentSelect.element),
            field('Description (optional)', description),
            h('p', { class: 'assign-hint' }, HINT_COPY),
            result);

        const modal = BossModOverlays.createModal({
            title: 'Assign a task',
            body: form,
            actions: [
                { label: 'Cancel' },
                // Submits the form from the footer band and does NOT close: the
                // outcome — created, reused, an ambiguous match to clarify — lands in
                // the dialog, and only a settled one should let it go.
                { label: 'Assign', tone: 'primary', id: 'ct-assign-submit', form: 'ct-assign-form' },
            ],
            onClose: () => grow.destroy(),
        });

        /** The pinned submit. In the footer, outside the form, found by id. */
        function submitButton() {
            return modal.element.querySelector('#ct-assign-submit');
        }

        function show(node) {
            clear(result);
            if (node) result.append(node);
        }

        function showOutcome(body) {
            show(BossModAssignOutcomes.renderOutcome(body, {
                onReuse: (candidateId) => { void send({ bindTaskId: candidateId }); },
                // Passed straight through: when the caller cannot open a task,
                // assign-outcomes.js renders no View button at all.
                onOpenTask: onOpenTask
                    ? (candidateId) => { modal.close(); onOpenTask(candidateId); }
                    : null,
            }));
        }

        /**
         * Submit the form.
         *
         * @param {object} options
         * @param {string} [options.bindTaskId]
         * @returns {Promise<void>} Never rejects; a failure is shown in the dialog.
         */
        async function send({ bindTaskId }) {
            if (submitting) return;
            const title = titleInput.value.trim();
            if (!title) {
                show(BossModAssignOutcomes.renderNotice('alert', 'Title is required'));
                titleInput.focus();
                return;
            }
            submitting = true;
            submitButton().disabled = true;
            submitButton().textContent = 'Assigning…';
            const payload = { title, description: description.value.trim() || null };
            if (chosen) payload.assigned_to = chosen;
            if (bindTaskId || taskId) payload.bind_task_id = bindTaskId || taskId;
            if (bindOrigin && state.conversationKind === 'thread' && state.conversationId) {
                payload.source_channel = 'channel';
                payload.notification_channel_id = state.conversationId;
            } else if (bindOrigin && state.conversationKind === 'agent') {
                payload.source_channel = 'chat';
            }
            try {
                const res = await api('/api/tasks', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const body = await res.json();
                if (!body || typeof body !== 'object') throw new Error('The server returned no body.');
                if (body.detail && !body.outcome) {
                    throw new Error(typeof body.detail === 'string'
                        ? body.detail : JSON.stringify(body.detail));
                }
                showOutcome(body);
                const settled = body.outcome !== 'clarify_ambiguous_match';
                if (settled && body.task && onCreated) onCreated(body.task);
            } catch (err) {
                console.error('[assign-form] assign failed', err);
                show(BossModAssignOutcomes.renderNotice('alert', 'Assign failed',
                    (err && err.message) || 'The request failed.'));
            } finally {
                submitting = false;
                submitButton().disabled = false;
                submitButton().textContent = 'Assign';
            }
        }

        /**
         * Load the roster the dropdown ranks.
         *
         * @returns {Promise<void>} Never rejects. A failure is shown rather than
         *   silently leaving an empty list, which company-tasks.js did and which
         *   read as "there is nobody to assign to".
         */
        async function loadRoster() {
            try {
                const res = await api('/api/agents', { cache: 'no-store' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const rows = await res.json();
                roster = Array.isArray(rows) ? rows : [];
                if (preselect && roster.some((agent) => agent.id === preselect)) chosen = preselect;
                agentSelect.setOptions(rosterOptions(roster), chosen);
            } catch (err) {
                console.error('[assign-form] could not load the roster', err);
                show(BossModAssignOutcomes.renderNotice('alert', 'Could not load the roster',
                    'The task can still be created for the unassigned backlog.'));
            }
        }

        void loadRoster();
        titleInput.focus();

        return { close: modal.close };
    }

    return { openAssignForm, rosterOptions };
})();
