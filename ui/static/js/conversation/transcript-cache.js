/**
 * BossMod AI — the last-loaded transcript per conversation.
 *
 * Split out of transcript.js, which owns the DOM. This owns none of it: it is
 * a Map of message arrays and the rules for reading them back safely. The two
 * were one file until the presence row grew a duration and pushed it past the
 * cap, and the seam was already there — retention is not rendering.
 *
 * It borrows `messageKey` from the view rather than reimplementing it. Two
 * opinions about what makes two messages the same message is exactly how a
 * cache starts disagreeing with the screen it is caching.
 */
const BossModTranscriptCache = (() => {
    /**
     * Last-loaded transcript per conversation, so a re-click is not cold.
     *
     * @returns {{ remember: (id: string, messages: object[]) => void,
     *             recall: (id: string) => object[]|null,
     *             forget: (id: string) => boolean,
     *             append: (id: string, message: object) => boolean }}
     *   `recall` returns a COPY: a caller that mutates what it got back must
     *   not silently rewrite what the next switch will paint. `append` returns
     *   false for an unknown id or a key already cached; a keyless message is
     *   always cached, matching the transcript's own dedupe rule.
     */
    function createCache() {
        const items = new Map();

        function normalize(conversationId) {
            return String(conversationId || '').trim();
        }

        function remember(conversationId, messages) {
            const id = normalize(conversationId);
            if (!id) return;
            items.set(id, Array.isArray(messages) ? messages.slice() : []);
        }

        function recall(conversationId) {
            const id = normalize(conversationId);
            if (!id) return null;
            const entry = items.get(id);
            return entry ? entry.slice() : null;
        }

        function forget(conversationId) {
            const id = normalize(conversationId);
            if (!id) return false;
            return items.delete(id);
        }

        function append(conversationId, message) {
            const id = normalize(conversationId);
            if (!id || !message) return false;
            const entry = items.get(id);
            if (!entry) return false;
            const key = BossModTranscript.messageKey(message);
            if (key) {
                const idx = entry.findIndex((item) => BossModTranscript.messageKey(item) === key);
                if (idx >= 0) {
                    if (message.cleared) {
                        entry.splice(idx, 1);
                        return true;
                    }
                    if (message.live) {
                        entry[idx] = message;
                        return true;
                    }
                    return false;
                }
            }
            if (message.cleared) return false;
            entry.push(message);
            return true;
        }

        return { remember, recall, forget, append };
    }

    return { createCache };
})();
