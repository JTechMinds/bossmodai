/**
 * BossMod AI — the recurrence editor of a schedule, set up the way a phone
 * reminder is: Repeats (daily, weekly, monthly), Every N, On which weekdays
 * or Day of month, At one or more times, Starting a date.
 *
 * Pure DOM, no API: context/schedule-layer.js mounts it for a new schedule
 * and for an edit, and reads the rule back when ✓ is pressed. `read()` checks
 * what the server would refuse (core/models/schedule.py RecurrenceRule) so a
 * rule that cannot be saved is said here and never sent.
 *
 * Repeats is BossModMenuSelect (never a native <select>). The times and the
 * start date are native `type=time` / `type=date` inputs: the app has no
 * picker of its own, and only <select> is ruled out.
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
    const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
    const DATE = /^\d{4}-\d{2}-\d{2}$/;

    /** Distinct ids per editor, so two open editors never share a label target. */
    let instances = 0;

    /** @returns {string} Today on the operator's clock, as `YYYY-MM-DD`. */
    function today() {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    }

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
     *   `read` returns the rule in the server's shape — `times` and
     *   `weekdays` sorted, `weekdays` empty unless weekly, `month_day` null
     *   unless monthly — or the first problem as a sentence.
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

        const every = h('input', {
            class: 'field-input schedule-every', id: `${id}-every`, type: 'number', min: '1', step: '1',
            inputmode: 'numeric', oninput: () => { syncUnit(); onChange(); },
        });
        every.value = String(start ? start.interval : 1);
        const unit = h('span', { class: 'schedule-unit' });

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

        const monthDay = h('input', {
            class: 'field-input schedule-month-day', id: `${id}-month-day`, type: 'number', min: '1', max: '31',
            step: '1', inputmode: 'numeric', oninput: () => onChange(),
        });
        // Left empty unless the stored rule has one: the operator picks the day.
        monthDay.value = start && start.month_day ? String(start.month_day) : '';

        const timesEl = h('div', { class: 'schedule-times' });
        const timeInputs = [];
        const addTime = h('button', {
            class: 'btn-link schedule-add-time', type: 'button',
            onclick: () => { addTimeRow(''); onChange(); },
        }, '+ Add time');

        const startDate = h('input', {
            class: 'field-input schedule-start', id: `${id}-start`, type: 'date', oninput: () => onChange(),
        });
        startDate.value = start ? String(start.start_date) : today();

        /** One `At` row: the time and its remove button. */
        function addTimeRow(value) {
            const input = h('input', {
                class: 'field-input schedule-time', type: 'time', 'aria-label': 'Time of day',
                oninput: () => onChange(),
            });
            input.value = value;
            const row = h('div', { class: 'schedule-time-row' }, input,
                h('button', {
                    class: 'header-icon-btn schedule-time-remove', type: 'button',
                    'aria-label': 'Remove this time', 'data-tooltip': 'Remove this time',
                    onclick: () => {
                        if (timeInputs.length === 1) return;
                        timeInputs.splice(timeInputs.indexOf(input), 1);
                        row.remove();
                        syncTimes();
                        onChange();
                    },
                }, h('i', { 'data-lucide': 'x', 'aria-hidden': 'true' })));
            timeInputs.push(input);
            timesEl.append(row);
            BossModIcons.paint(row, 'schedule-fields');
            syncTimes();
        }

        /** The last time cannot be removed, and a thirteenth cannot be added. */
        function syncTimes() {
            timesEl.querySelectorAll('.schedule-time-remove').forEach((button) => {
                button.disabled = timeInputs.length === 1;
            });
            addTime.hidden = timeInputs.length >= MAX_TIMES;
        }

        function syncUnit() {
            const item = frequencyOf(frequency);
            every.setAttribute('max', String(item.max));
            unit.textContent = item.unit[String(every.value).trim() === '1' ? 0 : 1];
        }

        // A new schedule starts with one empty time for the operator to fill.
        (start ? start.times : ['']).forEach((value) => addTimeRow(value));

        const field = (label, control, forId, ...extra) => h('div', { class: 'field' },
            forId ? h('label', { class: 'field-label', for: forId }, label)
                : h('span', { class: 'field-label', id: `${id}-${label.toLowerCase().replace(/\W+/g, '-')}` }, label),
            control, ...extra);
        const weekdaysField = field('On', h('div', {
            class: 'menu-segment schedule-weekdays', role: 'group', 'aria-labelledby': `${id}-on`,
        }, weekdayButtons));
        const monthDayField = field('Day of month', monthDay, monthDay.id,
            h('p', { class: 'field-hint' }, 'Shorter months use their last day.'));

        const element = h('div', { class: 'schedule-fields' },
            field('Repeats', repeats.element),
            field('Every', h('div', { class: 'schedule-every-row' }, every, unit), every.id),
            weekdaysField,
            monthDayField,
            field('At', h('div', { class: 'schedule-at' }, timesEl, addTime), null),
            field('Starting', startDate, startDate.id));

        /** Show only the controls the frequency takes. */
        function sync() {
            weekdaysField.hidden = frequency !== 'weekly';
            monthDayField.hidden = frequency !== 'monthly';
            syncUnit();
        }
        sync();

        /** See @returns. */
        function read() {
            const item = frequencyOf(frequency);
            const interval = wholeIn(every.value, 1, item.max);
            if (interval === null) return { ok: false, error: `Every must be a whole number from 1 to ${item.max}.` };
            const times = timeInputs.map((input) => String(input.value).trim());
            if (times.some((value) => !value)) return { ok: false, error: 'Fill in or remove the empty time.' };
            if (times.some((value) => !TIME.test(value))) return { ok: false, error: 'Times must be HH:MM.' };
            if (new Set(times).size !== times.length) return { ok: false, error: 'Each time can appear only once.' };
            const weekdays = frequency === 'weekly' ? Array.from(chosen).sort((a, b) => a - b) : [];
            if (frequency === 'weekly' && !weekdays.length) return { ok: false, error: 'Pick at least one weekday.' };
            const day = frequency === 'monthly' ? wholeIn(monthDay.value, 1, 31) : null;
            if (frequency === 'monthly' && day === null) {
                return { ok: false, error: 'Day of month must be a whole number from 1 to 31.' };
            }
            const startValue = String(startDate.value).trim();
            if (!DATE.test(startValue)) return { ok: false, error: 'Pick a start date.' };
            return {
                ok: true,
                rule: {
                    frequency, interval, times: times.slice().sort(), weekdays, month_day: day, start_date: startValue,
                },
            };
        }

        return {
            element,
            read,
            /** Freeze every control while a save is in flight. */
            setDisabled(disabled) {
                [every, monthDay, startDate, ...timeInputs, ...element.querySelectorAll('button')]
                    .forEach((node) => { node.disabled = disabled; });
                // Re-derive the one button that stays disabled on its own.
                if (!disabled) syncTimes();
            },
            /** Put the Repeats panel away; this editor is finished with. */
            destroy() {
                repeats.destroy();
            },
        };
    }

    return { create };
})();
