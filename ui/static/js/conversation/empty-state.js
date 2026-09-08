/**
 * BossMod AI — the empty conversation.
 *
 * A conversation with nothing in it used to be two lines of text and no way
 * forward: a title, a hint, and a composer the operator had to think of
 * something to type into. This is the same information plus the two things
 * there are to do with a person — say hello, or give them work.
 *
 * It is a view and only a view. The greeting text is composed here because it
 * is copy, but SENDING it is the composer's job and assigning is the Board's
 * form; both arrive as callbacks, so this module cannot become a second send
 * path or a second assign form.
 *
 * Split out of conversation/transcript.js because the transcript was already at
 * the 300-line cap and the empty state is the one status of the three that has
 * an identity and controls rather than just words.
 */
const BossModEmptyState = (() => {
    const { h } = BossModDom;

    const GREET_LABEL = 'Say hello';
    const ASSIGN_LABEL = 'Assign a task';

    /**
     * The message `Say hello` sends.
     *
     * Deliberately a request rather than a bare "hi": the operator's first turn
     * costs a real model call, and one that comes back with what the agent can
     * do is worth more than one that comes back with "Hello!".
     *
     * @param {string} name
     * @returns {string}
     */
    function greeting(name) {
        return `Hi ${name} — tell me what you can help with.`;
    }

    /**
     * Build the empty state node.
     *
     * Keeps the `.transcript-status` shape: the transcript owns removing and
     * replacing whatever the current status node is, and it finds it by class.
     *
     * @param {object} options
     * @param {string} [options.title]
     * @param {string} [options.hint]
     * @param {{name: string, color: string|null}} [options.avatar]  When the
     *   conversation has one face. A thread has none and gets the words alone.
     * @param {(text: string) => any} [options.onGreet]  Sends through the
     *   composer's send path.
     * @param {() => any} [options.onAssign]  Opens the one assign form.
     * @returns {HTMLElement}
     */
    function render(options) {
        const opts = options || {};
        const who = opts.avatar || null;
        // The actions are about a PERSON — greeting a thread and assigning to
        // "everyone" are both things this app has no single answer for, so the
        // words alone are the honest empty state there.
        const actions = [];
        if (who && typeof opts.onGreet === 'function') {
            actions.push(h('button', {
                class: 'btn btn-sm',
                type: 'button',
                onclick: () => { void opts.onGreet(greeting(who.name)); },
            }, GREET_LABEL));
        }
        if (who && typeof opts.onAssign === 'function') {
            actions.push(h('button', {
                class: 'btn btn-sm',
                type: 'button',
                onclick: () => { void opts.onAssign(); },
            }, ASSIGN_LABEL));
        }

        return h('div', {
            class: 'transcript-status is-empty conversation-empty-state',
            'data-status': 'empty',
        },
            // Decorative: the title beside it already names the person.
            who ? BossModAvatar.create({ name: who.name, color: who.color, size: 'lg' }) : null,
            h('p', { class: 'transcript-status-title' }, opts.title || 'No messages yet.'),
            opts.hint ? h('p', { class: 'transcript-status-hint' }, opts.hint) : null,
            actions.length
                ? h('div', { class: 'conversation-empty-actions' }, actions)
                : null);
    }

    return { render, greeting };
})();
