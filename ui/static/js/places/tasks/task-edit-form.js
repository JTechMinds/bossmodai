/**
 * BossMod AI — editing an open task: title, description, assignee, files.
 *
 * The operator's way to fix a task instead of cancelling it: reword it,
 * reassign it, or change the files it must produce. It sends only what
 * changed (`PATCH /api/tasks/{id}`); the server decides what that means —
 * a reassign or a requirements change puts the task back in front of its
 * assignee, a title edit wakes nobody.
 *
 * The assignee list is ranked exactly as the assign dialog ranks it
 * (BossModAssignForm.rankRoster / optionLabel): a second ranking would be a
 * second opinion about whether an assignment is sensible. A specialty
 * mismatch the server refuses is shown with its suggestions and a
 * "Reassign anyway" that resends with the confirmation.
 */
const BossModTaskEditForm = (() => {
    const { h, clear } = BossModDom;
    const ASSIGN = BossModAssignForm;
    const COLUMNS = BossModTasksColumns;

    const FORM_ID = 'ct-edit-form';
    const UNASSIGNED = '';

    /** A titled `.callout` in one of the shared tones. */
    function callout(tone, title, ...children) {
        return h('div', { class: 'callout', 'data-tone': tone },
            h('p', { class: 'callout-title' }, title), ...children);
    }

    /**
     * The file deliverables a task lists now, as plain rows.
     *
     * @param {object} task
     * @returns {Array<{path: string, description: (string|null)}>}
     */
    function currentFiles(task) {
        const contract = task.work_contract;
        const items = contract && Array.isArray(contract.deliverables) ? contract.deliverables : [];
        return items
            .filter((item) => item && item.type === 'file')
            .map((item) => ({ path: item.path, description: item.description || null }));
    }

    /**
     * Open the edit dialog for one task.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.store  Application store; its roster is who can be
     *   assigned, limited to the task's floor when the task has one.
     * @param {object} deps.task  The listed task row being edited.
     * @param {(task: object) => void} deps.onSaved  The server accepted; it is
     *   handed the stored task row.
     * @returns {{close: () => void}}
     * @throws {Error} When a dependency is missing, or the task has ended —
     *   a closed task is not editable, and the server would only refuse it.
     */
    function openEditForm(deps) {
        const { api, store, task, onSaved } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-edit-form] deps.api is required');
        if (!store) throw new Error('[task-edit-form] deps.store is required');
        if (!task || !task.id) throw new Error('[task-edit-form] deps.task is required');
        if (typeof onSaved !== 'function') throw new Error('[task-edit-form] deps.onSaved is required');
        if (COLUMNS.isTerminal(task.status)) throw new Error(`[task-edit-form] task "${task.id}" has ended`);

        const roster = (store.getState().roster || []).filter((agent) => agent
            && (!task.floor_id || agent.floorId === task.floor_id));
        const originalFiles = currentFiles(task);
        let saving = false;

        const titleInput = h('input', {
            class: 'assign-input', id: 'ct-edit-title', type: 'text', maxlength: '200',
            'aria-required': 'true', oninput: () => refreshAssignees(),
        });
        titleInput.value = task.title || '';
        const description = h('textarea', {
            class: 'assign-textarea', id: 'ct-edit-description', rows: '4', maxlength: '4000',
            oninput: () => refreshAssignees(),
        });
        description.value = task.description || '';

        /** Assignee options, ranked for the words in the form right now. */
        function assigneeOptions() {
            const ranked = ASSIGN.rankRoster(roster, titleInput.value, description.value)
                .map((agent) => ({
                    value: agent.id,
                    label: ASSIGN.optionLabel(agent, titleInput.value, description.value),
                    avatar: { name: agent.name, color: agent.color },
                }));
            // The current assignee stays choosable even when the roster no
            // longer lists them, so opening the form never changes it.
            if (task.assigned_to && !ranked.some((option) => option.value === task.assigned_to)) {
                ranked.unshift({ value: task.assigned_to, label: task.assigned_to_name || task.assigned_to });
            }
            return [{ value: UNASSIGNED, label: 'Unassigned backlog' }, ...ranked];
        }

        const assignee = BossModMenuSelect.create({
            label: 'Assignee',
            options: assigneeOptions(),
            value: task.assigned_to || UNASSIGNED,
            // The control owns the choice; it is read back when Save runs.
            onChange: () => {},
        });

        function refreshAssignees() {
            assignee.setOptions(assigneeOptions());
        }

        const fileRows = h('div', { class: 'task-edit-files', id: 'ct-edit-files' });

        /** One editable deliverable row. */
        function fileRow(file) {
            const path = h('input', {
                class: 'assign-input', type: 'text', 'data-field': 'path',
                'aria-label': 'File path', placeholder: '/me/out/report.md',
            });
            path.value = file.path || '';
            const note = h('input', {
                class: 'assign-input', type: 'text', 'data-field': 'description',
                'aria-label': 'What the file is', placeholder: 'What the file is (optional)',
            });
            note.value = file.description || '';
            const row = h('div', { class: 'assign-candidate', 'data-file-row': '' }, path, note);
            row.append(h('button', {
                class: 'btn btn-sm', type: 'button', 'aria-label': 'Remove this file',
                onclick: () => row.remove(),
            }, 'Remove'));
            return row;
        }

        originalFiles.forEach((file) => fileRows.append(fileRow(file)));
        const addFile = h('button', {
            class: 'assign-pick', id: 'ct-edit-add-file', type: 'button',
            onclick: () => {
                const row = fileRow({ path: '', description: null });
                fileRows.append(row);
                row.querySelector('[data-field="path"]').focus();
            },
        }, 'Add a required file');

        const result = h('div', { class: 'assign-result', role: 'alert' });

        function field(label, control, extra) {
            return h('label', { class: 'assign-field' },
                h('span', { class: 'assign-field-label' }, label), control, extra || null);
        }

        const form = h('form', {
            class: 'assign-form', id: FORM_ID,
            onsubmit: (event) => { event.preventDefault(); void send(false); },
        },
            field('Title', titleInput),
            h('div', { class: 'assign-field' },
                h('span', { class: 'assign-field-label' }, 'Assignee'), assignee.element),
            field('Description', description),
            h('div', { class: 'assign-field' },
                h('span', { class: 'assign-field-label' }, 'Required files'),
                fileRows, addFile,
                h('p', { class: 'assign-hint' }, 'No files means the task has no file requirement.')),
            result);

        const modal = BossModOverlays.createModal({
            title: `Edit: ${task.title || 'Task'}`,
            body: form,
            size: 'panel',
            closeOnBackdrop: false,
            actions: [
                { label: 'Discard changes' },
                // Submits without closing: a refusal (a mismatch, a bad path)
                // is shown here, and only a saved edit lets the dialog go.
                { label: 'Save', tone: 'primary', id: 'ct-edit-submit', form: FORM_ID },
            ],
            onClose: () => assignee.destroy(),
        });

        function show(node) {
            clear(result);
            if (node) result.append(node);
        }

        /**
         * The files the form lists now.
         * @returns {Array<{type: string, path: string, description: (string|null)}>}
         * @throws {Error} When a row describes a file but names no path.
         */
        function formFiles() {
            return Array.from(fileRows.querySelectorAll('[data-file-row]')).map((row) => {
                const path = row.querySelector('[data-field="path"]').value.trim();
                const note = row.querySelector('[data-field="description"]').value.trim();
                if (!path && note) throw new Error(`The file "${note}" needs a path.`);
                return path ? { type: 'file', path, description: note || null } : null;
            }).filter(Boolean);
        }

        /**
         * Only the fields that differ from the task as listed.
         * @returns {object}
         * @throws {Error} On a blank title or a file row without a path.
         */
        function changes() {
            const body = {};
            const title = titleInput.value.trim();
            if (!title) throw new Error('The title cannot be blank.');
            if (title !== task.title) body.title = title;
            const note = description.value.trim() || null;
            if (note !== (task.description || null)) body.description = note;
            const chosen = assignee.getValue();
            if (chosen !== (task.assigned_to || UNASSIGNED)) body.assigned_to = chosen || null;
            const files = formFiles();
            const before = originalFiles.map((file) => ({ type: 'file', ...file }));
            if (JSON.stringify(files) !== JSON.stringify(before)) {
                body.work_contract = files.length ? { deliverables: files } : null;
            }
            return body;
        }

        function mismatch(body) {
            const suggested = Array.isArray(body.suggested_assignees) ? body.suggested_assignees : [];
            const box = callout('warn', 'Specialty mismatch — nothing was saved',
                h('p', { class: 'callout-body' }, body.reason || 'That specialty does not match this work.'));
            suggested.forEach((agent) => {
                box.append(h('button', {
                    class: 'assign-pick', type: 'button',
                    onclick: () => { assignee.setOptions(assigneeOptions(), agent.id); void send(false); },
                }, `${agent.name || 'Teammate'} — ${agent.role || 'No specialty'}`));
            });
            box.append(h('button', {
                class: 'assign-pick', id: 'ct-edit-reassign-anyway', type: 'button',
                onclick: () => { void send(true); },
            }, 'Reassign anyway'));
            return box;
        }

        function submitButton() {
            return modal.element.querySelector('#ct-edit-submit');
        }

        /**
         * PATCH what changed.
         * @param {boolean} confirmMismatch  Resend past a specialty warning.
         * @returns {Promise<void>} Never rejects; every failure is shown in the dialog.
         */
        async function send(confirmMismatch) {
            if (saving) return;
            let payload;
            try {
                payload = changes();
            } catch (err) {
                show(callout('alert', err.message));
                return;
            }
            if (Object.keys(payload).length === 0) {
                show(callout('info', 'Nothing has changed.'));
                return;
            }
            if (confirmMismatch) payload.confirm_specialty_mismatch = true;
            saving = true;
            submitButton().disabled = true;
            submitButton().textContent = 'Saving…';
            try {
                const res = await api(`/api/tasks/${encodeURIComponent(task.id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const body = await res.json();
                if (res.status === 409 && body && body.outcome === 'specialty_mismatch') {
                    show(mismatch(body));
                    return;
                }
                if (!res.ok) {
                    const detail = body && body.detail;
                    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail || `HTTP ${res.status}`));
                }
                modal.close();
                onSaved(body);
            } catch (err) {
                console.error('[task-edit-form] save failed', err);
                show(callout('alert', 'Could not save the task',
                    h('p', { class: 'callout-body' }, (err && err.message) || 'The request failed.')));
            } finally {
                saving = false;
                submitButton().disabled = false;
                submitButton().textContent = 'Save';
            }
        }

        BossModIcons.paint(modal.element, 'task-edit-form');
        titleInput.focus();
        return { close: modal.close };
    }

    return { openEditForm };
})();
