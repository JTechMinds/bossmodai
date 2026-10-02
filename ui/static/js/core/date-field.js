/**
 * BossMod AI — a calendar date, picked from a month grid.
 *
 * It replaces the native `type=date` input, which the desktop webview
 * (WebKitGTK) paints as segmented text with no calendar button — a grey box
 * that read as disabled. The trigger reads as an Edit-mode field (a
 * calendar glyph and `Wed, Oct 1, 2026` over the edit hairline, the
 * `.menu-select-field` look) and opens the app's own anchored panel
 * (core/menu.js createMenu): the month, ‹ › to page, a Monday-first grid of
 * days (the recurrence editor's WEEKDAYS and the server's Monday-started
 * weeks), and Today.
 *
 * The grid follows the ARIA grid pattern with a roving tabindex: one day is
 * a tab stop; arrows move a day or a week, PageUp/PageDown a month,
 * Home/End to the week's ends, Enter or Space picks. Esc, the Tab trap and
 * focus returning to the trigger are core/menu.js's.
 *
 * Split along one seam: the calendar arithmetic (`monthGrid`,
 * `formatDateLabel`, `today`) is pure and exported so it is tested without
 * a DOM; `create` owns the DOM. Dates are local calendar days throughout and
 * every label is spelled by hand, as core/format.js spells its own, so the
 * output never depends on the host's locale data.
 */
