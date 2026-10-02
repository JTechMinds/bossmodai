/**
 * BossMod AI — a time of day you can type, or pick from a list.
 *
 * It replaces the native `type=time` input, which the desktop webview
 * (WebKitGTK) paints as segmented text with no clock button and no chooser —
 * a grey box that read as disabled and gave no hint that it could be edited.
 * Typing is the fast path: `7`, `730`, `7:30p`, `19:30` all read as a time,
 * and on blur or Enter the text is rewritten the one way every time on
 * screen is spelled (BossModFormat.formatTimeOfDay). The clock button beside
 * it opens the app's own anchored panel (core/menu.js createMenu) listing
 * times every SUGGESTION_STEP_MINUTES, which is what makes the control
 * discoverable.
 *
 * The input wears core/inline-rename.js's Edit-mode look (`.edit-field`,
 * controls.css): text with the edit hairline under it, like every other
 * field it sits among.
 *
 * Split along one seam: `parseTime` is pure and exported so the parse is
 * tested without a DOM; `create` owns the DOM and nothing else. It never
 * fetches; a change is reported through `onChange` with no arguments, and
 * the owner reads the value back with `read()`.
 */
const BossModTimeField = (() => {
    const { h } = BossModDom;

    /**
     * How far apart the suggestion rows are. A presentation choice — any
     * minute can still be typed, and a stored off-grid time is listed too.
     */
    const SUGGESTION_STEP_MINUTES = 30;
    const MINUTES_PER_DAY = 24 * 60;
    /** Hour, optional `:`/`.` and two-digit minutes, optional am/pm (spaces already gone). */
    const TYPED = /^(\d{1,2})(?:[:.]?(\d{2}))?(a|p|am|pm)?$/;

    const pad = (n) => String(n).padStart(2, '0');
    const toHHMM = (minutes) => `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
    const toMinutes = (hhmm) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));

    /**
     * Read what the operator typed as a time of day.
     *
     * Accepted: `7`, `730`, `7:30`, `7.30`, `7:30p`, `7:30 pm`, `19:30`,
     * `0730`, `12am`, `12 pm`. Case and spaces are ignored. Without am/pm the
     * hour is 0–23; with it, 1–12. Minutes are 0–59.
     *
     * @param {string} text
     * @returns {string|null} Canonical 24-hour `HH:MM`, or null when the text
     *   is not a time.
     */
    function parseTime(text) {
        const match = TYPED.exec(String(text === null || text === undefined ? '' : text)
            .toLowerCase().replace(/\s+/g, ''));
        if (!match) return null;
        const hour = Number(match[1]);
        const minute = match[2] === undefined ? 0 : Number(match[2]);
        const meridiem = match[3] ? match[3][0] : null;
        if (minute > 59) return null;
        if (meridiem) {
            if (hour < 1 || hour > 12) return null;
            return toHHMM(((hour % 12) + (meridiem === 'p' ? 12 : 0)) * 60 + minute);
        }
        if (hour > 23) return null;
        return toHHMM(hour * 60 + minute);
    }

    /** @returns {number} Minutes since midnight now, on the operator's clock. */
    function nowMinutes() {
        const now = new Date();
        return now.getHours() * 60 + now.getMinutes();
    }

    /**
     * Build one time field.
     *
     * @param {object} deps
     * @param {string} deps.label  What the time is ("Time of day", "Repeat
     *   from"); the input's accessible name and the panel's.
     * @param {string} [deps.value='']  `HH:MM`, or '' for an empty field.
     * @param {() => void} deps.onChange  The text changed or a suggestion was
     *   picked; read the value back with `read()`.
     * @returns {{element: HTMLElement,
     *   read: () => ({ok: true, value: string}|{ok: false, empty: true}|{ok: false, error: string}),
     *   focus: () => void, setDisabled: (disabled: boolean) => void, destroy: () => void}}
     *   `read` parses the text as it stands, so a value typed but not yet
     *   blurred is still read. `destroy` puts an open panel away.
     * @throws {Error} When label or onChange is missing, or `value` is
     *   neither '' nor `HH:MM`.
     */
    function create(deps) {
        const { label, value = '', onChange } = deps || {};
        if (!label) throw new Error('[time-field] deps.label is required');
        if (typeof onChange !== 'function') throw new Error('[time-field] deps.onChange is required');
        // Formatted up front, so a stored value the field cannot read throws here.
        const shown = value === '' ? '' : BossModFormat.formatTimeOfDay(value);

        /** The open suggestion panel, or null. */
        let menu = null;

        const input = h('input', {
            class: 'edit-field time-field-input', type: 'text', inputmode: 'text', autocomplete: 'off',
            spellcheck: 'false', 'aria-label': label, placeholder: 'Time',
            oninput: () => { input.removeAttribute('aria-invalid'); onChange(); },
            onblur: () => commit(),
            onkeydown: (event) => {
                if (event.key !== 'Enter') return;
                // Enter settles the text here; it must not submit whatever form
                // or editor the field sits in.
                event.preventDefault();
                commit();
            },
        });
        input.value = shown;
        const toggle = h('button', {
            class: 'header-icon-btn time-field-toggle', type: 'button', 'aria-label': 'Choose a time',
            'data-tooltip': 'Choose a time', 'aria-haspopup': 'dialog', 'aria-expanded': 'false',
            onclick: () => toggleMenu(),
        }, h('i', { 'data-lucide': 'clock', 'aria-hidden': 'true' }));
        // The positioned host the panel hangs off (it must not live inside the button).
        const element = h('span', { class: 'time-field' }, input, toggle);
        BossModIcons.paint(element, 'time-field');

        /** See @returns. */
        function read() {
            const text = String(input.value).trim();
            if (!text) return { ok: false, empty: true };
            const parsed = parseTime(text);
            if (!parsed) return { ok: false, error: `“${text}” is not a time (try 7:30 PM).` };
            return { ok: true, value: parsed };
        }

        /** Settle the typed text: a time is re-shown formatted; anything else is marked and kept. */
        function commit() {
            const result = read();
            if (result.ok) {
                input.value = BossModFormat.formatTimeOfDay(result.value);
                input.removeAttribute('aria-invalid');
            } else if (result.empty) {
                input.removeAttribute('aria-invalid');
            } else {
                input.setAttribute('aria-invalid', 'true');
            }
        }

        function pick(hhmm) {
            input.value = BossModFormat.formatTimeOfDay(hhmm);
            input.removeAttribute('aria-invalid');
            closeMenu();
            onChange();
        }

        /** Every step of the day, plus the field's own time when it is off the grid. */
        function suggestionRows(current) {
            const minutes = [];
            for (let m = 0; m < MINUTES_PER_DAY; m += SUGGESTION_STEP_MINUTES) minutes.push(m);
            if (current !== null && !minutes.includes(current)) {
                minutes.push(current);
                minutes.sort((a, b) => a - b);
            }
            return minutes.map((m) => {
                const hhmm = toHHMM(m);
                const chosen = m === current;
                return h('button', {
                    class: 'menu-action time-field-option', type: 'button', 'data-time': hhmm,
                    'aria-pressed': String(chosen), onclick: () => pick(hhmm),
                }, h('span', { class: 'menu-select-label' }, BossModFormat.formatTimeOfDay(hhmm)),
                chosen ? h('i', { 'data-lucide': 'check', 'aria-hidden': 'true' }) : null);
            });
        }

        function closeMenu() {
            if (!menu) return;
            const open = menu;
            menu = null;
            open.close();
        }

        /**
         * Show the suggestions, or put them away. The panel is core/menu.js's,
         * which owns the Tab trap, Esc, the press-outside dismiss and focus
         * returning to the clock button.
         */
        function toggleMenu() {
            if (menu) {
                closeMenu();
                return;
            }
            const typed = read();
            const current = typed.ok ? toMinutes(typed.value) : null;
            const rows = suggestionRows(current);
            menu = BossModMenu.createMenu({
                anchor: toggle,
                label,
                items: [h('div', { class: 'menu-actions' }, rows)],
                container: element,
                onClose: () => {
                    menu = null;
                    toggle.setAttribute('aria-expanded', 'false');
                },
            });
            menu.element.setAttribute('data-menu', 'time');
            BossModIcons.paint(menu.element, 'time-field');
            toggle.setAttribute('aria-expanded', 'true');
            // Open on the field's time, or the row nearest the present one
            // when it has none; focusing it scrolls the list to it.
            const target = current === null ? nowMinutes() : current;
            const nearest = rows.reduce((best, row) => (
                Math.abs(toMinutes(row.getAttribute('data-time')) - target)
                    < Math.abs(toMinutes(best.getAttribute('data-time')) - target) ? row : best));
            nearest.focus();
        }

        return {
            element,
            read,
            /** Put the caret in the field (a new time row asks for it). */
            focus: () => input.focus(),
            /** Freeze the field and its button while a save is in flight. */
            setDisabled(disabled) {
                if (disabled) closeMenu();
                input.disabled = disabled;
                toggle.disabled = disabled;
            },
            destroy: () => closeMenu(),
        };
    }

    return { SUGGESTION_STEP_MINUTES, parseTime, create };
})();
