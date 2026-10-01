/**
 * BossMod AI — one schedule, as a layer over the agent's desk.
 *
 * View: the Enabled switch (saved on the spot), the facts (Repeats, Next run,
 * Last run with its task, Notify) and the instructions each run hands the
 * agent. Edit follows the task detail's contract (places/tasks/task-detail.js):
 * ✎ at rest, ✓ ✕ while editing, Esc discards, the title edited in the head.
 * The same nodes flip `readonly`, the rule becomes context/schedule-fields.js,
 * and ✓ PATCHes only what changed; a refusal is a `.callout` and the draft
 * stays. A new schedule opens in edit mode and ✓ POSTs. Delete sits behind
 * the head's `⋯` and a danger confirm (context/desk-actions.js's pattern).
 */
const BossModScheduleLayer = (() => {
    const { h, clear } = BossModDom;

    const NEW_TITLE = 'New schedule';
    const TITLE_PLACEHOLDER = 'Schedule title';
    const LABELS = Object.freeze({
        edit: 'Edit schedule', save: 'Save changes', discard: 'Discard changes', options: 'Schedule options',
    });
    /** Notify choices; the first is the server's default (ScheduleCreate). */
    const NOTIFY = Object.freeze([
        { value: 'completion_blocked', label: 'Done & blocked' },
        { value: 'all', label: 'Every update' },
        { value: 'none', label: 'Don’t notify' },
    ]);
    const RULE_KEYS = Object.freeze(['frequency', 'interval', 'times', 'weekdays', 'month_day', 'start_date']);

    /** A rule as one comparable string, whatever order its keys arrived in. */
    const ruleKey = (rule) => JSON.stringify(RULE_KEYS.map((key) => rule[key]));

    /** A titled `.callout` in one of the shared tones. */
    function callout(tone, title, text) {
        return h('div', { class: 'callout', 'data-tone': tone },
            h('p', { class: 'callout-title' }, title),
            text ? h('p', { class: 'callout-body' }, text) : null);
    }

    /**
     * Send one request; resolve with its JSON body, or null for a 204.
     * @throws {Error} (rejects) With the server's `detail` (a 422's messages
     *   joined), else the HTTP status — including a body that is not JSON.
     */
    async function request(api, url, init) {
        const res = await api(url, init);
        if (res.status === 204) return null;
        const body = await res.json().catch((err) => { throw new Error(`HTTP ${res.status}: ${err.message}`); });
        if (res.ok) return body;
        const detail = body && body.detail;
        if (typeof detail === 'string') throw new Error(detail);
        throw new Error(Array.isArray(detail) ? detail.map((item) => item && item.msg).join('; ') : `HTTP ${res.status}`);
    }

    const json = (method, payload) => ({
        method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });

    /** The Last run fact: what happened, when, and a link to the task a run created. */
    function lastRun(schedule, onOpenTask) {
        const when = BossModFormat.formatDateTime(schedule.last_occurrence_at);
        const lines = {
            fired: `Ran ${when}`,
            missed: `Missed ${when}: the computer was asleep or BossMod was not running`,
            skipped_open: `Skipped ${when}: the last run was still open`,
            skipped_vacation: `Skipped ${when}: the agent was on vacation`,
            failed: `Failed ${when}: ${schedule.last_outcome_detail || 'no detail was recorded'}`,
        };
        const text = schedule.last_outcome ? lines[schedule.last_outcome] : 'Not run yet';
        if (text === undefined) throw new Error(`[schedule-layer] unknown outcome "${schedule.last_outcome}"`);
        const status = schedule.last_task_status;
        return h('span', { class: 'schedule-last-run' }, text,
            schedule.last_task_id && status ? h('button', {
                class: 'btn-link schedule-open-task', type: 'button',
                onclick: () => onOpenTask(schedule.last_task_id),
            }, `Open task (${BossModTasksColumns.STATUS_LABELS[status] || status})`) : null);
    }

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
        /** The recurrence editor and the Notify dropdown while editing. */
        let fields = null;
        let notify = null;
        let menu = null;

        const titleInput = h('input', {
            class: 'task-detail-title-input task-detail-edit-field', type: 'text', maxlength: '200',
            autocomplete: 'off', readonly: true, 'aria-label': 'Schedule title', 'aria-required': 'true',
            placeholder: TITLE_PLACEHOLDER,
            oninput: () => fitTitle(),
            onkeydown: (event) => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                void save();
            },
        });
        const instructions = h('textarea', {
            class: 'task-detail-instructions task-detail-description-input task-detail-edit-field',
            rows: '4', maxlength: '4000', readonly: true, 'aria-label': 'Instructions',
            placeholder: 'What each run asks the agent to do',
        });
        const errorSlot = h('div', { class: 'schedule-layer-error', role: 'alert' });
        errorSlot.hidden = true;
        const column = h('div', { class: 'task-detail-column' });
        const enabled = BossModSwitch.create({
            label: 'Enabled', pressed: Boolean(current && current.enabled), onChange: (on) => { void setEnabled(on); },
        });

        const tool = (id, icon, cls, label, onclick, extra) => h('button', {
            class: cls, id, type: 'button', 'aria-label': label, 'data-tooltip': label, onclick, ...(extra || {}),
        }, h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }));
        const saveButton = tool('schedule-save', 'check', 'btn btn-sm conversation-action inline-rename-save',
            LABELS.save, () => { void save(); });
        const discardButton = tool('schedule-discard', 'x', 'btn btn-sm conversation-action inline-rename-cancel',
            LABELS.discard, () => discard());
        const pencilButton = tool('schedule-edit', 'pencil', 'header-icon-btn', LABELS.edit, () => enterEdit());
        const optionsButton = tool('schedule-options', 'ellipsis', 'header-icon-btn', LABELS.options,
            () => toggleOptions(), { 'aria-haspopup': 'dialog', 'aria-expanded': 'false' });

        /** Show one callout above the fields, or clear it with null. */
        function show(node) {
            clear(errorSlot);
            errorSlot.hidden = !node;
            if (node) errorSlot.append(node);
        }

        const section = (title, node) => h('section', { class: 'task-detail-section' },
            h('p', { class: 'task-detail-heading' }, title), node);

        /** Paint the column for the current state: the view, or edit mode. */
        function render() {
            if (editing) {
                column.replaceChildren(errorSlot, section('Rule', fields.element),
                    section('Notify', notify.element), section('Instructions', instructions));
            } else {
                enabled.set(current.enabled);
                const policy = NOTIFY.find((item) => item.value === current.notification_policy);
                column.replaceChildren(errorSlot, enabled.element, BossModFactList.create([
                    { label: 'Repeats', value: String(current.summary) },
                    { label: 'Next run', value: current.next_run_at ? BossModFormat.formatDateTime(current.next_run_at) : 'Off' },
                    { label: 'Last run', value: lastRun(current, onOpenTask) },
                    { label: 'Notify', value: policy ? policy.label : String(current.notification_policy) },
                ]), section('Instructions', instructions));
            }
            BossModIcons.paint(column, 'schedule-layer');
        }

        /** ✓ ✕ while editing; ✎ and ⋯ at rest; a new schedule has neither of those. */
        function syncHead() {
            saveButton.hidden = !editing;
            discardButton.hidden = !editing;
            pencilButton.hidden = editing || !current;
            optionsButton.hidden = editing || !current;
        }

        /** Size the head's title to its text, or to the placeholder while empty. */
        function fitTitle() {
            titleInput.size = Math.max(titleInput.value.length, TITLE_PLACEHOLDER.length);
        }

        /** Put the title and instructions back to the stored row. */
        function restoreText() {
            titleInput.value = current ? current.title : '';
            fitTitle();
            instructions.value = current ? current.instructions : '';
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
                if (fields) fields.destroy();
                if (notify) notify.destroy();
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
            fields = BossModScheduleFields.create({ rule: current ? current.recurrence : null, onChange: () => show(null) });
            notify = BossModMenuSelect.create({
                label: 'Notify', options: NOTIFY, value: current ? current.notification_policy : NOTIFY[0].value,
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
            fields.destroy();
            notify.destroy();
            [fields, notify] = [null, null];
            [titleInput, instructions].forEach((field) => { field.readOnly = true; });
            restoreText();
            show(null);
            panel.setTitleEditor(null);
            render();
            syncHead();
            pencilButton.focus();
        }

        /** ✕: a new schedule is abandoned (the layer closes); an edit is put back. */
        function discard() {
            if (!editing || saving) return;
            if (current) leaveEdit();
            else panel.close();
        }

        /**
         * The draft as a request: the whole body for a new schedule, only the
         * changed fields for an edit.
         * @returns {{error: string}|{payload: object}}
         */
        function draft() {
            const title = titleInput.value.trim();
            if (!title) return { error: 'The title cannot be blank.' };
            const text = instructions.value.trim();
            if (!text) return { error: 'Say what each run asks the agent to do.' };
            const read = fields.read();
            if (!read.ok) return { error: read.error };
            const policy = notify.getValue();
            if (!current) {
                return { payload: { title, instructions: text, recurrence: read.rule, notification_policy: policy } };
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
            if (error) return show(callout('alert', error));
            if (current && !Object.keys(payload).length) return leaveEdit();
            saving = true;
            saveButton.disabled = true;
            fields.setDisabled(true);
            let row;
            try {
                row = current
                    ? await request(api, `/api/schedules/${encodeURIComponent(current.id)}`, json('PATCH', payload))
                    : await request(api, `/api/agents/${encodeURIComponent(agentId)}/schedules`, json('POST', payload));
            } catch (err) {
                console.error('[schedule-layer] save failed', err);
                if (closed) return;
                show(callout('alert', 'Could not save the schedule', (err && err.message) || 'The request failed.'));
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

        /** The switch saves on the spot; a refusal flips it back and says why. */
        async function setEnabled(on) {
            show(null);
            let row;
            try {
                row = await request(api, `/api/schedules/${encodeURIComponent(current.id)}`, json('PATCH', { enabled: on }));
            } catch (err) {
                console.error('[schedule-layer] could not change Enabled', err);
                if (closed) return;
                enabled.set(!on);
                show(callout('alert', on ? 'Could not turn the schedule on' : 'Could not turn the schedule off',
                    (err && err.message) || 'The request failed.'));
                return;
            }
            if (closed) return;
            current = row;
            if (!editing) render();
            onChanged(row);
        }

        function toggleOptions() {
            if (menu) {
                menu.close();
                return;
            }
            menu = BossModMenu.createMenu({
                anchor: optionsButton, label: LABELS.options, container: optionsButton.closest('.modal-head'),
                items: [h('div', { class: 'menu-actions' },
                    h('button', {
                        class: 'menu-action', id: 'schedule-delete', type: 'button', 'data-tone': 'danger',
                        onclick: () => { menu.close(); confirmDelete(); },
                    }, h('i', { 'data-lucide': 'trash-2', 'aria-hidden': 'true' }), 'Delete schedule…'))],
                onClose: () => {
                    menu = null;
                    optionsButton.setAttribute('aria-expanded', 'false');
                },
            });
            optionsButton.setAttribute('aria-expanded', 'true');
            BossModIcons.paint(menu.element, 'schedule-layer');
        }

        /** Nothing reaches the API until the operator confirms; Cancel is the focused default. */
        function confirmDelete() {
            const who = agentName();
            BossModOverlays.createModal({
                title: 'Delete this schedule?',
                closeOnBackdrop: true,
                body: `“${current.title}” stops running${who ? ` for ${who}` : ''}. `
                    + 'Tasks its past runs created stay on the board.',
                actions: [
                    { label: 'Delete schedule', tone: 'danger', onSelect: () => { void remove(); } },
                    { label: 'Cancel', tone: 'quiet' },
                ],
            });
        }

        async function remove() {
            show(null);
            try {
                await request(api, `/api/schedules/${encodeURIComponent(current.id)}`, { method: 'DELETE' });
            } catch (err) {
                console.error('[schedule-layer] delete failed', err);
                if (!closed) show(callout('alert', 'Could not delete the schedule', (err && err.message) || 'The request failed.'));
                return;
            }
            if (!closed) panel.close();
            onChanged(null);
        }

        restoreText();
        if (current) {
            render();
            syncHead();
        } else {
            enterEdit();
        }
        BossModIcons.paint(panel.element, 'schedule-layer');

        return { close: () => panel.closeFrom() };
    }

    return { open };
})();
