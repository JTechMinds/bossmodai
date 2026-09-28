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
    // The group entry. It inserts plain `@everyone`, which the runtime reads
    // as every member of the thread; it is not an agent and never a pill.
    const EVERYONE_TOKEN = 'everyone';
    const EVERYONE_LABEL = 'Everyone';
    const EVERYONE_HINT = 'wakes every member of this thread';
    const EVERYONE_OPTION_ID = 'mention-option-everyone';

    /**
     * Bind the live-agent picker to a composer field.
     *
     * @param {object} deps
     * @param {HTMLElement} deps.input
     * @param {HTMLElement} deps.container
     * @param {object} [deps.store]
     * @param {() => object[]} [deps.getAgents]
     * @param {() => void} [deps.onChange]
     * @param {() => {everyone: boolean, memberIds: string[]|null}} [deps.mentionScope]
     *   Who the list offers: `everyone` adds the Everyone entry, and an array
     *   `memberIds` limits agents to those ids (`null` is the full live
     *   roster). Absent means no Everyone and the full roster.
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
        const mentionScope = typeof deps.mentionScope === 'function'
            ? deps.mentionScope
            : () => ({ everyone: false, memberIds: null });

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

        /** Every live hire; the external insert path resolves against this. */
        function liveRoster() {
            return BossModMentions.liveAgents(getAgents());
        }

        /** The live hires this conversation's list offers. */
        function roster() {
            const ids = mentionScope().memberIds;
            if (!Array.isArray(ids)) return liveRoster();
            const allowed = new Set(ids);
            return liveRoster().filter((agent) => allowed.has(agent.id));
        }

        /** Everyone is offered in a thread for an empty query or a prefix of `everyone` / `all`. */
        function everyoneMatches(query) {
            if (!mentionScope().everyone) return false;
            const needle = String(query || '').trim().toLowerCase();
            return !needle || EVERYONE_TOKEN.startsWith(needle) || 'all'.startsWith(needle);
        }

        function optionId(entry) {
            return entry.kind === 'everyone' ? EVERYONE_OPTION_ID : `mention-option-${entry.agent.id}`;
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
            matches.forEach((entry, index) => {
                const selected = index === highlight;
                const body = entry.kind === 'everyone'
                    ? [h('span', { class: 'mention-pill-name' }, `@${EVERYONE_LABEL}`), ' — ', EVERYONE_HINT]
                    : [BossModMentionPills.renderPill(entry.agent)];
                list.append(h('li', {
                    class: 'mention-option',
                    id: optionId(entry),
                    role: 'option',
                    'aria-selected': selected ? 'true' : 'false',
                    onclick: () => insert(entry),
                }, ...body));
            });
            const current = matches[highlight];
            if (current) input.setAttribute('aria-activedescendant', optionId(current));
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
            // Everyone first: it is the broadest target, reached by typing @a / @e.
            matches = [
                ...(everyoneMatches(trigger.query) ? [{ kind: 'everyone' }] : []),
                ...BossModMentions.filterAgents(roster(), trigger.query).map((agent) => ({ kind: 'agent', agent })),
            ];
            highlight = matches.length ? Math.min(highlight, matches.length - 1) : 0;
            open = true;
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            paint();
        }

        function insert(entry, agents = roster()) {
            let name = EVERYONE_TOKEN;
            if (!entry || entry.kind !== 'everyone') {
                const live = BossModMentions.resolveLive(agents, entry && entry.agent && entry.agent.id);
                if (!live) return null;
                name = live.name;
            }
            const result = BossModMentions.insertAtCaret(input, name);
            if (onChange) onChange();
            close();
            if (input.focus) input.focus();
            return result;
        }

        // Outside callers (the pill menu's "Mention again", the composer's
        // insertMention) hand over an agent, not a picker entry. That is an
        // explicit pick of one agent, so it resolves against every live hire,
        // not this conversation's list; a non-member must not silently no-op.
        const insertAgent = (agent) => insert({ kind: 'agent', agent }, liveRoster());
        BossModMentions.setInsertHandler(insertAgent);

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
            insert: insertAgent,
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
