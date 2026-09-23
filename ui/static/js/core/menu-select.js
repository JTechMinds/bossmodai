/**
 * BossMod AI — a dropdown built from the app's own menu.
 *
 * A trigger button that names the current choice, and the shared anchored
 * panel (core/menu.js createMenu) listing the options as `.menu-action`
 * rows — the same panel and rows a click on an agent's name in a chat opens.
 * It replaces the native <select> in toolbars: WebKitGTK paints a <select> as
 * a grey GTK button with its own arrow and height, so the one control that
 * could not be styled was the one that broke every toolbar row it sat in.
 *
 * It owns the current value and nothing else. The options are handed in, and
 * a change is reported through onChange; the caller never reads the DOM.
 */
const BossModMenuSelect = (() => {
    const { h } = BossModDom;

    /**
     * @param {Array<object>} options
     * @returns {Array<{value: string, label: string, avatar: object|null}>}
     * @throws {Error} On an empty list, or an option without a label or with a
     *   duplicate value — two rows for one value cannot both be "the choice".
     */
    function normalize(options) {
        if (!Array.isArray(options) || options.length === 0) {
            throw new Error('[menu-select] options must be a non-empty array');
        }
        const seen = new Set();
        return options.map((option) => {
            const value = String((option && option.value) ?? '');
            if (!option || !option.label) throw new Error(`[menu-select] option "${value}" needs a label`);
            if (seen.has(value)) throw new Error(`[menu-select] duplicate option value "${value}"`);
            seen.add(value);
            return { value, label: String(option.label), avatar: option.avatar || null };
        });
    }

    /**
     * Build a dropdown.
     *
     * @param {object} deps
     * @param {string} deps.label  What is being chosen ("Filter by assignee").
     *   The trigger's accessible name is `${label}: ${current option}`, and the
     *   open panel is named by it.
     * @param {Array<{value: string, label: string,
     *   avatar?: {name: string, color?: string}}>} deps.options  An `avatar`
     *   puts the person's chip beside the row, as the mention picker does.
     * @param {string} [deps.value]  The starting choice; the first option when
     *   omitted.
     * @param {(value: string) => void} deps.onChange  A different option was
     *   picked. Picking the current one again reports nothing, as a native
     *   select does not.
     * @returns {{element: HTMLElement, getValue: () => string,
     *   setOptions: (options: object[], value?: string) => void,
     *   close: () => void, destroy: () => void}}
     * @throws {Error} On a missing label or onChange, bad options, or a value
     *   that is not one of them — a trigger naming a choice the list does not
     *   hold would say one thing while filtering by another.
     */
    function create(deps) {
        const { label, options, value, onChange } = deps || {};
        if (!label) throw new Error('[menu-select] deps.label is required');
        if (typeof onChange !== 'function') throw new Error('[menu-select] deps.onChange is required');

        let list = normalize(options);
        let current = value === undefined ? list[0].value : String(value);
        /** The open panel, or null. One at a time, and the trigger toggles it. */
        let menu = null;

        const text = h('span', { class: 'menu-select-value' });
        const trigger = h('button', {
            class: 'btn btn-sm menu-select-trigger',
            type: 'button',
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggle(),
        }, text, h('i', { 'data-lucide': 'chevron-down', 'aria-hidden': 'true' }));
        // The positioned host the panel hangs off, so it opens under the
        // trigger rather than against whatever toolbar row contains it; the
        // panel must not live inside the button (nested interactive).
        const element = h('span', { class: 'menu-select' }, trigger);

        /** @returns {object} The option for `current`. */
        function selected() {
            const found = list.find((option) => option.value === current);
            if (!found) throw new Error(`[menu-select] "${current}" is not one of the options`);
            return found;
        }

        /** Write the trigger FROM the value, never the other way round. */
        function sync() {
            const option = selected();
            text.textContent = option.label;
            trigger.setAttribute('aria-label', `${label}: ${option.label}`);
        }

        function row(option) {
            const pressed = option.value === current;
            return h('button', {
                class: 'menu-action menu-select-option',
                type: 'button',
                'aria-pressed': String(pressed),
                onclick: () => pick(option.value),
            },
                option.avatar
                    ? BossModAvatar.create({
                        name: option.avatar.name, color: option.avatar.color, size: 'chip',
                    })
                    : null,
                h('span', { class: 'menu-select-label' }, option.label),
                pressed ? h('i', { 'data-lucide': 'check', 'aria-hidden': 'true' }) : null);
        }

        function pick(next) {
            close();
            if (next === current) return;
            current = next;
            sync();
            onChange(current);
        }

        /** @returns {void} */
        function close() {
            if (!menu) return;
            const open = menu;
            menu = null;
            open.close();
        }

        /**
         * Show the options, or put them away again. The panel is
         * core/menu.js's, which owns the focus trap, Esc, the press-outside
         * dismiss and returning focus to the trigger.
         * @returns {void}
         */
        function toggle() {
            if (menu) {
                close();
                return;
            }
            menu = BossModMenu.createMenu({
                anchor: trigger,
                label,
                items: [h('div', { class: 'menu-actions' }, list.map(row))],
                container: element,
                onClose: () => {
                    menu = null;
                    trigger.setAttribute('aria-expanded', 'false');
                },
            });
            menu.element.setAttribute('data-menu', 'select');
            BossModIcons.paint(menu.element, 'menu-select');
            trigger.setAttribute('aria-expanded', 'true');
            // Open on the current choice, as a native list does, rather than on
            // whichever row happens to be first.
            const chosen = menu.element.querySelector('[aria-pressed="true"]');
            if (chosen) chosen.focus();
        }

        sync();

        return {
            element,

            /** @returns {string} The current choice's value. */
            getValue() {
                return current;
            },

            /**
             * Replace the options, and optionally the choice with them.
             *
             * An open panel keeps the rows it opened with until it is next
             * opened: swapping rows under the pointer would move the one the
             * operator is reaching for.
             *
             * @param {object[]} next  Same shape as deps.options.
             * @param {string} [nextValue]  Defaults to the current choice.
             * @returns {void}
             * @throws {Error} On bad options, or a choice they do not hold.
             */
            setOptions(next, nextValue) {
                const nextList = normalize(next);
                const nextCurrent = nextValue === undefined ? current : String(nextValue);
                // Checked before anything is assigned, so a refused call leaves
                // the control exactly as it was rather than half-replaced.
                if (!nextList.some((option) => option.value === nextCurrent)) {
                    throw new Error(`[menu-select] "${nextCurrent}" is not one of the options`);
                }
                list = nextList;
                current = nextCurrent;
                sync();
            },

            close,

            /**
             * Put the panel away; its press-outside listener must not outlive
             * the toolbar it hangs off.
             * @returns {void}
             */
            destroy() {
                close();
            },
        };
    }

    return { create };
})();
