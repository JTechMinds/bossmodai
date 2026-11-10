/**
 * BossMod AI — submit and load gates.
 *
 * Three small state machines that stop the UI lying to the operator: a load
 * generation so a stale response is dropped rather than painted, a send gate
 * that keeps the draft until the server acknowledges, and a one-at-a-time
 * action gate.
 *
 * Moved out of utils.js unchanged; every function here is covered by an
 * existing Node harness.
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
            hasPayload,
        } = {}) {
            if (typeof canSubmit === 'function' && !canSubmit()) {
                return { submitted: false, ok: false, reason: 'blocked' };
            }
            const draft = String(input && input.value != null ? input.value : '').trim();
            if (!draft && !hasPayload) return { submitted: false, ok: false, reason: 'empty' };
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

    return {
        createLoadGeneration,
        setComposerError,
        createComposerSendGate,
        createInFlightGate,
    };
})();
