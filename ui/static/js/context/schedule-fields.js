/**
 * BossMod AI — the recurrence editor of a schedule, set up the way a phone
 * reminder is: Repeats (daily, weekly, monthly), every N, on which weekdays
 * or on which day of the month, starting a date — which DAYS — and Times —
 * when on each of those days: "At set times" (one or more) or "Repeating"
 * every N minutes or hours from one time to another, the same day.
 *
 * Pure DOM, no API: context/schedule-layer.js mounts it as the Repeats
 * fact's value for a new schedule and for an edit, and reads the rule back
 * when ✓ is pressed. `read()` checks what the server would refuse
 * (core/models/schedule.py RecurrenceRule) so a rule that cannot be saved is
 * said here and never sent.
 *
 * It reads as compact sentence rows in the Edit-mode language (the hairline
 * fields of core/inline-rename.js's "one control, two states"), with the
 * connecting words quieter than the controls: Repeats, Day of month and the
 * repeat unit are BossModMenuSelect's field look (never a native <select>);
 * the start date is core/date-field.js and every time core/time-field.js
 * (the webview's native date and time inputs have no picker); the numbers
 * are text inputs with a numeric keyboard (no spinner).
 */
const BossModScheduleFields = (() => {
    const { h } = BossModDom;

    /** Each frequency: its label, its unit, and its interval cap (the server's). */
    const FREQUENCIES = Object.freeze([
        { value: 'daily', label: 'Daily', unit: ['day', 'days'], max: 365 },
        { value: 'weekly', label: 'Weekly', unit: ['week', 'weeks'], max: 52 },
        { value: 'monthly', label: 'Monthly', unit: ['month', 'months'], max: 12 },
    ]);
    const WEEKDAYS = Object.freeze(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']);
    const MAX_TIMES = 12;
    /** "Repeating" units: minutes up to 720 (12 hours), hours up to 12. */
    const REPEAT_UNITS = Object.freeze([
        { value: 'minutes', label: 'minutes', factor: 1, max: 720 },
        { value: 'hours', label: 'hours', factor: 60, max: 12 },
    ]);
    /** A new "Repeating" block starts at every 30 minutes, 09:00 to 17:00. */
    const REPEAT_DEFAULTS = Object.freeze({ every: 30, unit: 'minutes', start: '09:00', end: '17:00' });

    /** Day of month choices, 1–31, read as "1st" … "31st". */
    const MONTH_DAYS = Object.freeze(Array.from({ length: 31 }, (_, index) => {
        const day = index + 1;
        const teen = day % 100 >= 11 && day % 100 <= 13;
        const suffix = teen ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[day % 10] || 'th');
        return { value: String(day), label: `${day}${suffix}` };
    }));

    /** Distinct ids per editor, so two open editors never share a label target. */
    let instances = 0;

    /** @returns {object} The FREQUENCIES entry for `value`. */
    function frequencyOf(value) {
        const found = FREQUENCIES.find((item) => item.value === value);
        if (!found) throw new Error(`[schedule-fields] unknown frequency "${value}"`);
        return found;
    }

    /** @returns {number|null} `text` as a whole number in [min, max], else null. */
    function wholeIn(text, min, max) {
        const value = String(text).trim();
        if (!/^\d+$/.test(value)) return null;
        const n = Number(value);
        return n >= min && n <= max ? n : null;
    }

    /** A connecting word between controls ("every", "from", "to"). */
    const unitWord = (text, id) => h('span', { class: 'schedule-unit', id: id || null }, text);

    /** A short whole-number field: text with a numeric keyboard, so no spinner. */
    const numberField = (cls, label, value, onInput) => {
        const input = h('input', {
            class: `edit-field ${cls}`, type: 'text', inputmode: 'numeric', autocomplete: 'off',
            'aria-label': label, oninput: onInput,
        });
        input.value = String(value);
        return input;
    };

    /**
     * Build the editor.
     *
     * @param {object} deps
     * @param {object|null} [deps.rule]  The stored rule to start from; a new
     *   schedule starts daily, every 1, with one empty time, from today.
     * @param {() => void} deps.onChange  Any control changed.
     * @returns {{element: HTMLElement,
     *   read: () => ({ok: true, rule: object}|{ok: false, error: string}),
     *   setDisabled: (disabled: boolean) => void, destroy: () => void}}
     *   `read` returns the rule in the server's shape — exactly one time
     *   mode populated (`times` sorted with the repeat fields null, or
     *   `times` empty with `every_minutes` and the window set), `weekdays`
     *   sorted and empty unless weekly, `month_day` null unless monthly —
     *   or the first problem as a sentence.
     * @throws {Error} When onChange is missing or the rule has an unknown frequency.
     */
    function create(deps) {
        const { rule, onChange } = deps || {};
        if (typeof onChange !== 'function') throw new Error('[schedule-fields] deps.onChange is required');
        const start = rule || null;
        const id = `schedule-fields-${instances += 1}`;
        let frequency = frequencyOf(start ? start.frequency : 'daily').value;

        const repeats = BossModMenuSelect.create({
            label: 'Repeats',
            options: FREQUENCIES.map((item) => ({ value: item.value, label: item.label })),
            value: frequency,
            variant: 'field',
            onChange: (value) => { frequency = value; sync(); onChange(); },
        });
        const every = numberField('schedule-every', 'Every', start ? start.interval : 1,
            () => { syncUnit(); onChange(); });
        const unit = unitWord('');

        const chosen = new Set(start ? start.weekdays : []);
        const weekdayButtons = WEEKDAYS.map((name, index) => {
            const button = h('button', {
                class: 'menu-segment-option', type: 'button', 'data-weekday': String(index),
                'aria-pressed': chosen.has(index) ? 'true' : 'false',
                onclick: () => {
                    if (chosen.has(index)) chosen.delete(index);
                    else chosen.add(index);
                    button.setAttribute('aria-pressed', chosen.has(index) ? 'true' : 'false');
                    onChange();
                },
            }, name);
            return button;
        });

        const startDate = BossModDateField.create({
            label: 'Starting', value: start ? String(start.start_date) : BossModDateField.today(), onChange,
        });
        // A new monthly rule runs on the start date's day until the operator picks another.
        const monthDay = BossModMenuSelect.create({
            label: 'Day of month', options: MONTH_DAYS,
            value: start && start.month_day ? String(start.month_day) : String(Number(startDate.read().value.slice(8))),
            variant: 'field', onChange: () => onChange(),
        });

        // Times: "At set times" or "Repeating". A stored rule with no times
        // is a repeat; hours are shown when its step is whole hours.
        let timeMode = start && !start.times.length ? 'every' : 'at';
        const storedHours = timeMode === 'every' && start.every_minutes % 60 === 0;
        const repeatEvery = numberField('schedule-repeat-every', 'Repeat every', timeMode === 'every'
            ? (storedHours ? start.every_minutes / 60 : start.every_minutes) : REPEAT_DEFAULTS.every, () => onChange());
        const repeatUnit = BossModMenuSelect.create({
            label: 'Repeat unit', options: REPEAT_UNITS.map(({ value, label }) => ({ value, label })),
            value: timeMode === 'every' ? (storedHours ? 'hours' : 'minutes') : REPEAT_DEFAULTS.unit,
            variant: 'field', onChange: () => onChange(),
        });
        const windowStart = BossModTimeField.create({
            label: 'Repeat from', value: timeMode === 'every' ? start.window_start : REPEAT_DEFAULTS.start, onChange,
        });
        const windowEnd = BossModTimeField.create({
            label: 'Repeat until', value: timeMode === 'every' ? start.window_end : REPEAT_DEFAULTS.end, onChange,
        });
        const modeButtons = [['at', 'At set times'], ['every', 'Repeating']].map(([mode, label]) => h('button', {
            class: 'menu-segment-option', type: 'button', 'data-time-mode': mode,
            onclick: () => { timeMode = mode; syncMode(); onChange(); },
        }, label));

        const timesEl = h('div', { class: 'schedule-times' });
        /** The "At" rows, in order: each time field and its row. */
        const timeRows = [];
        const addTime = h('button', {
            class: 'btn-link schedule-add-time', type: 'button',
            onclick: () => { addTimeRow('').field.focus(); onChange(); },
        }, '+ Add time');

        /** One `At` row: the time and its remove button. @returns {object} The row's entry. */
        function addTimeRow(value) {
            const field = BossModTimeField.create({ label: 'Time of day', value, onChange });
            const row = h('div', { class: 'schedule-time-row' }, field.element);
            const entry = { field, row };
            row.append(h('button', {
                class: 'header-icon-btn schedule-time-remove', type: 'button',
                'aria-label': 'Remove this time', 'data-tooltip': 'Remove this time',
                onclick: () => {
                    if (timeRows.length === 1) return;
                    timeRows.splice(timeRows.indexOf(entry), 1);
                    field.destroy();
                    row.remove();
                    syncTimes();
                    onChange();
                },
            }, h('i', { 'data-lucide': 'x', 'aria-hidden': 'true' })));
            timeRows.push(entry);
            timesEl.append(row);
            BossModIcons.paint(row, 'schedule-fields');
            syncTimes();
            return entry;
        }

        /** The last time cannot be removed, and a thirteenth cannot be added. */
        function syncTimes() {
            timesEl.querySelectorAll('.schedule-time-remove').forEach((button) => {
                button.disabled = timeRows.length === 1;
            });
            addTime.hidden = timeRows.length >= MAX_TIMES;
        }

        function syncUnit() {
            const item = frequencyOf(frequency);
            unit.textContent = item.unit[String(every.value).trim() === '1' ? 0 : 1];
        }

        // A new schedule starts with one empty time for the operator to fill.
        (start ? start.times : ['']).forEach((value) => addTimeRow(value));

        const weekdaysRow = h('div', { class: 'schedule-weekdays-row' }, unitWord('on', `${id}-on`),
            h('div', { class: 'menu-segment schedule-weekdays', role: 'group', 'aria-labelledby': `${id}-on` },
                weekdayButtons));
        const monthRow = h('div', { class: 'schedule-month-field' },
            h('div', { class: 'schedule-month-row' }, unitWord('on day'), monthDay.element),
            h('p', { class: 'field-hint' }, 'Shorter months use their last day.'));
        const atPanel = h('div', { class: 'schedule-at' }, timesEl, addTime);
        const repeatPanel = h('div', { class: 'schedule-repeat-row' },
            unitWord('every'), repeatEvery, repeatUnit.element,
            unitWord('from'), windowStart.element, unitWord('to'), windowEnd.element);
        const element = h('div', { class: 'schedule-fields' },
            h('div', { class: 'schedule-cadence-row' }, repeats.element, unitWord('every'), every, unit),
            weekdaysRow,
            monthRow,
            h('div', { class: 'schedule-start-row' }, unitWord('starting'), startDate.element),
            h('div', { class: 'menu-segment schedule-time-mode', role: 'group', 'aria-label': 'Times' }, modeButtons),
            atPanel,
            repeatPanel);

        /** Show the chosen time mode's controls and press its segment. */
        function syncMode() {
            modeButtons.forEach((button) => {
                button.setAttribute('aria-pressed', button.getAttribute('data-time-mode') === timeMode ? 'true' : 'false');
            });
            atPanel.hidden = timeMode !== 'at';
            repeatPanel.hidden = timeMode !== 'every';
        }
        syncMode();

        /** Show only the controls the frequency takes. */
        function sync() {
            weekdaysRow.hidden = frequency !== 'weekly';
            monthRow.hidden = frequency !== 'monthly';
            syncUnit();
        }
        sync();

        /**
         * One window end as `{value}` or `{error}`: an empty one asks for
         * both ends; an unreadable one says so in the field's own words.
         */
        function readWindowEnd(field) {
            const result = field.read();
            if (result.ok) return { value: result.value };
            return { error: result.empty ? 'Pick when the repeat starts and ends.' : result.error };
        }

        /**
         * The chosen time mode as rule fields, the other mode's fields null
         * (or empty), or the first problem, mirroring RecurrenceRule.
         * @returns {{mode: object}|{error: string}}
         */
        function readTimes() {
            if (timeMode === 'at') {
                const reads = timeRows.map((entry) => entry.field.read());
                if (reads.some((result) => result.empty)) return { error: 'Fill in or remove the empty time.' };
                const unreadable = reads.find((result) => !result.ok);
                if (unreadable) return { error: unreadable.error };
                const times = reads.map((result) => result.value);
                if (new Set(times).size !== times.length) return { error: 'Each time can appear only once.' };
                return { mode: { times: times.slice().sort(), every_minutes: null, window_start: null, window_end: null } };
            }
            const unitSpec = REPEAT_UNITS.find((item) => item.value === repeatUnit.getValue());
            const n = wholeIn(repeatEvery.value, 1, unitSpec.max);
            if (n === null) return { error: `Repeat every must be a whole number from 1 to ${unitSpec.max} ${unitSpec.label}.` };
            const from = readWindowEnd(windowStart);
            if (from.error) return { error: from.error };
            const to = readWindowEnd(windowEnd);
            if (to.error) return { error: to.error };
            if (to.value < from.value) return { error: 'The window must end after it starts (overnight windows are not supported).' };
            return { mode: { times: [], every_minutes: n * unitSpec.factor, window_start: from.value, window_end: to.value } };
        }

        /** See @returns. */
        function read() {
            const item = frequencyOf(frequency);
            const interval = wholeIn(every.value, 1, item.max);
            if (interval === null) return { ok: false, error: `Every must be a whole number from 1 to ${item.max}.` };
            const slots = readTimes();
            if (slots.error) return { ok: false, error: slots.error };
            const weekdays = frequency === 'weekly' ? Array.from(chosen).sort((a, b) => a - b) : [];
            if (frequency === 'weekly' && !weekdays.length) return { ok: false, error: 'Pick at least one weekday.' };
            // The dropdown only offers 1–31; checked anyway, as the server checks it.
            const day = frequency === 'monthly' ? wholeIn(monthDay.getValue(), 1, 31) : null;
            if (frequency === 'monthly' && day === null) return { ok: false, error: 'Pick a day of the month.' };
            return {
                ok: true,
                rule: { frequency, interval, ...slots.mode, weekdays, month_day: day, start_date: startDate.read().value },
            };
        }

        /** Every field widget, the times included, as it stands now. */
        const widgets = () => [startDate, windowStart, windowEnd, ...timeRows.map((entry) => entry.field)];

        return {
            element,
            read,
            /** Freeze every control while a save is in flight. */
            setDisabled(disabled) {
                [every, repeatEvery].forEach((input) => { input.disabled = disabled; });
                widgets().forEach((widget) => widget.setDisabled(disabled));
                // The chips, the dropdown triggers, + Add time and the ✕ buttons.
                element.querySelectorAll('button').forEach((button) => { button.disabled = disabled; });
                // Re-derive the one button that stays disabled on its own.
                if (!disabled) syncTimes();
            },
            /** Put every open panel away; this editor is finished with. */
            destroy() {
                [repeats, repeatUnit, monthDay].forEach((select) => select.destroy());
                widgets().forEach((widget) => widget.destroy());
            },
        };
    }

    return { create };
})();
