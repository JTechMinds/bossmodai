/**
 * BossMod AI — the "add from other floors" picker layer.
 *
 * Opened by a floor settings section's Add door (people, threads, projects)
 * as a LAYER over the settings (core/overlays.js). A search field, then a
 * checklist grouped by the floor each item lives on now, and `Next`, which
 * stays disabled until something is chosen. Projects pick ONE (radios);
 * people and threads pick many (checkboxes). `Next` hands the chosen ids to
 * the caller, which opens the confirm layer over this one; the caller closes
 * this layer once the move lands.
 *
 * Loading, a failed load with Retry, nothing to pick, and the list: each is
 * said in words.
 */
const BossModFloorPicker = (() => {
    const { h, clear } = BossModDom;
    const NEXT_ID = 'floor-picker-next';
    let sequence = 0;

    /**
     * Open the picker.
     *
     * @param {object} opts
     * @param {string} opts.title  The layer's title.
     * @param {string} opts.what  Plural noun for the search label ("people").
     * @param {boolean} opts.multi  Many (checkboxes) or one (radios).
     * @param {string} opts.emptyText  What to say when nothing can be picked.
     * @param {() => Promise<Array<{label: string, items: Array<{id: string,
     *   name: string, meta: string, lead: (HTMLElement|null)}>}>>} opts.load
     *   The groups, one per source floor. Empty groups are the caller's to drop.
     * @param {(ids: string[]) => void} opts.onNext
     * @returns {{close: () => void}}
     */
    function open({ title, what, multi, emptyText, load, onNext }) {
        sequence += 1;
        const radioName = `floor-picker-${sequence}`;
        const chosen = new Set();
        const options = [];
        const list = h('div', { class: 'floor-pick-list' });
        const search = BossModSearchField.create({
            placeholder: 'Search',
            label: `Search ${what}`,
            onInput: () => filter(),
        });
        const body = h('div', { class: 'floor-picker' }, search.element, list);

        const layer = BossModOverlays.createModal({
            title,
            size: 'panel',
            body,
            closeOnBackdrop: false,
            actions: [
                { label: 'Cancel', tone: 'quiet' },
                {
                    label: 'Next', tone: 'primary', id: NEXT_ID, keepOpen: true,
                    onSelect: () => {
                        if (chosen.size) onNext(Array.from(chosen));
                    },
                },
            ],
        });
        BossModIcons.paint(layer.element, 'floor-picker');

        function syncNext() {
            const next = layer.element.querySelector(`#${NEXT_ID}`);
            if (!next) throw new Error('[floor-picker] the Next action did not render');
            next.disabled = chosen.size === 0;
        }

        function say(node) {
            clear(list);
            list.append(node);
        }

        /** Hide what the search does not match, and any group left empty. */
        function filter() {
            const query = String(search.input.value || '').trim().toLowerCase();
            options.forEach(({ element, haystack }) => {
                element.hidden = Boolean(query) && !haystack.includes(query);
            });
            Array.from(list.querySelectorAll('.floor-pick-group')).forEach((group) => {
                group.hidden = !Array.from(group.querySelectorAll('.floor-pick-option'))
                    .some((option) => !option.hidden);
            });
        }

        function option(item) {
            const input = h('input', {
                type: multi ? 'checkbox' : 'radio',
                name: multi ? null : radioName,
                value: item.id,
                onchange: () => {
                    if (!multi) chosen.clear();
                    if (input.checked) chosen.add(item.id);
                    else chosen.delete(item.id);
                    syncNext();
                },
            });
            const element = h('label', { class: 'floor-pick-option' },
                input,
                item.lead || null,
                h('span', { class: 'floor-item-who' },
                    h('span', { class: 'floor-item-name' }, item.name),
                    item.meta ? h('span', { class: 'floor-item-meta' }, item.meta) : null));
            options.push({ element, haystack: `${item.name} ${item.meta || ''}`.toLowerCase() });
            return element;
        }

        async function fill() {
            chosen.clear();
            options.length = 0;
            syncNext();
            say(h('p', { class: 'field-hint' }, 'Loading…'));
            let groups;
            try {
                groups = await load();
            } catch (err) {
                console.error('[floor-picker] could not load the choices', err);
                say(h('div', { class: 'floor-section-error' },
                    h('p', { class: 'context-error', role: 'alert' },
                        `Could not load ${what}. ${err.message || ''}`.trim()),
                    h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { void fill(); } }, 'Retry')));
                return;
            }
            if (!groups.length) {
                say(h('p', { class: 'field-hint' }, emptyText));
                return;
            }
            clear(list);
            groups.forEach((group, index) => {
                const headingId = `${radioName}-group-${index}`;
                list.append(h('div', { class: 'floor-pick-group', role: 'group', 'aria-labelledby': headingId },
                    h('p', { class: 'floor-pick-group-title', id: headingId }, group.label),
                    group.items.map(option)));
            });
            filter();
        }

        void fill();
        return { close: () => layer.close() };
    }

    return { open };
})();
