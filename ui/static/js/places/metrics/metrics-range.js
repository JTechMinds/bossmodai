/**
 * BossMod AI — the Metrics period: which days the dashboard counts.
 *
 * The dashboard endpoint takes a half-open pair of instants, `[start, end)`.
 * The operator thinks in calendar days on their own clock, so this is where
 * one becomes the other: a preset or a custom From/To names inclusive local
 * days, and `toInstants` turns them into local midnights as UTC ISO strings.
 * The browser's Date is the one party that knows where local midnight falls
 * across a DST change, so the server stays timezone-free.
 *
 * Split along one seam, as the Log's filter bar is: the calendar functions
 * (`PRESETS`, `presetDays`, `toInstants`, `label`) are pure and exported, so
 * they are tested without a DOM; `createRangeFilter` builds the toolbar row.
 * The row is stateless across mounts — it starts from `initial` and the place
 * remembers the last period (metrics-place.js `lastRange`), as log-place.js
 * remembers `lastView`.
 *
 * Day arithmetic is BossModDateField's (`parseIso`, `addDays`), so a week
 * starts on Monday here because it does there, not because this file agrees.
 */
const BossModMetricsRange = (() => {
    const { h } = BossModDom;
    const DATES = BossModDateField;

    /** The periods the dropdown offers, in order. `custom` reveals From/To. */
    const PRESETS = Object.freeze([
        Object.freeze({ value: 'today', label: 'Today' }),
        Object.freeze({ value: 'yesterday', label: 'Yesterday' }),
        Object.freeze({ value: 'week', label: 'This week' }),
        Object.freeze({ value: 'month', label: 'This month' }),
        Object.freeze({ value: 'custom', label: 'Custom range' }),
    ]);
    const CUSTOM = 'custom';
    const BACKWARDS = 'The end date is before the start date.';

    function presetLabel(preset) {
        const found = PRESETS.find((entry) => entry.value === preset);
        if (!found) throw new Error(`[metrics-range] unknown preset "${preset}"`);
        return found.label;
    }

    /**
     * The inclusive local days a named preset covers, counted from `todayIso`.
     * "This week" and "This month" run from their first day to today.
     *
     * @param {string} preset  One of PRESETS' values except `custom`.
     * @param {string} todayIso  Today as `YYYY-MM-DD`; injected so a test pins
     *   the clock (the place passes `BossModDateField.today()`).
     * @returns {{from: string, to: string}}
     * @throws {Error} On `custom` (its days are the operator's, not a rule's),
     *   an unknown preset, or a `todayIso` that is not a calendar day.
     */
    function presetDays(preset, todayIso) {
        const today = DATES.parseIso(todayIso);
        switch (preset) {
            case 'today':
                return { from: todayIso, to: todayIso };
            case 'yesterday': {
                const day = DATES.addDays(todayIso, -1);
                return { from: day, to: day };
            }
            case 'week':
                // Monday 0 … Sunday 6, from Date's Sunday-first getDay().
                return { from: DATES.addDays(todayIso, -((today.getDay() + 6) % 7)), to: todayIso };
            case 'month':
                return { from: DATES.addDays(todayIso, 1 - today.getDate()), to: todayIso };
            case CUSTOM:
                throw new Error('[metrics-range] a custom range has no preset days');
            default:
                throw new Error(`[metrics-range] unknown preset "${preset}"`);
        }
    }

    /**
     * Inclusive local days as the endpoint's half-open instants: local
     * midnight of `from`, and local midnight of the day after `to`.
     *
     * @param {{from: string, to: string}} days  `YYYY-MM-DD` each.
     * @returns {{start: string, end: string}} UTC ISO-8601 instants.
     * @throws {Error} When `to` is before `from`, or either is not a day.
     */
    function toInstants({ from, to }) {
        const start = DATES.parseIso(from);
        const end = DATES.parseIso(DATES.addDays(to, 1));
        if (to < from) throw new Error(`[metrics-range] ${to} is before ${from}`);
        return { start: start.toISOString(), end: end.toISOString() };
    }

    /**
     * The period as the header names it: the preset's name, one day, or a
     * span written with core/format.js's `→`.
     *
     * @param {{preset: string, from: string, to: string}} range
     * @returns {string}
     * @throws {Error} On an unknown preset or a custom day that is not a day.
     */
    function label({ preset, from, to }) {
        if (preset !== CUSTOM) return presetLabel(preset);
        if (from === to) return DATES.formatDateLabel(from);
        return `${DATES.formatDateLabel(from)} → ${DATES.formatDateLabel(to)}`;
    }

    /**
     * Build the period row: the Period dropdown, From/To when it says Custom
     * range, the inline error, and `trailing` last.
     *
     * @param {object} deps
     * @param {{preset: string, from: string, to: string}} deps.initial  Where
     *   the row starts. A named preset's days are recomputed from today on
     *   every read, so "Today" stays today across midnight; a custom range
     *   seeds From and To.
     * @param {() => void} deps.onChange  The period changed to a valid one;
     *   read it back with `read()`. Not called while To is before From.
     * @param {Node} [deps.trailing]  Appended last in the row (the place's
     *   Refresh), so the header has one toolbar row, not two.
     * @returns {{element: HTMLElement, read: () => object, destroy: () => void}}
     *   `read()` is `{ok: true, preset, days, instants, label}` or
     *   `{ok: false, error}`.
     * @throws {Error} When `initial` or `onChange` is missing, or `initial`
     *   names an unknown preset or, for a custom range, a non-day.
     */
    function createRangeFilter(deps) {
        const { initial, onChange, trailing } = deps || {};
        if (!initial || typeof initial !== 'object') throw new Error('[metrics-range] deps.initial is required');
        if (typeof onChange !== 'function') throw new Error('[metrics-range] deps.onChange is required');
        presetLabel(initial.preset);

        let from = null;
        let to = null;
        /** The days last shown, so switching to Custom range starts on them. */
        let lastDays = initial.preset === CUSTOM
            ? { from: initial.from, to: initial.to }
            : presetDays(initial.preset, DATES.today());
        const errorEl = h('p', { class: 'files-error', role: 'alert' });

        const period = BossModMenuSelect.create({
            label: 'Period',
            options: PRESETS,
            value: initial.preset,
            onChange: (preset) => {
                // Custom range opens on the days the operator was just looking at.
                if (preset === CUSTOM) showCustom(lastDays);
                else hideCustom();
                changed();
            },
        });

        const element = h('div', { class: 'place-controls' }, period.element, errorEl, trailing || null);

        function field(text, value) {
            return DATES.create({ label: text, value, variant: 'button', onChange: changed });
        }

        function showCustom(days) {
            hideCustom();
            from = field('From', days.from);
            to = field('To', days.to);
            element.insertBefore(from.element, errorEl);
            element.insertBefore(to.element, errorEl);
        }

        function hideCustom() {
            [from, to].forEach((date) => {
                if (!date) return;
                date.destroy();
                date.element.remove();
            });
            from = null;
            to = null;
        }

        function read() {
            const preset = period.getValue();
            const days = preset === CUSTOM
                ? { from: from.read().value, to: to.read().value }
                : presetDays(preset, DATES.today());
            if (days.to < days.from) return { ok: false, error: BACKWARDS };
            return {
                ok: true, preset, days, instants: toInstants(days), label: label({ preset, ...days }),
            };
        }

        /** Show the error, or clear it. @returns {object} The range read. */
        function showState() {
            const range = read();
            errorEl.textContent = range.ok ? '' : range.error;
            errorEl.hidden = range.ok;
            return range;
        }

        /** Tell the place only about a valid period; a backwards one stays on screen. */
        function changed() {
            const range = showState();
            if (!range.ok) return;
            lastDays = range.days;
            onChange();
        }

        if (initial.preset === CUSTOM) showCustom(lastDays);
        showState();

        return {
            element,
            read,
            /** Put any open panel away. */
            destroy() {
                hideCustom();
                period.destroy();
            },
        };
    }

    return { PRESETS, presetDays, toInstants, label, createRangeFilter };
})();
