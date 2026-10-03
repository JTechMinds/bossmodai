/**
 * BossMod AI — the task detail's Edit mode: the same panel, editable in place.
 *
 * It replaced a separate edit dialog. The operator presses the detail's
 * pencil and the fields they can change become controls where they already
 * sit: the title in the modal head, the assignee in its fact cell, the
 * task's text in its section, and its required files as the same rows, with
 * the subtasks' files read-only beneath them in the one Deliverables section
 * (task-edit-files.js, which picks each path from the assignee's files).
 * It follows core/inline-rename.js's "one control, two states": a field
 * that reads as text until it is editable, with the edit hairline under it
 * (the shared `.edit-field` look, controls.css; the description is outlined
 * and grows with its text through core/autogrow.js), and no dialogs or
 * layout jumps.
 *
 * This module owns the edit STATE and the nodes that carry it; it never
 * fetches. Saving is `deps.onSave`'s (the detail's call to
 * `update` in task-actions.js), and only what changed is sent, so the server
 * decides what a change means — a reassign or a requirements change puts
 * the task back in front of its assignee, a title edit wakes nobody.
 *
 * The assignee list is ranked exactly as the assign dialog ranks it
 * (BossModAssignForm.rankRoster / optionLabel): a second ranking would be a
 * second opinion about whether an assignment is sensible.
 */
