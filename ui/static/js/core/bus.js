/**
 * BossMod AI — WebSocket topic bus.
 *
 * Replaces the old switch of `typeof X !== 'undefined'` guards in app.js,
 * which silently no-opped when a module failed to load. Here an unknown topic
 * throws, so a typo or a missing script fails at mount instead of going quiet.
 */
const BossModBus = (() => {

    /**
     * Server message types from api/websocket.py, plus `resync`, which is
     * published client-side by shell/socket.js after a dropped connection.
     */
    const KNOWN_TOPICS = Object.freeze([
        'world_update',
        'runtime_state',
        'chat_message',
        'chat_reset',
        'meeting_message',
        'channel_message',
        'channel_presence',
        'channel_updated',
        'diagnostic',
        'agent_thought',
        'activity',
        'activity_update',
        'unified_feed',
        'resync',
    ]);

    /**
     * @param {string[]} knownTopics
     * @returns {{
     *   publish: (topic: string, data: any) => void,
     *   subscribe: (topic: string, fn: (data: any) => void) => (() => void),
     *   subscriberCount: () => number
     * }}
     */
    function createBus(knownTopics) {
        const known = new Set(knownTopics);
        const topics = new Map();

        function assertKnown(topic) {
            if (!known.has(topic)) {
                throw new Error(
                    `[bus] unknown topic "${topic}". Known topics: ${Array.from(known).join(', ')}`
                );
            }
        }

        function publish(topic, data) {
            assertKnown(topic);
            const handlers = topics.get(topic);
            if (!handlers) return;
            for (const fn of Array.from(handlers)) {
                try {
                    fn(data);
                } catch (err) {
                    console.error(`[bus] subscriber to "${topic}" threw`, err);
                }
            }
        }

        /**
         * @returns {() => void} disposer — callers MUST call this on unmount
         */
        function subscribe(topic, fn) {
            assertKnown(topic);
            if (!topics.has(topic)) topics.set(topic, new Set());
            const handlers = topics.get(topic);
            handlers.add(fn);
            return () => {
                handlers.delete(fn);
                if (handlers.size === 0) topics.delete(topic);
            };
        }

        function subscriberCount() {
            let total = 0;
            for (const handlers of topics.values()) total += handlers.size;
            return total;
        }

        return { publish, subscribe, subscriberCount };
    }

    return { createBus, KNOWN_TOPICS };
})();
