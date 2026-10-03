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
 * It owns the current value. The options are handed in, and a change is
 * reported through onChange; the caller never reads the trigger.
 *
 * THE FORM CONTRACT. Given a `name`, the control also owns a hidden
 * `<input name>` inside its element, whose value is always the current
 * choice — written at create time and on every pick, `setValue` and
 * `setOptions`. So what read a native select keeps working untouched:
 * `FormData`, `[name=…].value`, and `change` listeners, because a user pick
 * dispatches one bubbling `change` on that input (after it is updated), as a
 * native select does; `setValue` dispatches nothing, as assigning
 * `select.value` does not. READERS use the input; WRITERS go through the
 * instance — never `input.value = …`, which would leave the trigger naming
 * another choice. A module that holds only a DOM node reaches the instance
 * with `BossModMenuSelect.instanceFor(node)`.
 *
 * Two trigger looks, one control: the toolbar's `'button'` (the default), and
 * `'field'`, which reads as an in-place edit field — the task detail's Edit
 * mode puts it in a fact cell among fields that read as text.
 */
const BossModMenuSelect = (() => {
    const { h } = BossModDom;

    /** The trigger looks `deps.variant` may name; the first is the default. */
    const VARIANTS = Object.freeze(['button', 'field']);

    /**
     * Each control's API, keyed by its element, its trigger and its hidden
     * input, so `instanceFor` answers for any of the three without walking.
     */
    const INSTANCES = new WeakMap();

    /**
     * @param {Array<object>} options
     * @returns {Array<{value: string, label: string, short: (string|null),
     *   avatar: object|null}>}
     * @throws {Error} On an empty list, an option without a label, a `short`
     *   that is present but not a non-empty string, or a duplicate value —
     *   two rows for one value cannot both be "the choice".
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
            const hasShort = option.short !== undefined;
            if (hasShort && (typeof option.short !== 'string' || !option.short)) {
                throw new Error(`[menu-select] option "${value}" has a short label that is not a non-empty string`);
            }
            seen.add(value);
            return {
                value, label: String(option.label), short: hasShort ? option.short : null, avatar: option.avatar || null,
            };
        });
    }

    /**
     * Build a dropdown.
     *
     * @param {object} deps
     * @param {string} deps.label  What is being chosen ("Filter by assignee").
     *   The trigger's accessible name is `${label}: ${current option}`, and the
     *   open panel is named by it.
     * @param {Array<{value: string, label: string, short?: string,
     *   avatar?: {name: string, color?: string}}>} deps.options  An `avatar`
     *   puts the person's chip beside the row, as the mention picker does.
     *   `short` is the 'field' trigger's text only ("Charles" for the row
     *   "Charles — Build Engineer (matches)"); the rows and the accessible
     *   name keep the full `label`, and the 'button' trigger always shows
     *   the label. Without it the field trigger shows the label too.
     * @param {string} [deps.value]  The starting choice; the first option when
     *   omitted.
     * @param {'button'|'field'} [deps.variant='button']  The trigger's look.
     *   `'button'` is the toolbar's `.btn.btn-sm` naming the choice, then the
     *   chevron. `'field'` has no box: the chosen option's avatar chip (when
     *   it has one), its text, then the chevron, on one line over the edit
     *   hairline (controls.css `.menu-select-field`).
     * @param {(value: string) => void} [deps.onChange]  A different option was
     *   picked. Picking the current one again reports nothing, as a native
     *   select does not. Optional only with `name`: without either, a pick
     *   would go nowhere.
     * @param {string} [deps.name]  Puts a hidden `<input name>` carrying the
     *   current choice inside `element` (the form contract in the header).
     *   A user pick updates it, re-names the trigger, dispatches a bubbling
     *   `change` on it, then calls `onChange`.
     * @param {string} [deps.id]  Set on the trigger, so an existing
     *   `<label for>` names and focuses it. The trigger's `aria-label` still
     *   says "Label: choice".
     * @returns {{element: HTMLElement, getValue: () => string,
     *   getLabel: () => string, getOptions: () => Array<{value: string,
     *   label: string}>, setValue: (value: string) => void,
     *   setOptions: (options: object[], value?: string) => void,
     *   close: () => void, destroy: () => void}}
     * @throws {Error} On a missing label, neither onChange nor name, an
     *   unknown variant, bad options, or a value that is not one of them — a
     *   trigger naming a choice the list does not hold would say one thing
     *   while filtering by another.
     */
    function create(deps) {
        const { label, options, value, onChange, name, id, variant = VARIANTS[0] } = deps || {};
        if (!label) throw new Error('[menu-select] deps.label is required');
        if (onChange !== undefined && typeof onChange !== 'function') {
            throw new Error('[menu-select] deps.onChange must be a function');
        }
        if (!onChange && !name) throw new Error('[menu-select] deps.onChange is required without deps.name');
        if (name !== undefined && (typeof name !== 'string' || !name)) {
            throw new Error('[menu-select] deps.name must be a non-empty string');
        }
        if (!VARIANTS.includes(variant)) {
            throw new Error(`[menu-select] unknown variant "${variant}"; expected one of ${VARIANTS.join(', ')}`);
        }
        const field = variant === 'field';

        let list = normalize(options);
        let current = value === undefined ? list[0].value : String(value);
        /** The open panel, or null. One at a time, and the trigger toggles it. */
        let menu = null;

        const text = h('span', { class: 'menu-select-value' });
        /** The field trigger's avatar chip for the current choice, or null. */
        let chip = null;
        const trigger = h('button', {
            class: field ? 'menu-select-trigger menu-select-field' : 'btn btn-sm menu-select-trigger',
            type: 'button',
            id,
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            onclick: () => toggle(),
        }, text, h('i', { 'data-lucide': 'chevron-down', 'aria-hidden': 'true' }));
        /** The form value (see the header), or null without `name`. */
        const input = name ? h('input', { type: 'hidden', name }) : null;
        // The positioned host the panel hangs off, so it opens under the
        // trigger rather than against whatever toolbar row contains it; the
        // panel must not live inside the button (nested interactive).
        const element = h('span', { class: field ? 'menu-select menu-select--field' : 'menu-select' }, trigger, input);

        /** @returns {object} The option for `current`. */
        function selected() {
            const found = list.find((option) => option.value === current);
            if (!found) throw new Error(`[menu-select] "${current}" is not one of the options`);
            return found;
        }

        /**
         * Write the trigger FROM the value, never the other way round. The
         * field look shows the short label and swaps in the choice's chip,
         * rebuilt the way the rows build theirs; the toolbar look shows the
         * full label and never a chip.
         */
        function sync() {
            const option = selected();
            if (input) input.value = current;
            text.textContent = field && option.short ? option.short : option.label;
            trigger.setAttribute('aria-label', `${label}: ${option.label}`);
            if (!field) return;
            if (chip) chip.remove();
            chip = option.avatar
                ? BossModAvatar.create({ name: option.avatar.name, color: option.avatar.color, size: 'chip' })
                : null;
            if (chip) trigger.insertBefore(chip, text);
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
            // The input is written (in sync) before anyone hears of the
            // change, so a `change` listener reading it sees the new choice.
            sync();
            if (input) input.dispatchEvent(new Event('change', { bubbles: true }));
            if (onChange) onChange(current);
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

        const api = {
            element,

            /** @returns {string} The current choice's value. */
            getValue() {
                return current;
            },

            /** @returns {string} The current choice's full label. */
            getLabel() {
                return selected().label;
            },

            /**
             * The current options, for a caller matching on them (a template
             * names a personality by its label).
             * @returns {Array<{value: string, label: string}>} A fresh copy;
             *   changing it changes nothing here.
             */
            getOptions() {
                return list.map((option) => ({ value: option.value, label: option.label }));
            },

            /**
             * Choose an option from code, as assigning a native select's
             * `.value` does: the trigger and the hidden input follow, and
             * neither `onChange` nor `change` fires — only a person's pick is
             * a change to report.
             *
             * @param {string} next  One of the current options' values.
             * @returns {void}
             * @throws {Error} When no option holds that value; the control is
             *   left as it was.
             */
            setValue(next) {
                const nextCurrent = String(next);
                if (!list.some((option) => option.value === nextCurrent)) {
                    throw new Error(`[menu-select] "${nextCurrent}" is not one of the options`);
                }
                current = nextCurrent;
                sync();
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
             *   The trigger and any hidden input follow it; nothing is
             *   reported, as with `setValue`.
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
        INSTANCES.set(element, api);
        INSTANCES.set(trigger, api);
        if (input) INSTANCES.set(input, api);
        return api;
    }

    /**
     * The control a node belongs to, for a module that holds only the DOM —
     * the agent form's bindings find a field by `[name=…]` and must write it
     * through the control, never the input (the header's form contract).
     *
     * @param {Node} node  A control's `element`, its trigger, or its hidden
     *   input.
     * @returns {object} The API `create` returned.
     * @throws {Error} When the node is none of those: a module looking up a
     *   control that is not there is a bug, not an absent field.
     */
    function instanceFor(node) {
        const found = node ? INSTANCES.get(node) : undefined;
        if (!found) throw new Error('[menu-select] that node is not part of a menu select');
        return found;
    }

    return { create, instanceFor };
})();
