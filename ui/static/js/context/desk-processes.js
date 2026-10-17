/**
 * BossMod AI — the Processes section of an agent's desk.
 *
 * The background processes the agent started by ending a command with `&`
 * (core/bm_cli/background_processes.py): the running ones first, then the
 * newest ended ones (GET /api/agents/{id}/processes). One row each: the
 * command, where it runs, and "Running for 2h 5m" or "Ended 5m ago · <why>",
 * with a "Check-in waiting" tone while the operator is being asked whether
 * to keep it. A running row offers Stop (POST /api/agent-processes/{id}/stop);
 * every row offers Log, which opens the end of its output as a layer over
 * the desk (BossModOverlays.createModal), read fresh when it opens.
 *
 * It repaints itself when this agent's processes change
 * (`agent_process_changed`) or a check-in is asked (`agent_process_checkin`),
 * both activity events carrying `agent_id`; bursts are debounced. Content
 * only, like the other desk sections: skeleton, error with a retry, empty.
 */
const BossModDeskProcesses = (() => {
    const { h, clear } = BossModDom;

    const EVENTS = Object.freeze(['agent_process_changed', 'agent_process_checkin']);
    /** Several changes in a burst repaint once (the Schedules section's delay). */
    const REFRESH_DELAY_MS = 500;
    const COPY = Object.freeze({
        loading: 'Loading background processes…',
        loadError: 'Could not load background processes.',
        retry: 'Try again',
        empty: 'Nothing running in the background.',
        checkin: 'Check-in waiting',
        stop: 'Stop',
        stopping: 'Stopping…',
        log: 'Log',
        logTitle: 'Background process log',
        logLoading: 'Loading the log…',
        logError: 'Could not load the log.',
        logGone: 'This process is no longer listed.',
        noOutput: 'No output yet.',
    });

    /**
     * Send one request and resolve with its JSON body.
     * @throws {Error} (rejects) With the server's sentence (api-client.js
     *   `formatError`), or the status for a body that is not JSON.
     */
    async function request(api, url, init) {
        const res = await api(url, init);
        const body = await res.json().catch((err) => { throw new Error(`HTTP ${res.status}: ${err.message}`); });
        if (res.ok) return body;
        throw new Error(window.BossModApi.formatError(body, res.status));
    }

    /**
     * One agent's processes: running first, then the newest ended.
     * @returns {Promise<object[]>}
     * @throws {Error} (rejects) On a refusal, or an answer that is not a list.
     */
    async function list(api, agentId) {
        const rows = await request(api, `/api/agents/${encodeURIComponent(agentId)}/processes`, { cache: 'no-store' });
        if (!Array.isArray(rows)) throw new Error('the processes answer is not a list');
        return rows;
    }

    /** "Running for 2h 5m", or "Ended 5m ago · Stopped by you". */
    function stateLine(proc) {
        if (proc.status === 'running') {
            const seconds = (Date.now() - new Date(proc.started_at).getTime()) / 1000;
            return `Running for ${BossModFormat.formatDuration(seconds)}`;
        }
        return `Ended ${BossModFormat.formatRelativeTime(proc.ended_at)} · ${proc.stop_reason}`;
    }

    /**
     * Build the section.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.bus  Topic bus; `activity` repaints the rows.
     * @param {string} deps.agentId
     * @returns {{ element: HTMLElement, refresh: () => Promise<void>, destroy: () => void }}
     *   `destroy` unsubscribes, drops an in-flight read and closes an open log.
     * @throws {Error} When any dependency is missing.
     */
    function createDeskProcesses(deps) {
        const { api, bus, agentId } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-processes] deps.api is required');
        if (!bus) throw new Error('[desk-processes] deps.bus is required');
        if (!agentId) throw new Error('[desk-processes] deps.agentId is required');

        const load = BossModGates.createLoadGeneration();
        const logLoads = BossModGates.createLoadGeneration();
        const listEl = h('div', { class: 'desk-processes' });
        const errorEl = h('p', { class: 'context-error', role: 'alert', hidden: true });
        const element = h('div', { class: 'desk-processes-section' }, errorEl, listEl);
        let destroyed = false;
        let refreshTimer = null;
        /** The open log layer, or null. One at a time: it covers the desk. */
        let layer = null;

        function showError(message) {
            errorEl.textContent = message;
            errorEl.hidden = !message;
        }

        async function stopProcess(proc, button) {
            button.disabled = true;
            button.textContent = COPY.stopping;
            showError('');
            try {
                await request(api, `/api/agent-processes/${encodeURIComponent(proc.id)}/stop`, { method: 'POST' });
            } catch (err) {
                if (destroyed) return;
                console.error('[desk-processes] could not stop the process', err);
                showError(`Could not stop it: ${err.message}`);
                button.disabled = false;
                button.textContent = COPY.stop;
                return;
            }
            if (!destroyed) void refresh();
        }

        /** Put the log layer up, then fill it with a fresh read of this process. */
        function openLog(proc) {
            if (layer || destroyed) return;
            const body = h('div', { class: 'desk-process-log' },
                h('p', { class: 'desk-process-command' }, String(proc.command)),
                h('p', { class: 'context-skeleton' }, COPY.logLoading));
            layer = BossModOverlays.createModal({
                title: COPY.logTitle,
                closeOnBackdrop: true,
                body,
                onClose: () => {
                    layer = null;
                    // A read landing after the layer closed paints nothing.
                    logLoads.next();
                },
            });
            void fillLog(proc, body);
        }

        async function fillLog(proc, body) {
            const loadId = logLoads.next();
            let rows;
            try {
                rows = await list(api, agentId);
            } catch (err) {
                if (!layer || !logLoads.isCurrent(loadId)) return;
                console.error('[desk-processes] could not load the log', err);
                paintLog(body, proc, h('div', {},
                    h('p', { class: 'context-error', role: 'alert' }, COPY.logError),
                    h('button', {
                        class: 'btn btn-sm', type: 'button', id: 'desk-process-log-retry',
                        onclick: () => { void fillLog(proc, body); },
                    }, COPY.retry)));
                return;
            }
            if (!layer || !logLoads.isCurrent(loadId)) return;
            const fresh = rows.find((row) => row && row.id === proc.id);
            if (!fresh) {
                paintLog(body, proc, h('p', { class: 'context-empty' }, COPY.logGone));
                return;
            }
            if (fresh.log_error) {
                paintLog(body, proc, h('p', { class: 'context-error', role: 'alert' }, String(fresh.log_error)));
                return;
            }
            const text = String(fresh.log_tail || '').trimEnd();
            paintLog(body, proc, text
                ? h('pre', { class: 'log-pre', tabindex: '0', 'aria-label': 'Log output' }, text)
                : h('p', { class: 'context-empty' }, COPY.noOutput));
        }

        function paintLog(body, proc, content) {
            clear(body);
            body.append(h('p', { class: 'desk-process-command' }, String(proc.command)), content);
        }

        function row(proc) {
            const running = proc.status === 'running';
            const stopButton = running ? h('button', {
                class: 'btn btn-sm', type: 'button', 'data-action': 'stop',
                'aria-label': `Stop ${proc.command}`,
                onclick: () => { void stopProcess(proc, stopButton); },
            }, COPY.stop) : null;
            return h('div', { class: 'desk-process', 'data-process-id': proc.id, 'data-status': proc.status },
                h('i', { 'data-lucide': 'terminal', 'aria-hidden': 'true' }),
                h('span', { class: 'desk-process-main' },
                    h('span', { class: 'desk-process-command' }, String(proc.command)),
                    h('span', { class: 'desk-process-meta' }, `${proc.cwd} · ${stateLine(proc)}`),
                    running && proc.checkin_requested_at
                        ? h('span', { class: 'desk-process-tone', 'data-tone': 'ask' }, COPY.checkin)
                        : null),
                h('span', { class: 'desk-process-actions' },
                    h('button', {
                        class: 'btn btn-sm', type: 'button', 'data-action': 'log',
                        'aria-label': `Show the log of ${proc.command}`,
                        onclick: () => openLog(proc),
                    }, COPY.log),
                    stopButton));
        }

        /**
         * Re-read this agent's processes and paint them.
         * @returns {Promise<void>} Never rejects; a failure is the error state.
         */
        async function refresh() {
            const loadId = load.next();
            if (!listEl.children.length) listEl.append(h('p', { class: 'context-skeleton' }, COPY.loading));
            let rows;
            try {
                rows = await list(api, agentId);
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[desk-processes] could not load the processes', err);
                clear(listEl);
                listEl.append(
                    h('p', { class: 'context-error', role: 'alert' }, COPY.loadError),
                    h('button', {
                        class: 'btn btn-sm', type: 'button', id: 'desk-processes-retry',
                        onclick: () => { void refresh(); },
                    }, COPY.retry));
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;
            clear(listEl);
            if (!rows.length) {
                listEl.append(h('p', { class: 'context-empty empty-slot' }, COPY.empty));
                return;
            }
            listEl.append(...rows.map(row));
            // Rebuilt per read, so this section paints its own glyphs.
            BossModIcons.paint(listEl, 'desk-processes');
        }

        const unsubscribe = bus.subscribe('activity', (entry) => {
            if (!entry || EVENTS.indexOf(String(entry.event || '')) === -1) return;
            // The server merges the activity's extra fields into the entry.
            if (entry.agent_id !== agentId) return;
            clearTimeout(refreshTimer);
            refreshTimer = setTimeout(() => { void refresh(); }, REFRESH_DELAY_MS);
        });

        void refresh();

        return {
            element,
            refresh,
            destroy() {
                destroyed = true;
                load.next();
                logLoads.next();
                clearTimeout(refreshTimer);
                unsubscribe();
                if (layer) layer.close();
                layer = null;
            },
        };
    }

    return { COPY, createDeskProcesses };
})();