const BossModDateField = (() => {
    const { h } = BossModDom;

    const MONTHS = Object.freeze(['January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December']);
    /** Monday first; index 0 is Monday. */
    const WEEKDAYS = Object.freeze(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']);
    const GRID_CELLS = 42;
    const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

    const pad = (n) => String(n).padStart(2, '0');
    const isoOf = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    /** Monday 0 … Sunday 6, from Date's Sunday-first getDay(). */
    const weekdayOf = (date) => (date.getDay() + 6) % 7;

    /**
     * @param {string} iso
     * @returns {Date} Local midnight of that calendar day.
     * @throws {Error} When `iso` is not a real `YYYY-MM-DD` day.
     */
    function parseIso(iso) {
        const match = ISO.exec(String(iso));
        if (!match) throw new Error(`[date-field] not an ISO date: "${iso}"`);
        const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
        const date = new Date(year, month - 1, day);
        // new Date rolls 2026-02-30 into March; a day that rolled is not a day.
        if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) {
            throw new Error(`[date-field] not a calendar day: "${iso}"`);
        }
        return date;
    }

    /** @returns {string} Today on the operator's clock, as `YYYY-MM-DD`. */
    function today() {
        return isoOf(new Date());
    }

    /**
     * The 42 days a month's grid shows: Monday-first, starting on or before
     * the 1st, six full weeks so the grid never changes height.
     *
     * @param {number} year  Four-digit year.
     * @param {number} month  1–12.
     * @returns {Array<{iso: string, day: number, inMonth: boolean}>}
     * @throws {Error} On a month outside 1–12 or a non-integer year.
     */
    function monthGrid(year, month) {
        if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
            throw new Error(`[date-field] no month ${year}-${month}`);
        }
        const offset = weekdayOf(new Date(year, month - 1, 1));
        const cells = [];
        for (let i = 0; i < GRID_CELLS; i += 1) {
            const date = new Date(year, month - 1, 1 - offset + i);
            cells.push({ iso: isoOf(date), day: date.getDate(), inMonth: date.getMonth() === month - 1 });
        }
        return cells;
    }

    /**
     * A day as the trigger shows it: `Wed, Oct 1, 2026`.
     *
     * @param {string} iso  `YYYY-MM-DD`.
     * @returns {string}
     * @throws {Error} When `iso` is not a real calendar day.
     */
    function formatDateLabel(iso) {
        const date = parseIso(iso);
        return `${WEEKDAYS[weekdayOf(date)].slice(0, 3)}, ${MONTHS[date.getMonth()].slice(0, 3)} `
            + `${date.getDate()}, ${date.getFullYear()}`;
    }

    /** `iso` moved by whole days. */
    function addDays(iso, days) {
        const date = parseIso(iso);
        return isoOf(new Date(date.getFullYear(), date.getMonth(), date.getDate() + days));
    }

    /** `iso` moved by whole months, the day clamped to the target month's last. */
    function addMonths(iso, months) {
        const date = parseIso(iso);
        const first = new Date(date.getFullYear(), date.getMonth() + months, 1);
        const last = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
        return isoOf(new Date(first.getFullYear(), first.getMonth(), Math.min(date.getDate(), last)));
    }

    /** Distinct ids per field, so two open grids never share a label target. */
    let instances = 0;

    /**
     * Build one date field.
     *
     * @param {object} deps
     * @param {string} deps.label  What the date is ("Starting"); the
     *   trigger's accessible name is `${label}: ${the date}`.
     * @param {string} deps.value  `YYYY-MM-DD`. Required: a date field always
     *   holds a day (callers default to `today()`).
     * @param {() => void} deps.onChange  A different day was picked; read it
     *   back with `read()`.
     * @returns {{element: HTMLElement, read: () => ({ok: true, value: string}),
     *   setDisabled: (disabled: boolean) => void, destroy: () => void}}
     *   `destroy` puts an open panel away.
     * @throws {Error} When label or onChange is missing, or value is not a
     *   real calendar day.
     */
    function create(deps) {
        const { label, value, onChange } = deps || {};
        if (!label) throw new Error('[date-field] deps.label is required');
        if (typeof onChange !== 'function') throw new Error('[date-field] deps.onChange is required');
        formatDateLabel(value);
        const id = `date-field-${instances += 1}`;

        let current = String(value);
        /** The focusable day in the open grid, and the month the grid shows. */
        let cursor = current;
        let menu = null;
        let grid = null;
        let monthLabel = null;

        const text = h('span', { class: 'date-field-value' });
        const trigger = h('button', {
            class: 'edit-field date-field-trigger', type: 'button', 'aria-haspopup': 'dialog',
            'aria-expanded': 'false', onclick: () => toggleMenu(),
        }, h('i', { 'data-lucide': 'calendar', 'aria-hidden': 'true' }), text);
        const element = h('span', { class: 'date-field' }, trigger);
        BossModIcons.paint(element, 'date-field');

        /** Write the trigger from the value. */
        function sync() {
            const formatted = formatDateLabel(current);
            text.textContent = formatted;
            trigger.setAttribute('aria-label', `${label}: ${formatted}`);
        }

        function pick(iso) {
            if (!menu) return;
            const changed = iso !== current;
            current = iso;
            sync();
            closeMenu();
            if (changed) onChange();
        }

        /** The day button for `iso` in the open grid, or null. */
        const dayButton = (iso) => (grid ? grid.querySelector(`[data-date="${iso}"]`) : null);

        /** Paint the month `cursor` is in; only the cursor's day is a tab stop. */
        function renderMonth() {
            const date = parseIso(cursor);
            monthLabel.textContent = `${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
            const cells = monthGrid(date.getFullYear(), date.getMonth() + 1);
            const now = today();
            const rows = [];
            const day = (cell) => {
                const date = parseIso(cell.iso);
                return h('td', { role: 'gridcell', 'aria-selected': cell.iso === current ? 'true' : 'false' },
                    h('button', {
                        class: 'date-field-day', type: 'button', 'data-date': cell.iso,
                        'data-outside': cell.inMonth ? null : 'true',
                        tabindex: cell.iso === cursor ? '0' : '-1',
                        'aria-current': cell.iso === now ? 'date' : null,
                        'aria-label': `${WEEKDAYS[weekdayOf(date)]}, ${MONTHS[date.getMonth()]} ${cell.day}, `
                            + `${date.getFullYear()}`,
                        onclick: () => pick(cell.iso),
                    }, String(cell.day)));
            };
            for (let week = 0; week < GRID_CELLS / 7; week += 1) {
                rows.push(h('tr', { role: 'row' }, cells.slice(week * 7, week * 7 + 7).map(day)));
            }
            grid.querySelector('tbody').replaceChildren(...rows);
        }

        /** Move the cursor; a move out of the shown month repaints it, and focus follows. */
        function moveTo(iso) {
            const before = parseIso(cursor);
            const after = parseIso(iso);
            cursor = iso;
            if (before.getMonth() !== after.getMonth() || before.getFullYear() !== after.getFullYear()) {
                renderMonth();
            } else {
                grid.querySelectorAll('.date-field-day').forEach((button) => {
                    button.setAttribute('tabindex', button.getAttribute('data-date') === cursor ? '0' : '-1');
                });
            }
            dayButton(cursor).focus();
        }

        /** Page the shown month from ‹ ›; focus stays on the button pressed. */
        function page(months) {
            cursor = addMonths(cursor, months);
            renderMonth();
        }

        function onGridKeydown(event) {
            const weekday = weekdayOf(parseIso(cursor));
            const moves = {
                ArrowLeft: () => addDays(cursor, -1),
                ArrowRight: () => addDays(cursor, 1),
                ArrowUp: () => addDays(cursor, -7),
                ArrowDown: () => addDays(cursor, 7),
                PageUp: () => addMonths(cursor, -1),
                PageDown: () => addMonths(cursor, 1),
                Home: () => addDays(cursor, -weekday),
                End: () => addDays(cursor, 6 - weekday),
            };
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                pick(cursor);
                return;
            }
            const move = moves[event.key];
            if (!move) return;
            event.preventDefault();
            moveTo(move());
        }

        function closeMenu() {
            if (!menu) return;
            const open = menu;
            menu = null;
            grid = null;
            monthLabel = null;
            open.close();
        }

        /**
         * Show the month of the current day, or put it away. The panel is
         * core/menu.js's: Tab trap, Esc, press-outside, focus back to the trigger.
         */
        function toggleMenu() {
            if (menu) {
                closeMenu();
                return;
            }
            cursor = current;
            const monthId = `${id}-month`;
            monthLabel = h('p', { class: 'date-field-month', id: monthId, 'aria-live': 'polite' });
            const arrow = (icon, name, months) => h('button', {
                class: 'header-icon-btn date-field-page', type: 'button', 'aria-label': name,
                'data-tooltip': name, onclick: () => page(months),
            }, h('i', { 'data-lucide': icon, 'aria-hidden': 'true' }));
            grid = h('table', {
                class: 'date-field-grid', role: 'grid', 'aria-labelledby': monthId, onkeydown: onGridKeydown,
            },
            h('thead', {}, h('tr', { role: 'row' }, WEEKDAYS.map((name) => h('th', {
                role: 'columnheader', scope: 'col', abbr: name,
            }, name.slice(0, 2))))),
            h('tbody', {}));
            const head = h('div', { class: 'date-field-head' },
                arrow('chevron-left', 'Previous month', -1), monthLabel, arrow('chevron-right', 'Next month', 1));
            const todayButton = h('button', {
                class: 'btn-link date-field-today', type: 'button', onclick: () => pick(today()),
            }, 'Today');
            renderMonth();
            menu = BossModMenu.createMenu({
                anchor: trigger,
                label,
                items: [head, grid, todayButton],
                container: element,
                onClose: () => {
                    menu = null;
                    grid = null;
                    monthLabel = null;
                    trigger.setAttribute('aria-expanded', 'false');
                },
            });
            menu.element.setAttribute('data-menu', 'date');
            BossModIcons.paint(menu.element, 'date-field');
            trigger.setAttribute('aria-expanded', 'true');
            // createMenu focused the first stop (‹); the grid opens on the day.
            dayButton(cursor).focus();
        }

        sync();

        return {
            element,
            /** @returns {{ok: true, value: string}} The day, always set. */
            read: () => ({ ok: true, value: current }),
            /** Freeze the trigger while a save is in flight. */
            setDisabled(disabled) {
                if (disabled) closeMenu();
                trigger.disabled = disabled;
            },
            destroy: () => closeMenu(),
        };
    }

    return { today, monthGrid, formatDateLabel, create };
})();
