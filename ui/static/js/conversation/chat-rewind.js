/**
 * BossMod AI — the Rewind flow for an agent DM or a thread.
 *
 * The one owner of Rewind: its two entry points (the header's `⋯`, and on an
 * agent DM the red error card above the composer), the dialog it opens, and
 * what happens after the server has cut the conversation. Each part stays
 * with its job:
 *   - data: the agent or thread source (its rewindPoints and rewind calls);
 *   - presentation: conversation/chat-rewind-dialog.js;
 *   - composition: conversation/conversation.js, which owns the source, the
 *     composer and the needs bar this is handed;
 *   - flow and side effects: here.
 *
 * Either order of the server's reset broadcast (`chat_reset`, or
 * `channel_reset` for a thread) and the POST response is safe for the
 * composer: the reload that the reset triggers stashes and restores the
 * current draft, and the draft written here is merged into whatever is there.
 */
const BossModChatRewind = (() => {

    /**
     * Build the Rewind flow for one open agent DM or thread.
     *
     * @param {object} deps
     * @param {object} deps.source  The agent or thread source: `id`, `kind`
     *   (`'agent'` or `'thread'`), `canSend()`, `rewindPoints()` and
     *   `rewind(fromMessageId)`.
     * @param {() => string} deps.subjectName  Who the DM is with, or the
     *   thread's name, read when the dialog opens (the roster may name the
     *   agent late, and a thread loads its name).
     * @param {object} deps.composer  `readDraft()` / `setDraft(draft)`.
     * @param {object} deps.needs  The needs store; only `acknowledge(need)` is used.
     * @returns {{menuActions: () => object[], cardActions: (need: object) => object[],
     *            open: (options?: {need?: object}) => Promise<void>}}
     * @throws {Error} When a dependency is missing, the source is neither an
     *   agent nor a thread, or it has no rewind calls or no `canSend`: an
     *   entry point that cannot rewind must not render.
     */
    function createChatRewind(deps) {
        const { source, subjectName, composer, needs } = deps || {};
        if (!source) throw new Error('[chat-rewind] deps.source is required');
        if (source.kind !== 'agent' && source.kind !== 'thread') {
            throw new Error(`[chat-rewind] deps.source.kind must be 'agent' or 'thread', got ${String(source.kind)}`);
        }
        if (typeof source.rewindPoints !== 'function' || typeof source.rewind !== 'function') {
            throw new Error('[chat-rewind] deps.source must offer rewindPoints() and rewind()');
        }
        if (typeof source.canSend !== 'function') throw new Error('[chat-rewind] deps.source must offer canSend()');
        if (typeof subjectName !== 'function') throw new Error('[chat-rewind] deps.subjectName is required');
        if (!composer || typeof composer.readDraft !== 'function' || typeof composer.setDraft !== 'function') {
            throw new Error('[chat-rewind] deps.composer is required');
        }
        if (!needs || typeof needs.acknowledge !== 'function') {
            throw new Error('[chat-rewind] deps.needs (with acknowledge) is required');
        }

        /**
         * The header `⋯` entry. Offered mid-turn too: the most common regret
         * ("sent too fast") is exactly when the agent is busy. Not offered
         * where nothing can be written: a sealed (archived) thread.
         * @returns {object[]} One chrome action, or none.
         */
        function menuActions() {
            if (!source.canSend()) return [];
            return [{
                id: 'conversation-rewind',
                label: 'Rewind…',
                icon: 'rotate-ccw',
                slot: 'menu',
                section: 'chat',
                // Not awaited: the dialog owns its own loading state, and the
                // chrome's busy gate must not hold every header button meanwhile.
                onSelect: () => { void open({}); },
            }];
        }

        /**
         * The error card's entry, for this agent DM's own error needs only. A
         * need names an agent, never a thread, so a thread offers none.
         * @param {object} need  A normalised need (needs/need-shape.js).
         * @returns {object[]} Event-card actions; empty for any other need.
         */
        function cardActions(need) {
            if (source.kind !== 'agent' || !need || need.kind !== 'error' || need.agentId !== source.id) return [];
            return [{ label: 'Rewind…', tone: 'quiet', onSelect: () => { void open({ need }); } }];
        }

        /**
         * Put the operator's rewound text back, without losing what is typed.
         * @param {string} text
         * @returns {void}
         */
        function restoreDraft(text) {
            const current = composer.readDraft();
            const typed = String((current && current.text) || '');
            const attachments = (current && current.attachments) || [];
            composer.setDraft({
                text: typed.trim() ? `${text}\n\n${typed}` : text,
                attachments,
            });
        }

        /**
         * Cut the conversation at `point`, then restore and acknowledge.
         * @param {object} point  The chosen Message.
         * @param {object|null} need  The error need this was opened from.
         * @returns {Promise<{warning: string}>}
         * @throws {Error} The server's refusal; nothing local has changed.
         */
        async function confirm(point, need) {
            const result = await source.rewind(point.key);
            if (point.author === 'human') restoreDraft(point.text);
            if (need) needs.acknowledge(need);
            const unremoved = result && result.unremoved_files;
            return {
                warning: unremoved > 0
                    ? `Rewound, but ${unremoved} attached ${unremoved === 1 ? 'file' : 'files'} `
                        + 'couldn\'t be deleted.'
                    : '',
            };
        }

        /**
         * Open the dialog and load the messages it offers.
         * @param {{need?: object}} [options]  The error need, when opened from
         *   its card: the newest operator message is then preselected.
         * @returns {Promise<void>} Resolves once the rows (or the load error)
         *   are shown; never rejects, the dialog says what went wrong.
         */
        async function open({ need } = {}) {
            const dialog = BossModChatRewindDialog.open({
                kind: source.kind,
                subjectName: subjectName(),
                preselectLatestHuman: Boolean(need),
                onConfirm: (point) => confirm(point, need || null),
            });
            let points;
            try {
                points = await source.rewindPoints();
            } catch (err) {
                console.error('[chat-rewind] could not load the messages to rewind', err);
                dialog.setLoadError((err && err.message) || 'Could not load this conversation.');
                return;
            }
            dialog.setPoints(points);
        }

        return { menuActions, cardActions, open };
    }

    return { createChatRewind };
})();
