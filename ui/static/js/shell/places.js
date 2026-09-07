/**
 * BossMod AI — place registry.
 *
 * A "place" is what fills the centre column. All six are stubs in Part B;
 * Phases 2 and 3 replace them via register() rather than editing this file,
 * so the nav order and the shell contract live in exactly one place.
 */
const BossModPlaces = (() => {
    const { h, clear } = BossModDom;

    /** Nav order, left to right. */
    const PLACE_IDS = Object.freeze(['chat', 'office', 'board', 'files', 'metrics', 'log']);

    /**
     * Build a placeholder place. Every place — stub or real — renders exactly
     * one h1[tabindex="-1"], which navigate() focuses after mounting.
     *
     * @param {string} label
     * @param {string} note What will live here once implemented.
     * @returns {object} Place
     */
    function stub(label, note) {
        return {
            label,
            mount(el) {
                clear(el);
                el.append(h('div', { class: 'place-stub' },
                    h('h1', { tabindex: '-1' }, label),
                    h('p', { class: 'place-stub-note' }, note)));
            },
            unmount() { /* stubs hold no subscriptions */ },
        };
    }

    const registry = {
        chat: Object.assign(stub('Chat', 'Conversations arrive in Phase 2.'), {
            icon: 'message-circle',
            hasContext: true,
        }),
        office: Object.assign(stub('Office', 'The office map arrives in Phase 3.'), {
            icon: 'building',
        }),
        board: Object.assign(stub('Board', 'The task board arrives in Phase 3.'), {
            icon: 'list-todo',
        }),
        files: Object.assign(stub('Files', 'The file browser arrives in Phase 3.'), {
            icon: 'folder',
        }),
        metrics: Object.assign(stub('Metrics', 'Company metrics arrive in Phase 3.'), {
            icon: 'bar-chart-3',
        }),
        log: Object.assign(stub('Log', 'Activity and diagnostics arrive in Phase 3.'), {
            icon: 'activity',
        }),
    };

    /**
     * Replace a place. Phases 2 and 3 call this at load time.
     * @param {string} id
     * @param {object} place
     */
    function register(id, place) {
        if (PLACE_IDS.indexOf(id) === -1) {
            throw new Error(`[places] unknown place "${id}"`);
        }
        if (typeof place.mount !== 'function' || typeof place.unmount !== 'function') {
            throw new Error(`[places] "${id}" must provide mount and unmount`);
        }
        registry[id] = place;
    }

    function get(id) {
        return Object.prototype.hasOwnProperty.call(registry, id) ? registry[id] : null;
    }

    function all() {
        return PLACE_IDS.map((id) => registry[id]);
    }

    return { PLACE_IDS, get, all, register };
})();
