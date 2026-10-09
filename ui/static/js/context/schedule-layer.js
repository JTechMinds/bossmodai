/**
 * BossMod AI — one schedule, as a layer over the agent's desk.
 *
 * View: the Enabled switch (saved on the spot), the facts (Repeats, Next run,
 * Last run with its task, Notify) and the instructions each run hands the
 * agent. Edit follows the task detail's contract (places/tasks/task-detail.js):
 * ✎ at rest, ✓ ✕ while editing, Esc discards, the title edited in the head.
 * Edit happens in place: the view keeps its layout and the facts become
 * editable where they sit — Repeats holds the recurrence editor
 * (context/schedule-fields.js), Next run the draft's upcoming runs
 * (context/schedule-preview.js), Notify its dropdown — and the title and
 * instructions flip `readonly` (the shared `.edit-field` look; the
 * instructions grow with their text through core/autogrow.js). ✓ PATCHes
 * only what changed; a refusal is a `.callout` and the draft stays. A new
 * schedule opens in edit mode with the Enabled switch (default on, sent
 * with the POST) and ✓ POSTs. A saved schedule has
 * Run now beside Enabled (schedule-view.js's control). Delete sits behind
 * the head's `⋯` and a danger confirm (context/desk-actions.js's pattern).
 */
const BossModScheduleLayer = (() => {
    const { h, clear } = BossModDom;

    const NEW_TITLE = 'New schedule';
    const TITLE_PLACEHOLDER = 'Schedule title';
    const LABELS = Object.freeze({
        edit: 'Edit schedule', save: 'Save changes', discard: 'Discard changes', options: 'Schedule options',
    });
    /** How many upcoming runs the editor's preview lists (a presentation choice; the server allows 1..20). */
    const PREVIEW_COUNT = 5;
    const VIEW = BossModScheduleView;
    const API = BossModScheduleApi;
    const RULE_KEYS = Object.freeze([
        'frequency', 'interval', 'times', 'every_minutes', 'window_start', 'window_end',
        'weekdays', 'month_day', 'start_date',
    ]);

    /** A rule as one comparable string, whatever order its keys arrived in. */
    const ruleKey = (rule) => JSON.stringify(RULE_KEYS.map((key) => rule[key]));

    /**
     * Open one schedule, or a new one, over the desk.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {string} deps.agentId  Whose schedule; a new one is created for them.
     * @param {() => string} deps.agentName  For the delete confirmation.
     * @param {object|null} deps.schedule  A ScheduleView, or null for a new one.
     * @param {(row: object|null) => void} deps.onChanged  After every stored
     *   change: the new row, or null once deleted.
     * @param {(taskId: string) => void} deps.onOpenTask  Opens a run's task.
     * @param {() => void} [deps.onClose]  The layer closed, however it closed.
     * @returns {{close: () => void}} `close` also closes anything this layer
     *   opened over itself (its confirmation).
     * @throws {Error} When a dependency is missing.
     */
    function open(deps) {
        const { api, agentId, agentName, schedule, onChanged, onOpenTask, onClose } = deps || {};
        if (typeof api !== 'function') throw new Error('[schedule-layer] deps.api is required');
        if (!agentId) throw new Error('[schedule-layer] deps.agentId is required');
        if (typeof agentName !== 'function') throw new Error('[schedule-layer] deps.agentName is required');
        if (typeof onChanged !== 'function') throw new Error('[schedule-layer] deps.onChanged is required');
        if (typeof onOpenTask !== 'function') throw new Error('[schedule-layer] deps.onOpenTask is required');

        /** The stored row on screen, or null until a new schedule is saved. */
        let current = schedule || null;
        let editing = false;
        let saving = false;
        let closed = false;
        /** The recurrence editor, the Notify dropdown and the preview while editing; the open `⋯` menu. */
        let [fields, notify, preview, menu] = [null, null, null, null];
        /** Run now, built once the schedule is saved. */
        let runNow = null;
        /** The switches' values for a schedule not yet created (it has no row to PATCH yet). */
        const draftFlags = { enabled: true, agent_can_change: false };

        const titleInput = h('input', {
            class: 'edit-field edit-field-title', type: 'text', maxlength: '200',
            autocomplete: 'off', readonly: true, 'aria-label': 'Schedule title', 'aria-required': 'true',
            placeholder: TITLE_PLACEHOLDER,
            oninput: () => fitTitle(),
            onkeydown: (event) => { if (event.key === 'Enter') { event.preventDefault(); void save(); } },
        });
        const instructions = h('textarea', {
            class: 'edit-field edit-field-multiline schedule-instructions',
            readonly: true, 'aria-label': 'Instructions',
            placeholder: 'What each run asks the agent to do',
        });
        /** Sizes the instructions to their text, in view mode too, so a long one is never clipped. */
        const grow = BossModAutoGrow.bind(instructions);
        const errorSlot = h('div', { class: 'schedule-layer-error', role: 'alert' });
        errorSlot.hidden = true;
        const column = h('div', { class: 'task-detail-column' });
        /** A switch saved on the spot for a stored schedule, held in draftFlags before that. */
        const flagSwitch = (field, label) => BossModSwitch.create({
            label, pressed: current ? current[field] : draftFlags[field],
            onChange: (on) => { if (current) void saveFlag(field, label, on); else draftFlags[field] = on; },
        });
        const switches = {
            enabled: flagSwitch('enabled', 'Enabled'),
            agent_can_change: flagSwitch('agent_can_change', 'Agent can manage this task'),
        };
        const switchElements = () => [switches.enabled.element, switches.agent_can_change.element];

        const tool = (id, icon, cls, label, onclick) => h('button', {
            class: cls, id, type: 'button', 'aria-label': label, 'data-tooltip': label, onclick,
        }, h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }));
        const saveButton = tool('schedule-save', 'check', 'btn btn-sm conversation-action inline-rename-save',
            LABELS.save, () => { void save(); });
        const discardButton = tool('schedule-discard', 'x', 'btn btn-sm conversation-action inline-rename-cancel',
            LABELS.discard, () => discard());
        const pencilButton = tool('schedule-edit', 'pencil', 'header-icon-btn', LABELS.edit, () => enterEdit());
        const optionsButton = BossModMenuButton.createTrigger({
            id: 'schedule-options', label: LABELS.options, size: 'header', onClick: () => toggleOptions(),
        });

        /** Show one callout above the fields, or clear it with null. */
        function show(node) {
            clear(errorSlot);
            errorSlot.hidden = !node;
            if (node) errorSlot.append(node);
        }

        const section = (title, node) => h('section', { class: 'task-detail-section' },
            h('p', { class: 'task-detail-heading' }, title), node);

        /** Paint the column for the current state: the view, or edit mode in the view's layout. */
        function render() {
            if (editing) {
                column.replaceChildren(errorSlot, ...(current ? [] : switchElements()),
                    VIEW.facts(current, {
                        onOpenTask,
                        edit: { repeats: fields.element, nextRun: preview.element, notify: notify.element },
                    }),
                    section('Instructions', instructions));
            } else {
                Object.keys(switches).forEach((field) => switches[field].set(current[field]));
                runNow = runNow || VIEW.runNowControl({
                    api, scheduleId: () => current.id, onOpenTask,
                    onRan: (row) => { current = row; if (!editing) render(); onChanged(row); },
                });
                column.replaceChildren(errorSlot, h('div', { class: 'schedule-run-row' }, ...switchElements(), runNow.element),
                    VIEW.facts(current, { onOpenTask }), section('Instructions', instructions));
            }
            BossModIcons.paint(column, 'schedule-layer');
            // Measured once it is in the column; a detached node has no height.
            grow.fit();
        }

        /** ✓ ✕ while editing; ✎ and ⋯ at rest; a new schedule has neither of those. */
        function syncHead() {
            [saveButton, discardButton].forEach((button) => { button.hidden = !editing; });
            [pencilButton, optionsButton].forEach((button) => { button.hidden = editing || !current; });
        }

        /** Size the head's title to its text, or to the placeholder while empty. */
        const fitTitle = () => { titleInput.size = Math.max(titleInput.value.length, TITLE_PLACEHOLDER.length); };

        /** Put the title and instructions back to the stored row. */
        function restoreText() {
            titleInput.value = current ? current.title : '';
            fitTitle();
            instructions.value = current ? current.instructions : '';
            grow.fit();
        }

        const panel = BossModOverlays.createModal({
            title: current ? current.title : NEW_TITLE,
            body: h('div', { class: 'schedule-layer' }, column),
            size: 'panel',
            tools: [saveButton, discardButton, pencilButton, optionsButton],
            actions: [],
            // The one thing an outside click could lose is a draft.
            closeOnBackdrop: () => !editing,
            onClose: () => {
                closed = true;
                if (menu) menu.close();
                [fields, notify, preview, runNow].forEach((control) => { if (control) control.destroy(); });
                grow.destroy();
                if (onClose) onClose();
            },
        });

        /** Esc while editing a stored schedule discards; for a new one it closes, as ✕ does. */
        function onEditKeydown(event) {
            if (event.key !== 'Escape' || !editing || !current) return;
            // An open dropdown in the panel answers its own Esc.
            if (event.target && event.target.closest && event.target.closest('.menu')) return;
            event.preventDefault();
            event.stopPropagation();
            discard();
        }
        panel.element.addEventListener('keydown', onEditKeydown);

        function enterEdit() {
            if (editing) return;
            editing = true;
            restoreText();
            [titleInput, instructions].forEach((field) => { field.readOnly = false; });
            fields = BossModScheduleFields.create({
                rule: current ? current.recurrence : null, onChange: () => { show(null); refreshPreview(); },
            });
            preview = BossModSchedulePreview.create({ api, count: PREVIEW_COUNT });
            refreshPreview();
            notify = BossModMenuSelect.create({
                label: 'Notify', options: VIEW.NOTIFY, value: current ? current.notification_policy : VIEW.NOTIFY[0].value,
                variant: 'field', onChange: () => show(null),
            });
            show(null);
            panel.setTitleEditor(titleInput);
            render();
            syncHead();
            titleInput.focus();
        }

        function leaveEdit() {
            editing = false;
            [fields, notify, preview].forEach((control) => control.destroy());
            [fields, notify, preview] = [null, null, null];
            [titleInput, instructions].forEach((field) => { field.readOnly = true; });
            restoreText();
            show(null);
            panel.setTitleEditor(null);
            render();
            syncHead();
            pencilButton.focus();
        }

        /** Feed the preview the editor's read: a rule, an unfinished draft, or why it is wrong. */
        function refreshPreview() {
            preview.update(fields.read());
        }

        /** ✕: a new schedule is abandoned (the layer closes); an edit is put back. */
        function discard() {
            if (!editing || saving) return;
            if (current) leaveEdit();
            else panel.close();
        }

        /** The draft as `{payload}` (all of a new schedule, the changes of an edit) or `{error}`. */
        function draft() {
            const title = titleInput.value.trim();
            if (!title) return { error: 'The title cannot be blank.' };
            const text = instructions.value.trim();
            if (!text) return { error: 'Say what each run asks the agent to do.' };
            const read = fields.read();
            if (!read.ok) return { error: read.error };
            const policy = notify.getValue();
            if (!current) {
                return { payload: {
                    title, instructions: text, recurrence: read.rule, notification_policy: policy, ...draftFlags,
                } };
            }
            const payload = {};
            if (title !== current.title) payload.title = title;
            if (text !== current.instructions) payload.instructions = text;
            if (ruleKey(read.rule) !== ruleKey(current.recurrence)) payload.recurrence = read.rule;
            if (policy !== current.notification_policy) payload.notification_policy = policy;
            return { payload };
        }

        /** ✓ and Enter: send the draft, or leave when nothing changed. Never rejects. */
        async function save() {
            if (!editing || saving) return;
            const { payload, error } = draft();
            if (error) return show(VIEW.callout('alert', error));
            if (current && !Object.keys(payload).length) return leaveEdit();
            saving = true;
            saveButton.disabled = true;
            fields.setDisabled(true);
            let row;
            try {
                row = current ? await API.update(api, current.id, payload) : await API.create(api, agentId, payload);
            } catch (err) {
                console.error('[schedule-layer] save failed', err);
                if (closed) return;
                show(VIEW.callout('alert', 'Could not save the schedule', (err && err.message) || 'The request failed.'));
                return;
            } finally {
                saving = false;
                saveButton.disabled = false;
                if (fields) fields.setDisabled(false);
            }
            if (closed) return;
            current = row;
            panel.setTitle(row.title);
            leaveEdit();
            onChanged(row);
        }

        /** A switch saves on the spot (`enabled`, `agent_can_change`); a refusal flips it back and says why. */
        async function saveFlag(field, label, on) {
            show(null);
            let row;
            try {
                row = await API.update(api, current.id, { [field]: on });
            } catch (err) {
                console.error(`[schedule-layer] could not change ${field}`, err);
                if (closed) return;
                switches[field].set(!on);
                show(VIEW.callout('alert', `Could not turn “${label}” ${on ? 'on' : 'off'}`,
                    (err && err.message) || 'The request failed.'));
                return;
            }
            if (closed) return;
            current = row;
            if (!editing) render();
            onChanged(row);
        }

        /** ⋯ toggles the options menu; its one row asks before anything is deleted. */
        function toggleOptions() {
            if (menu) return menu.close();
            menu = VIEW.optionsMenu({
                anchor: optionsButton, label: LABELS.options, onClose: () => { menu = null; },
                onDelete: () => VIEW.confirmDelete({
                    title: current.title, agentName: agentName(), onConfirm: () => { void remove(); },
                }),
            });
        }

        async function remove() {
            show(null);
            try {
                await API.remove(api, current.id);
            } catch (err) {
                console.error('[schedule-layer] delete failed', err);
                if (!closed) show(VIEW.callout('alert', 'Could not delete the schedule', (err && err.message) || 'The request failed.'));
                return;
            }
            if (!closed) panel.close();
            onChanged(null);
        }

        restoreText();
        if (!current) enterEdit();
        else { render(); syncHead(); }
        BossModIcons.paint(panel.element, 'schedule-layer');

        return { close: () => panel.closeFrom() };
    }

    return { open };
})();
