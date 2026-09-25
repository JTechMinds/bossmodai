/**
 * BossMod AI — one invalidate path for operator-owned surfaces.
 *
 * Agent events and server-side operator mutations fan in here. Each open
 * surface registers how it repaints; the bus is wired once at boot so places
 * do not each subscribe in their own shape.
 *
 * Fail-closed: a handler that throws is logged; a refetch that fails must say
 * so on the surface — never silent success with invisible UI.
 */
const BossModOperatorInvalidate = (() => {

    /** @type {Map<string, object>} */
    const registry = new Map();
    /** @type {{disposers: (() => void)[], wired: Set<string>}[]} */
    const attachments = [];

    /**
     * @param {string} topic
     * @param {any} data
     */
    function dispatch(topic, data) {
        for (const reg of registry.values()) {
            if (!reg.topics.has(topic)) continue;
            try {
                reg.onEvent(topic, data);
            } catch (err) {
                console.error(`[operator-invalidate] "${reg.id}" threw on "${topic}"`, err);
            }
        }
    }

    /**
     * @param {{disposers: (() => void)[], wired: Set<string>}} attachment
     * @param {object} bus
     * @param {string} topic
     */
    function wireTopic(attachment, bus, topic) {
        if (attachment.wired.has(topic)) return;
        attachment.wired.add(topic);
        attachment.disposers.push(bus.subscribe(topic, (data) => dispatch(topic, data)));
    }

    /**
     * @param {object} bus
     * @param {Set<string>} topics
     */
    function wireAllTopics(bus, topics) {
        const attachment = { disposers: [], wired: new Set() };
        topics.forEach((topic) => wireTopic(attachment, bus, topic));
        attachments.push(attachment);
        return () => {
            attachment.disposers.splice(0).forEach((off) => off());
            const at = attachments.indexOf(attachment);
            if (at !== -1) attachments.splice(at, 1);
        };
    }

    /**
     * @param {object} spec
     * @param {string} spec.id  Unique for this registration.
     * @param {string[]} spec.topics  Bus topics this handler listens to.
     * @param {(topic: string, data: any) => void} spec.onEvent
     * @returns {() => void} disposer
     */
    function register({ id, topics, onEvent }) {
        if (!id) throw new Error('[operator-invalidate] register() needs an id');
        if (typeof onEvent !== 'function') {
            throw new Error('[operator-invalidate] register() needs onEvent');
        }
        const topicSet = new Set(topics || []);
        if (!topicSet.size) throw new Error('[operator-invalidate] register() needs topics');
        registry.set(id, { id, topics: topicSet, onEvent });
        if (attachments.length) {
            attachments.forEach((entry) => {
                const attachedBus = entry._bus;
                if (!attachedBus) return;
                topicSet.forEach((topic) => wireTopic(entry, attachedBus, topic));
            });
        }
        return () => {
            registry.delete(id);
        };
    }

    /**
     * Publish a local invalidate after this tab's own mutation landed.
     *
     * @param {string[]} surfaces
     */
    function notifyLocal(surfaces) {
        if (!attachments.length) return;
        dispatch('operator_invalidate', { surfaces: surfaces || [], local: true });
    }

    /**
     * Wire the bus once at boot, before the socket connects.
     *
     * @param {object} deps
     * @param {object} deps.bus
     * @returns {() => void} disposer
     */
    function attach({ bus }) {
        if (!bus) throw new Error('[operator-invalidate] attach() needs deps.bus');
        const topics = new Set();
        registry.forEach((reg) => reg.topics.forEach((topic) => topics.add(topic)));
        const off = wireAllTopics(bus, topics);
        const entry = attachments[attachments.length - 1];
        entry._bus = bus;
        return off;
    }

    /**
     * @param {string[]} surfaces
     * @param {any} data
     * @returns {boolean}
     */
    function touchesSurface(surfaces, data) {
        const wanted = Array.isArray(surfaces) ? surfaces : [];
        const payload = (data && data.surfaces) || [];
        if (!wanted.length) return payload.length > 0;
        return wanted.some((name) => payload.indexOf(name) !== -1);
    }

    return { attach, register, notifyLocal, touchesSurface };
})();
