/**
 * BossMod AI — what the agents in one conversation can see, marked on its transcript.
 *
 * Each conversation source (the agent DM and the thread) is wrapped by
 * `attach`: its load also fetches the conversation's window from the server,
 * in parallel — the current conversation notes and the oldest row the agents
 * still see word for word — and turns it into transcript data: one
 * `conversation_notes` row and a `faded` flag on the rows before the window.
 * So the operator sees exactly the agents' context. A wrapper rather than an
 * edit to each source: the marking is the same for both, and the sources stay
 * adapters for their own wire format. Data only: it never touches the DOM.
 */
const BossModConversationWindow = (() => {

    /** What the divider says when the agents read notes instead of older rows. */
    const COMPACTED_TITLE = 'Conversation compacted — agents see the notes below instead of the messages above';
    /** What it says when older rows fell off with no notes (compaction failing or off). */
    const CEILING_TITLE = 'Older messages are not in agents’ context';

    /**
     * The divider's stable key, the same `conversation-notes:<kind>:<key>`
     * id the prompt gives the notes entry.
     * @param {{kind: string, key: string}} scope
     * @returns {string}
     */
    function notesKey(scope) {
        return `conversation-notes:${scope.kind}:${scope.key}`;
    }

    /**
     * A DOM-safe id for the divider, so faded rows can point at it.
     * @param {{kind: string, key: string}} scope
     * @returns {string}
     */
    function dividerId(scope) {
        return `conversation-notes-${scope.kind}-${scope.key}`.replace(/[^A-Za-z0-9_-]/g, '-');
    }

    /**
     * The window API's answer for one conversation.
     * @param {Function} api  The injected authenticated fetch helper.
     * @param {{kind: string, key: string}} scope
     * @returns {Promise<object>} `{notes, visible_from_message_id, ceiling_hit}`.
     * @throws {Error} With the server's text on a non-OK response: a
     *   transcript painted without its window would claim the agents see
     *   rows they do not.
     */
    async function fetchWindow(api, scope) {
        const res = await api(
            `/api/conversations/${encodeURIComponent(scope.kind)}/${encodeURIComponent(scope.key)}/window`,
            { cache: 'no-store' },
        );
        if (!res.ok) throw new Error((await res.text()) || 'Could not load what the agents can see.');
        return res.json();
    }

    /**
     * Whether a `conversation_notes_updated` payload is about this scope.
     * @param {object} data
     * @param {{kind: string, key: string}} scope
     * @returns {boolean}
     */
    function matches(data, scope) {
        return Boolean(data) && data.scope_kind === scope.kind && data.scope_key === scope.key;
    }

    /**
     * Wrap one conversation source so its transcript marks the agents' window.
     *
     * `load` fetches the source's rows and the window in parallel and returns
     * the marked rows; `subscribe` turns this conversation's
     * `conversation_notes_updated` into `on.refresh()` (a full reload) and
     * passes every other topic to the source. Everything else is the
     * source's own.
     *
     * @param {Function} api  The injected authenticated fetch helper.
     * @param {'human_dm'|'channel'} kind  The window scope kind; the key is
     *   the source's `id` (the agent id, or the thread id).
     * @param {object} source  A ConversationSource.
     * @returns {object} The wrapped ConversationSource.
     * @throws {Error} When the source has no id.
     */
    function attach(api, kind, source) {
        if (!source || !source.id) throw new Error('[conversation-window] the source has no id');
        const scope = { kind, key: String(source.id) };
        return Object.assign({}, source, {
            async load() {
                const [messages, seen] = await Promise.all([source.load(), fetchWindow(api, scope)]);
                return apply(messages, seen, scope);
            },
            subscribe(on) {
                const live = source.subscribe(on);
                return {
                    dispose: live.dispose,
                    onLiveEvent(topic, data) {
                        if (topic !== 'conversation_notes_updated') {
                            live.onLiveEvent(topic, data);
                        } else if (matches(data, scope)) {
                            on.refresh();
                        }
                    },
                };
            },
        });
    }

    /**
     * The open/closed state of the notes dividers, by scope. The conversation
     * controller owns one: every repaint rebuilds the divider's node, and a
     * rebuilt divider must not snap shut under the operator.
     * @returns {{isOpen: (scopeKey: string) => boolean,
     *            setOpen: (scopeKey: string, open: boolean) => void}}
     */
    function createDisclosure() {
        const open = new Set();
        return {
            isOpen: (scopeKey) => open.has(scopeKey),
            setOpen: (scopeKey, next) => {
                if (next) open.add(scopeKey);
                else open.delete(scopeKey);
            },
        };
    }

    function indexOfKey(messages, key) {
        if (!key) return -1;
        return messages.findIndex((message) => String(message.key || '').trim() === String(key));
    }

    /**
     * Mark one transcript with the conversation's window.
     *
     * - Notes present: the divider goes right after the last summarized row
     *   (`through_message_id`) and carries the notes text.
     * - No notes, but older rows past the hard limit: the divider goes right
     *   before the first row the agents see.
     * - No notes and nothing dropped, but the last run failed: the divider
     *   carries only the failure, at the end, where the operator is reading.
     *
     * The rows before the first row the agents see are faded. A row the
     * transcript does not show (a thread's hidden round marker, or one older
     * than the loaded page) cannot be found by key: the divider then goes
     * before the first visible row, else at the top, and the fading stops at
     * the divider. Without a hard-limit drop the anchor and the first visible
     * row are neighbours, so both positions are exact.
     *
     * @param {object[]} messages  Normalised Messages, oldest first.
     * @param {{notes: object|null, visible_from_message_id: string|null,
     *          ceiling_hit: boolean}} window  From the window API.
     * @param {{kind: string, key: string}} scope
     * @returns {object[]} A new array; the input rows are copied, not mutated.
     * @throws {Error} When the window payload is not an object.
     */
    function apply(messages, window, scope) {
        if (!window || typeof window !== 'object') {
            throw new Error('[conversation-window] the window payload is missing');
        }
        const rows = (Array.isArray(messages) ? messages : []).map((message) => Object.assign({}, message));
        const notes = window.notes || null;
        const notesText = notes && notes.notes && notes.through_message_id ? String(notes.notes) : '';
        const failure = notes && notes.run_state === 'failed' ? String(notes.last_error || '').trim() : '';
        const visibleIndex = indexOfKey(rows, window.visible_from_message_id);
        let title = '';
        if (notesText) title = COMPACTED_TITLE;
        else if (window.ceiling_hit === true) title = CEILING_TITLE;
        if (!title && !failure) return rows;

        let at = rows.length;
        if (notesText) {
            const anchor = indexOfKey(rows, notes.through_message_id);
            at = anchor >= 0 ? anchor + 1 : Math.max(visibleIndex, 0);
        } else if (title) {
            at = Math.max(visibleIndex, 0);
        }
        const id = dividerId(scope);
        const fadeUntil = title ? (visibleIndex >= 0 ? visibleIndex : at) : 0;
        for (let i = 0; i < fadeUntil; i += 1) {
            if (rows[i].kind === 'message') {
                rows[i].faded = true;
                rows[i].describedBy = id;
            }
        }
        rows.splice(at, 0, {
            key: notesKey(scope),
            kind: 'conversation_notes',
            author: 'system',
            authorName: '',
            text: notesText,
            title,
            failure,
            scopeKey: `${scope.kind}:${scope.key}`,
            dividerId: id,
            createdAt: '',
            systemReceipt: false,
            live: false,
            cleared: false,
        });
        return rows;
    }

    return {
        attach, apply, fetchWindow, matches, createDisclosure, notesKey, dividerId,
        COMPACTED_TITLE, CEILING_TITLE,
    };
})();
