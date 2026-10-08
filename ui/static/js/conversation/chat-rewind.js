/**
 * BossMod AI — the Rewind flow for an agent DM.
 *
 * The one owner of Rewind: its two entry points (the header's `⋯` and the
 * red error card above the composer), the dialog it opens, and what happens
 * after the server has cut the DM. Each part stays with its job:
 *   - data: the agent source (its rewindPoints and rewind calls);
 *   - presentation: conversation/chat-rewind-dialog.js;
 *   - composition: conversation/conversation.js, which owns the source, the
 *     composer and the needs bar this is handed;
 *   - flow and side effects: here.
 *
 * Either order of the server's `chat_reset` broadcast and the POST response
 * is safe for the composer: the reload that `chat_reset` triggers stashes and
 * restores the current draft, and the draft written here is merged into
 * whatever is there.
 */
const BossModChatRewind = (() => {

    /**
     * Build the Rewind flow for one open agent DM.
     *
     * @param {object} deps
     * @param {object} deps.source  The agent source: `id`, `rewindPoints()`
     *   and `rewind(fromMessageId)`.
     * @param {() => string} deps.agentName  Who the DM is with, read when the
     *   dialog opens (the roster may name the agent late).
     * @param {object} deps.composer  `readDraft()` / `setDraft(draft)`.
     * @param {object} deps.needs  The needs store; only `acknowledge(need)` is used.
     * @returns {{menuAction: () => object, cardActions: (need: object) => object[],
     *            open: (options?: {need?: object}) => Promise<void>}}
     * @throws {Error} When a dependency is missing, or the source has no
     *   rewind calls: an entry point that cannot rewind must not render.
     */
    function createChatRewind(deps) {
        const { source, agentName, composer, needs } = deps || {};
        if (!source) throw new Error('[chat-rewind] deps.source is required');
        if (typeof source.rewindPoints !== 'function' || typeof source.rewind !== 'function') {
            throw new Error('[chat-rewind] deps.source must offer rewindPoints() and rewind()');
        }
        if (typeof agentName !== 'function') throw new Error('[chat-rewind] deps.agentName is required');
        if (!composer || typeof composer.readDraft !== 'function' || typeof composer.setDraft !== 'function') {
            throw new Error('[chat-rewind] deps.composer is required');
        }
        if (!needs || typeof needs.acknowledge !== 'function') {
            throw new Error('[chat-rewind] deps.needs (with acknowledge) is required');
        }

        /**
         * The header `⋯` entry. Always offered, mid-turn included: the most
         * common regret ("sent too fast") is exactly when the agent is busy.
         * @returns {object} A chrome action.
         */
        function menuAction() {
            return {
                id: 'agent-chat-rewind',
                label: 'Rewind…',
                icon: 'rotate-ccw',
                slot: 'menu',
                // Not awaited: the dialog owns its own loading state, and the
                // chrome's busy gate must not hold every header button meanwhile.
                onSelect: () => { void open({}); },
            };
        }

        /**
         * The error card's entry, for this agent's error needs only.
         * @param {object} need  A normalised need (needs/need-shape.js).
         * @returns {object[]} Event-card actions; empty for any other need.
         */
        function cardActions(need) {
            if (!need || need.kind !== 'error' || need.agentId !== source.id) return [];
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
         * Cut the DM at `point`, then restore and acknowledge.
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
                agentName: agentName(),
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

        return { menuAction, cardActions, open };
    }

    return { createChatRewind };
})();
