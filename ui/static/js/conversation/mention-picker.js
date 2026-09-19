/**
 * BossMod AI — the composer `@` live-agent picker.
 *
 * Typing `@` opens a list of live hires, filtered as the operator types.
 * A pick inserts `@Name ` and paints the pill in the field. The pill stays
 * while the operator keeps typing — the field is not rebuilt on each key.
 * Never sends. Focus stays in the composer; this is a typeahead, not a modal.
 */
const BossModMentionPicker = (() => {
    const { h } = BossModDom;

    const PICKER_ID = 'composer-mention-picker';

    /**
     * Bind the live-agent picker to a composer field.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.input
     * @param {HTMLElement} deps.container
     * @param {object} [deps.store]
     * @param {() => object[]} [deps.getAgents]
     * @param {() => void} [deps.onChange]
     * @returns {{insert: Function, handleKeyDown: Function, sync: Function,
     *            isOpen: Function, destroy: Function, element: HTMLElement}}
     * @throws {Error} When the field or its host is missing.
     */
    function bindComposer(deps) {
        const input = deps && deps.input;
        const container = deps && deps.container;
        if (!input) throw new Error('[mention-picker] bindComposer needs the composer input');
        if (!container) throw new Error('[mention-picker] bindComposer needs the composer host');
        const getAgents = typeof deps.getAgents === 'function'
            ? deps.getAgents
            : () => (deps.store ? deps.store.getState().roster : BossModMentions.currentAgents());
        const onChange = typeof deps.onChange === 'function' ? deps.onChange : null;

        let open = false;
        let highlight = 0;
        let matches = [];

        const list = h('ul', {
            class: 'mention-picker',
            id: PICKER_ID,
            role: 'listbox',
            hidden: true,
        });
        container.append(list);
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-controls', PICKER_ID);
        input.setAttribute('aria-expanded', 'false');

        function roster() {
            return BossModMentions.liveAgents(getAgents());
        }

        function close() {
            open = false;
            matches = [];
            highlight = 0;
            list.hidden = true;
            list.replaceChildren();
            input.setAttribute('aria-expanded', 'false');
            input.removeAttribute('aria-activedescendant');
        }

        function paint() {
            list.replaceChildren();
            if (!matches.length) {
                list.append(h('li', { class: 'mention-empty' }, 'No live agent matches.'));
                input.removeAttribute('aria-activedescendant');
                return;
            }
            matches.forEach((agent, index) => {
                const selected = index === highlight;
                list.append(h('li', {
                    class: 'mention-option',
                    id: `mention-option-${agent.id}`,
                    role: 'option',
                    'aria-selected': selected ? 'true' : 'false',
                    onclick: () => insert(agent),
                }, BossModMentionPills.renderPill(agent)));
            });
            const current = matches[highlight];
            if (current) input.setAttribute('aria-activedescendant', `mention-option-${current.id}`);
        }

        function sync() {
            const caret = typeof BossModMentionDraft !== 'undefined'
                ? BossModMentionDraft.caretIn(input)
                : (Number.isInteger(input.selectionStart)
                    ? input.selectionStart : String(input.value || '').length);
            const trigger = BossModMentions.findTrigger(input.value, caret);
            if (!trigger) {
                if (open) close();
                return;
            }
            matches = BossModMentions.filterAgents(roster(), trigger.query);
            highlight = matches.length ? Math.min(highlight, matches.length - 1) : 0;
            open = true;
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            paint();
        }

        function insert(agent) {
            const live = BossModMentions.resolveLive(roster(), agent && agent.id);
            if (!live) return null;
            const result = BossModMentions.insertAtCaret(input, live.name);
            if (onChange) onChange();
            close();
            if (input.focus) input.focus();
            return result;
        }

        BossModMentions.setInsertHandler(insert);

        function handleKeyDown(event) {
            if (!open) return false;
            if (event.key === 'Escape') {
                event.preventDefault();
                close();
                return true;
            }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (matches.length) {
                    const step = event.key === 'ArrowDown' ? 1 : -1;
                    highlight = (highlight + step + matches.length) % matches.length;
                    paint();
                }
                return true;
            }
            if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
                if (!matches.length) return false;
                event.preventDefault();
                insert(matches[highlight]);
                return true;
            }
            return false;
        }

        function onInput() { sync(); }

        const onPointerDown = (event) => {
            const target = event.target;
            if (!open) return;
            if (list.contains && list.contains(target)) return;
            if (input === target || (input.contains && input.contains(target))) return;
            close();
        };

        input.addEventListener('input', onInput);
        document.addEventListener('mousedown', onPointerDown);

        return {
            element: list,
            insert,
            handleKeyDown,
            sync,
            isOpen: () => open,
            destroy() {
                close();
                input.removeEventListener('input', onInput);
                document.removeEventListener('mousedown', onPointerDown);
                list.remove();
                BossModMentions.setInsertHandler(null);
            },
        };
    }

    return { bindComposer };
})();
