/**
 * BossMod AI — one ordinary message node.
 *
 * Renders `kind === 'message'` and nothing else; every other kind belongs to
 * event-cards.js. It reads only the normalised Message shape (spec 4.1), never
 * a backend field, which is the whole point of the source adapters: three wire
 * formats used to mean three renderers.
 *
 * Message bodies are agent output and are therefore untrusted. They are set
 * through textContent, never innerHTML.
 */
const BossModMessage = (() => {
    const { h } = BossModDom;

    /** Author values the stylesheet has a bubble for. */
    const AUTHORS = new Set(['human', 'agent', 'system', 'other']);

    /**
     * A clock face for the transcript.
     *
     * An unparseable timestamp renders as the raw server string rather than
     * as an empty element: showing what the server sent is honest, hiding it
     * is not. The machine-readable value stays in `datetime` either way.
     *
     * @param {string} createdAt  ISO-8601.
     * @returns {string}
     */
    function timeLabel(createdAt) {
        const at = new Date(createdAt);
        if (Number.isNaN(at.getTime())) return createdAt;
        return at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    /**
     * Build one message node.
     *
     * @param {object} message  A normalised Message. `showAuthor` decides
     *   whether the name is labelled — threads label it, agent DMs do not,
     *   because in a DM every non-human turn is the same agent.
     * @returns {HTMLElement}
     * @throws {Error} When called with a kind other than 'message'. The
     *   transcript dispatches on kind, so arriving here with another one means
     *   a source produced a shape no renderer owns.
     */
    function renderMessage(message) {
        if (!message || message.kind !== 'message') {
            throw new Error(`[message] renderMessage got kind "${message && message.kind}"`);
        }
        const author = AUTHORS.has(message.author) ? message.author : 'other';
        const key = String(message.key || '').trim();
        const createdAt = String(message.createdAt || '').trim();

        return h('div', {
            class: `msg msg-${author}`,
            'data-message-key': key || null,
        },
            message.showAuthor
                ? h('div', { class: 'msg-author' }, message.authorName || 'Unknown')
                : null,
            h('div', { class: 'msg-body' }, String(message.text || '')),
            createdAt
                ? h('time', { class: 'msg-time', datetime: createdAt }, timeLabel(createdAt))
                : null);
    }

    return { renderMessage };
})();
