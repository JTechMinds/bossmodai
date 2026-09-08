/**
 * BossMod AI — the Office activity ticker.
 *
 * A quiet strip along the bottom of the floor showing the last few things that
 * happened, and a way into the Log for the rest. It holds no history of its
 * own beyond what it displays: the Log is the record, this is the glance.
 *
 * The whole strip is one button rather than a row of links, because every
 * entry leads to the same place. One control means one tab stop and one
 * accessible name, instead of four that all say "open the log".
 */
const BossModTicker = (() => {
    const { h, clear } = BossModDom;

    /** How many entries fit the strip before the oldest falls off. */
    const VISIBLE = 4;

    /**
     * Build the ticker.
     *
     * @param {object} deps
     * @param {object} deps.bus  Topic bus; `activity` is subscribed here.
     * @param {(placeId: string, params?: object) => void} deps.navigate
     * @returns {{element: HTMLElement, destroy: () => void}}
     * @throws {Error} When bus or navigate is missing.
     */
    function createTicker(deps) {
        const { bus, navigate } = deps || {};
        if (!bus) throw new Error('[ticker] deps.bus is required');
        if (typeof navigate !== 'function') throw new Error('[ticker] deps.navigate is required');

        /** Newest first. Capped at VISIBLE so the strip cannot grow unbounded. */
        const entries = [];
        const list = h('div', { class: 'ticker-list' });
        const element = h('button', {
            class: 'ticker',
            type: 'button',
            'aria-label': 'Recent activity — open the Log',
            onclick: () => navigate('log'),
        }, list);

        function row(entry) {
            const when = BossModFormat.formatRelativeTime(entry.timestamp);
            return h('span', {
                class: 'ticker-entry',
                // Errors are marked, not coloured alone: the marker carries the
                // meaning for anyone who cannot distinguish the tone.
                'data-tone': entry.category === 'error' ? 'error' : 'normal',
            },
                h('span', { class: 'ticker-time' }, when || 'just now'),
                entry.category === 'error' ? h('span', { class: 'ticker-mark' }, 'error') : null,
                h('span', { class: 'ticker-text' }, entry.title || entry.event || 'Activity'));
        }

        function paint() {
            clear(list);
            if (entries.length === 0) {
                list.append(h('span', { class: 'ticker-empty' }, 'Nothing has happened yet'));
                return;
            }
            entries.forEach((entry) => list.append(row(entry)));
        }

        const unsubscribe = bus.subscribe('activity', (entry) => {
            if (!entry) return;
            entries.unshift(entry);
            if (entries.length > VISIBLE) entries.length = VISIBLE;
            paint();
        });

        paint();

        return {
            element,

            /**
             * Drop the activity subscription. The strip is inert afterwards.
             * @returns {void}
             */
            destroy() {
                unsubscribe();
                entries.length = 0;
                clear(list);
            },
        };
    }

    return { createTicker };
})();
