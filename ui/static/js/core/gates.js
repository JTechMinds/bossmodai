/**
 * BossMod AI — submit, load, and presence gates.
 *
 * Four small state machines that stop the UI lying to the operator: a load
 * generation so a stale response is dropped rather than painted, a send gate
 * that keeps the draft until the server acknowledges, a one-at-a-time action
 * gate, and a presence model keyed by conversation.
 *
 * Moved out of utils.js unchanged; every function here is covered by an
 * existing Node harness.
 *
 * The predicate-guarded chat typing controller that used to live here was
 * deleted in Phase 2B with its last caller, agent-context.js. Presence is
 * keyed by conversation instead, which partitions state structurally rather
 * than guarding it with an "is this the active chat" test (spec 4.1).
 */
const BossModGates = (() => {

    /**
     * A monotonic generation counter for async loads.
     *
     * @returns {{ next: () => number, isCurrent: (id: number) => boolean }}
     *   `next()` invalidates every id handed out before it; `isCurrent(id)`
     *   says whether a settled response may still be painted.
     */
    function createLoadGeneration() {
        let current = 0;
        return {
            next() {
                current += 1;
                return current;
            },
            isCurrent(id) {
                return id === current;
            },
        };
    }

    /**
     * Show or clear a composer's inline error line.
     *
     * @param {HTMLElement|null} el  Error node; null is a no-op, so a composer
     *   without an error line still sends.
     * @param {string} message  Empty string hides the line.
     * @returns {void}
     */
    function setComposerError(el, message) {
        if (!el) return;
        const text = String(message || '').trim();
        el.textContent = text;
        el.classList.toggle('hidden', !text);
    }

    /**
     * A composer submit gate that never freezes the field and never loses a draft.
     *
     * Accepting a send takes that text out of the box immediately so the
     * operator can type the next line while agents are still thinking. The
     * field and Send stay enabled. A second send waits behind the first and
     * posts in order — it is not dropped. The box is not grayed out.
     *
     * A rejection puts the text back when the operator has not started a
     * newer draft, and reports through `onError`. A newer draft is left
     * alone. `onQueued` receives how many sends are still unacknowledged
     * so the composer can show a quiet hint.
     *
     * @returns {{ busy: () => boolean, submit: (opts: object) => Promise<object> }}
     *   `submit` resolves `{submitted, ok, reason?, error?}` and never throws;
     *   `reason` is `'blocked'` or `'empty'` when nothing was accepted.
     */
    function createComposerSendGate() {
        let pending = 0;
        let tail = Promise.resolve();

        function busy() {
            return pending > 0;
        }

        function noteQueued(onQueued) {
            if (typeof onQueued === 'function') onQueued(pending);
        }

        async function submit({
            input,
            send,
            applyIdleState,
            onSuccess,
            onError,
            onQueued,
            canSubmit,
        } = {}) {
            if (typeof canSubmit === 'function' && !canSubmit()) {
                return { submitted: false, ok: false, reason: 'blocked' };
            }
            const draft = String(input && input.value != null ? input.value : '').trim();
            if (!draft) return { submitted: false, ok: false, reason: 'empty' };
            if (typeof send !== 'function') {
                return { submitted: false, ok: false, reason: 'blocked' };
            }

            // Free the field now. Do not disable it — a gray box traps the
            // next line for as long as this post takes.
            if (input) {
                input.value = '';
                if (input.style) input.style.height = 'auto';
            }
            if (typeof applyIdleState === 'function') applyIdleState();

            pending += 1;
            noteQueued(onQueued);

            const run = async () => {
                try {
                    await send(draft);
                    if (typeof onSuccess === 'function') onSuccess(draft);
                    return { submitted: true, ok: true };
                } catch (err) {
                    const current = String(input && input.value != null ? input.value : '').trim();
                    if (input && !current) {
                        input.value = draft;
                        if (typeof applyIdleState === 'function') applyIdleState();
                    }
                    if (typeof onError === 'function') onError(err, draft);
                    return { submitted: true, ok: false, error: err };
                } finally {
                    pending -= 1;
                    noteQueued(onQueued);
                }
            };

            const result = tail.then(run);
            tail = result.then(() => {}, () => {});
            return result;
        }

        return { busy, submit };
    }

    /**
     * Run at most one action at a time.
     *
     * @returns {{ busy: () => boolean, run: (fn: Function) => Promise<object> }}
     *   `run` resolves `{started: false, reason: 'in-flight'|'blocked'}` when it
     *   refused, and rethrows whatever `fn` rejects with after clearing the
     *   gate — a failed action must never wedge the control.
     */
    function createInFlightGate() {
        let inFlight = false;

        function busy() {
            return inFlight;
        }

        async function run(fn) {
            if (inFlight) return { started: false, reason: 'in-flight' };
            if (typeof fn !== 'function') return { started: false, reason: 'blocked' };
            inFlight = true;
            try {
                const value = await fn();
                return { started: true, value };
            } finally {
                inFlight = false;
            }
        }

        return { busy, run };
    }

    /**
     * Who is mid-turn, keyed `conversationId::agentId`.
     *
     * Partitioning by conversation is what makes presence survive a switch:
     * state cannot leak between conversations because it is separated, not
     * guarded by an "is this the active chat" predicate.
     *
     * @returns {{ start: Function, stop: Function, stopAll: Function,
     *             list: Function, has: Function }}
     *   start/stop/has take (conversationId, agentId); stopAll and list take a
     *   conversationId. Every one returns false / 0 / [] for a missing id
     *   rather than throwing: a presence signal for a conversation that has
     *   since been archived is expected traffic, not an error.
     */
    function createChannelPresenceController() {
        const members = new Map();

        function key(channelId, agentId) {
            return `${channelId}::${agentId}`;
        }

        function start(channelId, agentId, agentName, options) {
            if (!channelId || !agentId) return false;
            const opts = options || {};
            const ahead = Number(opts.ahead);
            members.set(key(channelId, agentId), {
                channelId: String(channelId),
                agentId: String(agentId),
                name: agentName || 'Agent',
                phase: opts.phase === 'queued' ? 'queued' : 'thinking',
                ahead: Number.isFinite(ahead) ? Math.max(0, ahead) : 0,
            });
            return true;
        }

        function stop(channelId, agentId) {
            if (!channelId || !agentId) return false;
            return members.delete(key(channelId, agentId));
        }

        function stopAll(channelId) {
            if (!channelId) return 0;
            const prefix = `${channelId}::`;
            let removed = 0;
            for (const itemKey of Array.from(members.keys())) {
                if (itemKey.startsWith(prefix)) {
                    members.delete(itemKey);
                    removed += 1;
                }
            }
            return removed;
        }

        function list(channelId) {
            if (!channelId) return [];
            return Array.from(members.values()).filter((item) => item.channelId === String(channelId));
        }

        function has(channelId, agentId) {
            if (!channelId || !agentId) return false;
            return members.has(key(channelId, agentId));
        }

        return { start, stop, stopAll, list, has };
    }

    return {
        createLoadGeneration,
        setComposerError,
        createComposerSendGate,
        createInFlightGate,
        createChannelPresenceController,
    };
})();
