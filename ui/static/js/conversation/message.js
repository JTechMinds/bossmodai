/**
 * BossMod AI — one ordinary message node.
 *
 * Renders `kind === 'message'` and nothing else; every other kind belongs to
 * event-cards.js. It reads only the normalised Message shape (spec 4.1), never
 * a backend field, which is the whole point of the source adapters: three wire
 * formats used to mean three renderers.
 *
 * Message bodies are agent output and are therefore untrusted. They are also
 * markdown — agents write lists, fences and emphasis whether or not anything
 * renders them — so they go through core/markdown.js, which parses inertly and
 * sanitises against an allowlist before a node reaches the page. This module
 * still builds no markup of its own and still never touches innerHTML.
 *
 * Every author renders the same way, the operator's own turns included: one
 * transcript that formatted `**x**` for one speaker and not the other would be
 * two renderers again, and a pasted log is worth a fence whoever pasted it.
 *
 * The name is chrome, not prose. It sits above the paragraph bubble as a
 * quiet gray chip with a border. A color-coded initial sits lower-left
 * beside the bubble, bottom-aligned with it, so a labelled turn reads as
 * a name over `[face] [bubble]` rather than a face stacked on the name row.
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

        const body = h('div', { class: 'msg-body md' },
            BossModMarkdown.render(String(message.text || '')));
        if (typeof BossModMentionPills !== 'undefined') {
            BossModMentionPills.linkify(body);
        }

        const label = String(message.authorName || '').trim();
        const showName = Boolean(message.showAuthor);
        const faceName = label || (author === 'human' ? 'You' : 'Agent');
        const color = message.authorColor || null;
        const withFace = author === 'human' || author === 'agent';
        const bubble = h('div', { class: `msg msg-${author}` },
            body,
            createdAt
                ? h('time', { class: 'msg-time', datetime: createdAt }, timeLabel(createdAt))
                : null);

        return h('div', {
            class: `msg-turn msg-turn-${author}`,
            'data-message-key': key || null,
        },
            withFace
                ? h('span', { class: 'msg-face', 'aria-hidden': 'true' },
                    BossModAvatar.create({ name: faceName, color, size: 'sm' }))
                : null,
            h('div', { class: 'msg-stack' },
                showName
                    ? h('div', { class: 'msg-author' }, label || 'Unknown')
                    : null,
                bubble));
    }

    return { renderMessage };
})();
