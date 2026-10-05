/**
 * BossMod AI — the task detail dialog.
 *
 * A panel modal whose body is one centred column: the status line, the facts,
 * the one callout the state calls for, the task itself, the deliverables, the
 * subtasks, what counts as done, and the activity. The title is the modal's
 * own head and is not repeated below it. The sections are pure builders in
 * task-detail-sections.js; this file composes them, owns the modal, and owns
 * the head's tools: the pencil that enters Edit mode, and its ✓ and ✕.
 *
 * Edit mode (task-edit-mode.js) makes the same panel editable in place; one
 * renderColumn() paints the view, the edit and every repaint after a change.
 * In Edit mode the status line also carries the status actions — Resume,
 * Mark complete…, Cancel task… — disabled while the draft holds unsaved
 * edits, so a status change never drops or saves typed text. Every change
 * resolves with the stored row: the detail repaints from it in place and
 * re-reads its Activity. The detail still calls no task route itself: it is
 * handed `actions` (BossModTaskActions) at construction, so each change a
 * task can take has exactly one place in Tasks that performs it.
 */
const BossModTaskDetail = (() => {
    const { h } = BossModDom;
    const COLUMNS = BossModTasksColumns;
    const SECTIONS = BossModTaskDetailSections;

    /** The pencil's accessible name and tooltip. */
    const EDIT_LABEL = 'Edit mode';
    /** Why the status actions are disabled while the draft differs. */
    const DIRTY_HINT = 'Save or discard your changes first';

    /**
     * Open one task in the shared modal.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string} deps.taskId
     * @param {object[]} deps.tasks  The place's current list, used to resolve
     *   the parent and children without a second request.
     * @param {(agentId: string) => (string|undefined)} deps.colorOf  Roster
     *   colours for the avatars in the facts and the activity.
     * @param {(taskId: string) => void} deps.onNavigate  Opens a related task
     *   as a layer over this one.
     * @param {{cancel: Function, complete: Function, resume: Function,
     *   update: Function, roster: Function}} deps.actions  The caller owns
     *   completing, cancelling, resuming and updating (the Tasks place, or
     *   the desk that opened this over itself); this panel only asks, and
     *   repaints from the row each one resolves with.
     * @param {(task: object) => void} deps.onOpenChat  Leaves for the task's
     *   conversation.
     * @param {() => void} [deps.onClose]
     * @returns {{close: () => void}}
     * @throws {Error} When a dependency is missing, or when the task id is not
     *   in the list — opening a panel for a task nobody can name would show an
     *   empty modal with no explanation.
     */
    function openTaskDetail(deps) {
        const {
            api, taskId, tasks, colorOf, onNavigate, actions, onOpenChat, onClose,
        } = deps || {};
        if (typeof api !== 'function') throw new Error('[task-detail] deps.api is required');
        if (typeof colorOf !== 'function') throw new Error('[task-detail] deps.colorOf is required');
        if (typeof onNavigate !== 'function') throw new Error('[task-detail] deps.onNavigate is required');
        if (!actions || typeof actions.cancel !== 'function' || typeof actions.complete !== 'function'
            || typeof actions.resume !== 'function' || typeof actions.update !== 'function'
            || typeof actions.roster !== 'function') {
            throw new Error('[task-detail] deps.actions must be a BossModTaskActions');
        }
        if (typeof onOpenChat !== 'function') throw new Error('[task-detail] deps.onOpenChat is required');
        /** The row on screen; each change replaces it with the one the server stored. */
        let task = (tasks || []).find((item) => item.id === taskId);
        if (!task) throw new Error(`[task-detail] no task "${taskId}" in the current list`);

        const events = BossModTaskEvents.createTaskEvents({ api, taskId: task.id, colorOf });
        const column = h('div', { class: 'task-detail-column' });
        const body = h('div', { class: 'task-detail' }, column);
        /** The live edit state while Edit mode is on, else null. */
        let editMode = null;
        /** The modal, once it is on screen. */
        let panel = null;
        /** The status actions on screen while editing, else null. */
        let status = null;
        /** The modal has closed: a late answer repaints nothing. */
        let closed = false;

        /**
         * Paint the column for `current`, as a view or in Edit mode. The one
         * path for opening, entering and leaving Edit mode, and every
         * repaint after a save or a status action.
         * @param {object} current
         * @param {boolean} editing
         * @returns {{measure: () => void}|null} The instructions' clamp,
         *   already measured unless the modal is not yet on screen.
         */
        function renderColumn(current, editing) {
            const children = tasks.filter((item) => item.parent_task_id === current.id);
            const instructions = editing ? null : SECTIONS.instructions(current);
            const assigneeValue = editing ? editMode.assigneeControl : undefined;
            status = editing ? statusActions(current) : null;
            column.replaceChildren(...[
                SECTIONS.statusLine(current, { actions: status && status.element }),
                editing ? editMode.errorSlot : null,
                SECTIONS.facts(current, { tasks, colorOf, onNavigate, assigneeValue }),
                SECTIONS.callout(current, { onOpenChat }),
                editing ? editMode.descriptionSection : (instructions && instructions.element),
                // In Edit mode one Deliverables section holds the task's own
                // editable rows and its subtasks' read-only ones.
                editing ? editMode.deliverablesSection : SECTIONS.deliverables(current, children, api),
                SECTIONS.subtasks(children, onNavigate),
                SECTIONS.doneContract(current),
                events.element,
            ].filter(Boolean));
            // The clamp is measured on screen; the first paint is measured
            // once the modal has mounted it.
            if (panel && instructions) instructions.measure();
            BossModIcons.paint(column, 'task-detail');
            return instructions;
        }

        /**
         * The status line's actions for one render of Edit mode: Resume and
         * Mark complete where the server flags them, Cancel for any open
         * task, and the hint that says why they are disabled.
         * @param {object} current
         * @returns {{element: HTMLElement, sync: (busy: boolean) => void,
         *   resync: () => void}} `sync` disables every button while `busy`
         *   or while the draft is dirty, and shows the hint only for the
         *   latter; `resync` re-reads the draft, keeping `busy` as it was.
         */
        function statusActions(current) {
            const hint = h('span', { class: 'task-detail-meta task-detail-status-hint' }, DIRTY_HINT);
            const button = (id, label, run, danger) => h('button', {
                class: danger ? 'btn btn-sm btn-danger' : 'btn btn-sm', id, type: 'button',
                onclick: () => { void runStatus(run); },
            }, label);
            const buttons = [
                current.operator_can_resume ? button('ct-resume-task-btn', 'Resume', actions.resume) : null,
                current.operator_can_complete
                    ? button('ct-complete-task-btn', 'Mark complete…', actions.complete) : null,
                button('ct-cancel-task-btn', 'Cancel task…', actions.cancel, true),
            ].filter(Boolean);
            let busy = false;
            const sync = (nowBusy) => {
                busy = nowBusy;
                const dirty = Boolean(editMode) && editMode.isDirty();
                hint.hidden = !dirty;
                buttons.forEach((item) => { item.disabled = busy || dirty; });
            };
            sync(false);
            return {
                element: h('div', { class: 'task-detail-status-actions' }, hint, ...buttons),
                sync,
                resync: () => sync(busy),
            };
        }

        /** An icon-only head tool: the inline rename's ✓/✕ pair and tones. */
        const editTool = (id, icon, tone, label, onclick) => h('button', {
            class: `btn btn-sm conversation-action ${tone}`,
            id,
            type: 'button',
            'aria-label': label,
            'data-tooltip': label,
            onclick,
        }, h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }));

        // A task that opens finished can no longer be edited, which is all
        // the head's tools do — so it gets none rather than a dead pencil.
        const openedOpen = !COLUMNS.isTerminal(task.status);
        const saveButton = editTool('ct-edit-save', 'check', 'inline-rename-save', 'Save changes',
            () => { void editMode.save(); });
        const discardButton = editTool('ct-edit-discard', 'x', 'inline-rename-cancel', 'Discard changes',
            () => editMode.discard());
        const pencilButton = h('button', {
            class: 'header-icon-btn',
            id: 'ct-edit-mode-btn',
            type: 'button',
            'aria-label': EDIT_LABEL,
            'data-tooltip': EDIT_LABEL,
            onclick: () => enterEditMode(),
        }, h('i', { 'data-lucide': 'pencil', 'aria-hidden': 'true' }));

        /**
         * The head in or out of Edit mode: `[✎]` at rest, `[✓ ✕]` while
         * editing, and nothing once the task has finished. Hidden, not
         * removed, so the pencil's slot is where focus comes back to.
         * @param {boolean} editing
         * @returns {void}
         */
        function syncHead(editing) {
            const finished = COLUMNS.isTerminal(task.status);
            saveButton.hidden = !editing || finished;
            discardButton.hidden = !editing || finished;
            pencilButton.hidden = editing || finished;
        }

        const firstInstructions = renderColumn(task, false);
        syncHead(false);
        panel = BossModOverlays.createModal({
            title: task.title || 'Task',
            body,
            size: 'panel',
            tools: openedOpen ? [saveButton, discardButton, pencilButton] : [],
            // Mark complete and Cancel each open their own layer; the one
            // thing an outside click could lose is an Edit mode draft.
            actions: [],
            closeOnBackdrop: () => !(editMode && editMode.isEditing()),
            onClose: () => {
                closed = true;
                if (editMode) editMode.destroy();
                events.destroy();
                if (onClose) onClose();
            },
        });

        /**
         * Esc while editing discards instead of closing: the frame's Esc
         * listens on `document`, so stopping it here means it never hears it.
         * @param {KeyboardEvent} event
         * @returns {void}
         */
        function onEditKeydown(event) {
            if (event.key !== 'Escape' || !editMode || !editMode.isEditing()) return;
            // An open dropdown in the panel (the assignee) answers its own Esc.
            if (event.target && event.target.closest && event.target.closest('.menu')) return;
            event.preventDefault();
            event.stopPropagation();
            editMode.discard();
        }

        /**
         * PATCH the draft through the caller's actions, with ✓ disabled while
         * it is in flight.
         * @returns {Promise<object>} The stored row.
         */
        async function sendUpdate(payload) {
            saveButton.disabled = true;
            try {
                return await actions.update(task.id, payload);
            } finally {
                saveButton.disabled = false;
            }
        }

        /**
         * Leave Edit mode (if it is on) and repaint the view in place, from
         * `row` when the server stored a change. Focus goes back to the
         * pencil, or to the frame's ✕ once the task has finished.
         * @param {object|null} row
         * @returns {void}
         */
        function repaint(row) {
            if (editMode) {
                panel.element.removeEventListener('keydown', onEditKeydown);
                editMode.destroy();
                editMode = null;
            }
            if (row) {
                const renamed = row.title !== task.title;
                task = row;
                if (renamed) panel.setTitle(task.title || 'Task');
            }
            panel.setTitleEditor(null);
            renderColumn(task, false);
            syncHead(false);
            const target = COLUMNS.isTerminal(task.status)
                ? panel.element.querySelector('.modal-close') : pencilButton;
            if (target) target.focus();
        }

        /** Edit mode ended; a save repaints from the stored row and re-reads Activity. */
        function leaveEditMode(result) {
            repaint(result.saved ? result.row : null);
            if (result.saved) void events.refresh();
        }

        /**
         * Run one status action. A row repaints the detail in place and
         * re-reads Activity; null (the operator backed out of its
         * confirmation) keeps Edit mode as it was; a refusal is shown in
         * Edit mode's error slot with the draft kept.
         * @param {(task: object) => Promise<object|null>} run
         * @returns {Promise<void>} Never rejects.
         */
        async function runStatus(run) {
            const own = status;
            own.sync(true);
            let row;
            try {
                row = await run(task);
            } catch (err) {
                console.error('[task-detail] a status action failed', err);
                if (closed || !editMode) return;
                editMode.showError((err && err.message) || 'The request failed.');
                own.sync(false);
                return;
            }
            if (closed) return;
            if (!row) {
                if (status === own) own.sync(false);
                return;
            }
            repaint(row);
            void events.refresh();
        }

        /** Make the fields editable where they sit; see task-edit-mode.js. */
        function enterEditMode() {
            const children = tasks.filter((item) => item.parent_task_id === task.id);
            editMode = BossModTaskEditMode.create({
                task,
                roster: actions.roster(),
                childGroups: SECTIONS.childDeliverableGroups(children, api),
                api,
                onSave: sendUpdate,
                onLeave: leaveEditMode,
                onDirtyChange: () => { if (status) status.resync(); },
            });
            renderColumn(task, true);
            panel.setTitleEditor(editMode.titleInput);
            syncHead(true);
            panel.element.addEventListener('keydown', onEditKeydown);
            editMode.begin();
        }

        // Measured once the modal is on screen, which is the first moment the
        // instructions have a height to compare against.
        if (firstInstructions) firstInstructions.measure();
        BossModIcons.paint(panel.element, 'task-detail');

        return { close: panel.close };
    }

    return { openTaskDetail };
})();
