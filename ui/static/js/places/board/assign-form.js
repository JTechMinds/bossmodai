/**
 * BossMod AI — the assign-task sheet.
 *
 * Ported from the assign section of company-tasks.js. It is the ONE task form
 * in the application: spec 4.4 deferred the composer's clipboard button to this
 * module rather than letting Phase 2 build a second one, so `openAssignForm` is
 * the entry point conversation/composer.js opens in Phase 3B.
 *
 * Specialty ranking, the mismatch warning, and the match labels all come from
 * BossModSpecialty. None of that logic is reimplemented here — a second copy would
 * be a second opinion about whether an assignment is sensible.
 */
const BossModAssignForm = (() => {
    const { h, clear } = BossModDom;

    const HINT_COPY = 'Same title + assignee reuses an open workstream instead of '
        + 'creating a duplicate. Matching specialties are listed first.';

    /**
     * Rank the roster: matching specialties first, then by name.
     *
     * @param {object[]} roster
     * @param {string} title
     * @param {string} description
     * @returns {object[]} A new array.
     */
    function rankRoster(roster, title, description) {
        return roster.slice().sort((a, b) => {
            const delta = BossModSpecialty.specialtyRank(a, title, description)
                - BossModSpecialty.specialtyRank(b, title, description);
            if (delta !== 0) return delta;
            return (a.name || '').localeCompare(b.name || '');
        });
    }

    /**
     * The label one assignee gets in the select.
     *
     * @param {object} agent
     * @param {string} title
     * @param {string} description
     * @returns {string}
     */
    function optionLabel(agent, title, description) {
        const status = BossModSpecialty.specialtyMatch(agent.role, title, description);
        let label = agent.name || 'Teammate';
        if (agent.role) label += ` — ${agent.role}`;
        if (status === 'match') label += ' (matches)';
        if (status === 'mismatch') label += ' (mismatch)';
        return label;
    }

    /**
     * Open the assign sheet.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.store  Read for the assignee to preselect: the
     *   Board's agent filter, or the agent whose conversation is open. Both are
     *   "the person you were already looking at".
     * @param {string} [deps.taskId]  An existing workstream to bind to, sent as
     *   `bind_task_id`.
     * @param {boolean} [deps.bindOrigin]  Conversation assign stamps the open
     *   thread (or Focus) so Created/Accepted land there. Board omits this —
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
        let chosen = state.placeParams.agentFilter
            || (state.conversationKind === 'agent' ? state.conversationId : '')
            || '';
        let submitting = false;

        const titleInput = h('input', {
            class: 'assign-input', type: 'text', required: true, maxlength: '200',
            placeholder: 'What should they work on?',
            oninput: () => refreshSpecialtyHints(),
        });
        const agentSelect = h('select', {
            class: 'assign-select', id: 'ct-assign-agent',
            onchange: (event) => { chosen = event.target.value; refreshSpecialtyHints(); },
        });
        const mismatch = h('div', { class: 'assign-mismatch', id: 'ct-assign-mismatch', hidden: true });
        const description = h('textarea', {
            class: 'assign-textarea', id: 'ct-assign-description', rows: '3', maxlength: '4000',
            placeholder: 'Context, constraints, or the expected deliverable',
            oninput: () => refreshSpecialtyHints(),
        });
        const result = h('div', { class: 'assign-result' });
        const submit = h('button', { class: 'btn', type: 'submit' }, 'Assign');

        function field(label, control, extra) {
            return h('label', { class: 'assign-field' },
                h('span', { class: 'assign-field-label' }, label), control, extra || null);
        }

        const form = h('form', { class: 'assign-form', onsubmit: (event) => {
            event.preventDefault();
            void send({});
        } },
            field('Title', titleInput),
            field('Assignee', agentSelect, mismatch),
            field('Description (optional)', description),
            h('p', { class: 'assign-hint' }, HINT_COPY),
            h('div', { class: 'assign-actions' },
                h('button', { class: 'btn', type: 'button', onclick: () => sheet.close() }, 'Cancel'),
                submit),
            result);

        const sheet = BossModOverlays.slideOver({ title: 'Assign a task', body: form });

        /** Repaint the ranked options and the mismatch warning together. */
        function refreshSpecialtyHints() {
            const title = titleInput.value;
            const note = description.value;
            clear(agentSelect);
            agentSelect.append(h('option', { value: '' }, 'Unassigned backlog'));
            rankRoster(roster, title, note).forEach((agent) => {
                agentSelect.append(h('option', { value: agent.id }, optionLabel(agent, title, note)));
            });
            agentSelect.value = chosen;

            clear(mismatch);
            const agent = roster.find((item) => item.id === chosen);
            const warning = BossModSpecialty.specialtyWarningMessage(agent, title, note);
            mismatch.hidden = !warning;
            if (warning) {
                mismatch.append(
                    BossModAssignOutcomes.renderNotice('warn', 'Specialty mismatch', warning));
            }
        }

        function show(node) {
            clear(result);
            if (node) result.append(node);
        }

        function showOutcome(body) {
            show(BossModAssignOutcomes.renderOutcome(body, {
                onPickAssignee: (agentId) => {
                    chosen = agentId;
                    refreshSpecialtyHints();
                    void send({});
                },
                onAssignAnyway: () => { void send({ confirmSpecialtyMismatch: true }); },
                onReuse: (candidateId) => { void send({ bindTaskId: candidateId }); },
                // Passed straight through: when the caller cannot open a task,
                // assign-outcomes.js renders no View button at all.
                onOpenTask: onOpenTask
                    ? (candidateId) => { sheet.close(); onOpenTask(candidateId); }
                    : null,
            }));
        }

        /**
         * Submit the form.
         *
         * @param {object} options
         * @param {string} [options.bindTaskId]
         * @param {boolean} [options.confirmSpecialtyMismatch]
         * @returns {Promise<void>} Never rejects; a failure is shown in the sheet.
         */
        async function send({ bindTaskId, confirmSpecialtyMismatch }) {
            if (submitting) return;
            const title = titleInput.value.trim();
            if (!title) {
                show(BossModAssignOutcomes.renderNotice('error', 'Title is required'));
                titleInput.focus();
                return;
            }
            submitting = true;
            submit.disabled = true;
            submit.textContent = 'Assigning…';
            const payload = { title, description: description.value.trim() || null };
            if (chosen) payload.assigned_to = chosen;
            if (bindTaskId || taskId) payload.bind_task_id = bindTaskId || taskId;
            if (confirmSpecialtyMismatch) payload.confirm_specialty_mismatch = true;
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
                const settled = body.outcome !== 'clarify_ambiguous_match'
                    && body.outcome !== 'specialty_mismatch';
                if (settled && body.task && onCreated) onCreated(body.task);
            } catch (err) {
                console.error('[assign-form] assign failed', err);
                show(BossModAssignOutcomes.renderNotice('error', 'Assign failed',
                    (err && err.message) || 'The request failed.'));
            } finally {
                submitting = false;
                submit.disabled = false;
                submit.textContent = 'Assign';
            }
        }

        /**
         * Load the roster the select ranks.
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
                refreshSpecialtyHints();
            } catch (err) {
                console.error('[assign-form] could not load the roster', err);
                show(BossModAssignOutcomes.renderNotice('error', 'Could not load the roster',
                    'The task can still be created for the unassigned backlog.'));
            }
        }

        refreshSpecialtyHints();
        void loadRoster();
        titleInput.focus();

        return { close: sheet.close };
    }

    return { openAssignForm, rankRoster, optionLabel };
})();