const BossModTaskEditMode = (() => {
    const { h, clear } = BossModDom;
    const ASSIGN = BossModAssignForm;
    const FILES = BossModTaskEditFiles;

    const UNASSIGNED = '';
    /** Every edit-mode field: text at rest, the edit hairline while editing. */
    const FIELD = 'edit-field';

    /** A titled `.callout` in one of the shared tones. */
    function callout(tone, title, ...children) {
        return h('div', { class: 'callout', 'data-tone': tone },
            h('p', { class: 'callout-title' }, title), ...children);
    }

    /**
     * Build the edit state for one open task.
     *
     * @param {object} deps
     * @param {object} deps.task  The listed row the detail shows; the draft is
     *   diffed against it.
     * @param {object[]} deps.roster  Everyone who could be assigned; limited
     *   here to the task's floor when it has one.
     * @param {{nodes: HTMLElement[], count: number}} deps.childGroups  Subtask files
     *   (childDeliverableGroups), read-only after the add-row and counted.
     * @param {Function} deps.api  Authenticated fetch helper; the file picker
     *   lists the draft assignee's files through it.
     * @param {(dirty: boolean) => void} deps.onDirtyChange  `isDirty()` after every
     *   input, file row added, removed or repicked, and assignee change.
     * @param {(payload: object, opts: {confirmMismatch: boolean}) => Promise<object>} deps.onSave
     *   Sends the changed fields and resolves with the stored row; rejects
     *   with the typed Error of `update` in task-actions.js.
     * @param {(result: {saved: boolean, row?: object}) => void} deps.onLeave
     *   Edit mode has ended — saved (with the stored row), discarded, or
     *   saved with nothing changed. However it ended (✓, Enter, ✕, Esc,
     *   Reassign anyway), the detail repaints from here.
     * @returns {{titleInput: HTMLInputElement, assigneeControl: HTMLElement,
     *   descriptionSection: HTMLElement, deliverablesSection: HTMLElement,
     *   errorSlot: HTMLElement, begin: () => void, discard: () => void,
     *   save: () => Promise<{saved: boolean, row?: object}>,
     *   isEditing: () => boolean, isDirty: () => boolean,
     *   showError: (message: string) => void, destroy: () => void}}
     *   `save` never rejects: a refusal is rendered into `errorSlot`, and the
     *   draft and edit mode stay. `isDirty`: the draft would send something,
     *   or cannot be sent as it stands. `showError`: a status action's failure.
     * @throws {Error} When a dependency is missing.
     */
    function create(deps) {
        const { task, roster, childGroups, api, onSave, onLeave, onDirtyChange } = deps || {};
        if (!task || !task.id) throw new Error('[task-edit-mode] deps.task is required');
        if (!Array.isArray(roster)) throw new Error('[task-edit-mode] deps.roster must be an array');
        if (typeof api !== 'function') throw new Error('[task-edit-mode] deps.api is required');
        if (typeof onSave !== 'function') throw new Error('[task-edit-mode] deps.onSave is required');
        if (typeof onLeave !== 'function') throw new Error('[task-edit-mode] deps.onLeave is required');
        if (typeof onDirtyChange !== 'function') throw new Error('[task-edit-mode] deps.onDirtyChange is required');

        const agents = roster.filter((agent) => agent && (!task.floor_id || agent.floorId === task.floor_id));
        /** Suggested assignees the roster does not list, kept choosable once picked. */
        const extra = [];
        let editing = false;
        let saving = false;
        let destroyed = false;

        const titleInput = h('input', {
            class: `${FIELD} edit-field-title`, type: 'text', maxlength: '200', autocomplete: 'off',
            readonly: true, 'aria-label': 'Task title', 'aria-required': 'true',
            oninput: () => { fitTitle(); refreshAssignees(); changed(); },
            onkeydown: (event) => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                void send(false);
            },
        });
        const description = h('textarea', {
            class: `task-detail-instructions ${FIELD} edit-field-multiline`,
            maxlength: '4000', readonly: true, 'aria-label': 'Task description',
            placeholder: 'What the task asks for',
            oninput: () => { refreshAssignees(); changed(); },
        });
        /** Grows the description to its text, as the composer grows. */
        const grow = BossModAutoGrow.bind(description);

        /**
         * Assignee options, ranked for the words in the draft right now — the
         * assign dialog's rows (BossModAssignForm.rosterOptions), plus the
         * kept assignee and any `extra` agent this task needs offered.
         */
        function assigneeOptions() {
            const options = ASSIGN.rosterOptions(agents, titleInput.value, description.value);
            const listed = (id) => options.some((option) => option.value === id);
            // The current assignee stays choosable even when the roster no
            // longer lists them, so entering Edit mode never changes it.
            // Right after the backlog row, where the roster's rows start.
            if (task.assigned_to && !listed(task.assigned_to)) {
                const kept = task.assigned_to_name || task.assigned_to;
                options.splice(1, 0, { value: task.assigned_to, label: kept, short: kept });
            }
            extra.forEach((agent) => {
                const name = agent.name || agent.id;
                if (!listed(agent.id)) options.push({ value: agent.id, label: name, short: name });
            });
            return options;
        }

        const assignee = BossModMenuSelect.create({
            label: 'Assignee',
            options: ASSIGN.rosterOptions([], '', ''),
            value: UNASSIGNED,
            variant: 'field',
            // The control owns the choice; it is read back when Save runs.
            onChange: () => assigneeChanged(),
        });

        // The required files are picked as the DRAFT's assignee sees them.
        const files = FILES.create({
            task, childGroups, api, getAgentId: () => assignee.getValue(), onChange: () => changed(),
        });

        /** The draft's assignee moved: browsing follows it, and the draft is re-diffed. */
        function assigneeChanged() {
            files.syncAgent();
            changed();
        }

        function refreshAssignees() {
            assignee.setOptions(assigneeOptions());
        }

        const errorSlot = h('div', { class: 'task-detail-edit-error', role: 'alert' });
        errorSlot.hidden = true;

        /** Tell the detail whether the draft differs now. */
        const changed = () => onDirtyChange(isDirty());

        /** Show one callout in the error slot, or clear it with null. */
        function show(node) {
            clear(errorSlot);
            errorSlot.hidden = !node;
            if (node) errorSlot.append(node);
        }

        const descriptionSection = h('section', { class: 'task-detail-section' },
            h('p', { class: 'task-detail-heading' }, 'Task'), description);

        /** Put every field back to the task row; the draft is dropped. */
        function restore() {
            titleInput.value = task.title || '';
            fitTitle();
            description.value = task.description || '';
            extra.length = 0;
            assignee.setOptions(assigneeOptions(), task.assigned_to || UNASSIGNED);
            // After the assignee, so the rows' browsing follows the restored one.
            files.restore();
            show(null);
        }

        /** Size the head's title to its text, as the heading it replaces is. */
        function fitTitle() {
            titleInput.size = Math.max(titleInput.value.length, 1);
        }

        function setEditing(on) {
            editing = on;
            [titleInput, description].forEach((field) => { field.readOnly = !on; });
            files.setEditing(on);
        }

        /**
         * The draft as a PATCH body: only what differs from the task row.
         * @returns {{payload: object}|{error: string}} An error for a blank
         *   title.
         */
        function draft() {
            const title = titleInput.value.trim();
            if (!title) return { error: 'The title cannot be blank.' };
            const listed = files.files();
            const payload = {};
            if (title !== (task.title || '').trim()) payload.title = title;
            const text = description.value.trim() || null;
            if (text !== ((task.description || '').trim() || null)) payload.description = text;
            const chosen = assignee.getValue();
            if (chosen !== (task.assigned_to || UNASSIGNED)) payload.assigned_to = chosen || null;
            const before = FILES.currentFiles(task).map((file) => ({ type: 'file', ...file }));
            if (JSON.stringify(listed) !== JSON.stringify(before)) {
                payload.work_contract = listed.length ? { deliverables: listed } : null;
            }
            return { payload };
        }

        /** See @returns: a draft that would send something, or cannot be sent. */
        function isDirty() {
            const { payload, error } = draft();
            return Boolean(error) || Object.keys(payload).length > 0;
        }

        /** Choose a suggested assignee in the dropdown; the operator saves. */
        function pickSuggested(agent) {
            if (!extra.some((item) => item.id === agent.id)) extra.push(agent);
            assignee.setOptions(assigneeOptions(), agent.id);
            assigneeChanged();
        }

        /** The mismatch refusal, with its suggestions and the override. */
        function mismatch(err) {
            const actions = h('div', { class: 'callout-actions' },
                err.suggested.map((agent) => h('button', {
                    class: 'btn btn-sm', type: 'button', onclick: () => pickSuggested(agent),
                }, `${agent.name || 'Teammate'} — ${agent.role || 'No specialty'}`)),
                h('button', {
                    class: 'btn btn-sm', id: 'ct-edit-reassign-anyway', type: 'button',
                    onclick: () => { void send(true); },
                }, 'Reassign anyway'));
            return callout('warn', 'Specialty mismatch — nothing was saved',
                h('p', { class: 'callout-body' }, err.reason || 'That specialty does not match this work.'),
                actions);
        }

        /** Leave edit mode and tell the detail how it ended. */
        function leave(result) {
            setEditing(false);
            show(null);
            onLeave(result);
        }

        /**
         * Send the draft, or leave when nothing changed.
         * @param {boolean} confirmMismatch  Resend past a specialty warning.
         * @returns {Promise<{saved: boolean, row?: object}>} Never rejects.
         */
        async function send(confirmMismatch) {
            if (!editing || saving) return { saved: false };
            const { payload, error } = draft();
            if (error) {
                show(callout('alert', error));
                return { saved: false };
            }
            if (Object.keys(payload).length === 0) {
                leave({ saved: false });
                return { saved: false };
            }
            saving = true;
            let row;
            try {
                row = await onSave(payload, { confirmMismatch });
            } catch (err) {
                console.error('[task-edit-mode] save failed', err);
                if (destroyed) return { saved: false };
                show(err && err.kind === 'specialty_mismatch'
                    ? mismatch(err)
                    : callout('alert', 'Could not save the task',
                        h('p', { class: 'callout-body' }, (err && err.message) || 'The request failed.')));
                return { saved: false };
            } finally {
                saving = false;
            }
            // The panel closed while the save was in flight: nothing to repaint.
            if (!destroyed) leave({ saved: true, row });
            return { saved: true, row };
        }

        restore();

        return {
            titleInput,
            assigneeControl: assignee.element,
            descriptionSection,
            deliverablesSection: files.section,
            errorSlot,
            /** Enter edit mode; the title takes focus with its text selected. */
            begin() {
                if (editing) return;
                setEditing(true);
                grow.fit();
                titleInput.focus();
                if (titleInput.select) titleInput.select();
            },
            /** Put every field back and leave; refused while a save is in flight. */
            discard() {
                if (!editing || saving) return;
                restore();
                leave({ saved: false });
            },
            save: () => send(false),
            isEditing: () => editing,
            isDirty,
            /** A status action's refusal, in the slot a save's refusal uses. */
            showError: (message) => show(callout('alert', message)),
            /** Put the assignee panel away and unbind the description; this edit state is finished with. */
            destroy() {
                destroyed = true;
                assignee.destroy();
                grow.destroy();
            },
        };
    }

    return { create };
})();
