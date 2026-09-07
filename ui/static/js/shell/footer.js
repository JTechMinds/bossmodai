/**
 * BossMod AI — the status footer.
 *
 * Connection state, live agent count, and runtime uptime. It reads the store
 * and the bus; it fetches nothing of its own.
 *
 * The reconnect notice is the point of this module. The WebSocket has no
 * replay buffer, so everything broadcast during an outage is gone. Showing
 * "Connected" the instant the socket re-opens would tell the operator the UI
 * is current when it is not, so the footer says "Reconnected — refreshing"
 * from the moment `resync` arrives until the shell's refetches settle.
 */
const BossModFooter = (() => {
    const { h, clear } = BossModDom;

    const RESYNC_LABEL = 'Reconnected — refreshing';

    /**
     * Render the uptime as the coarsest unit that still reads as a duration.
     * @param {number|null} startedAt Epoch ms, or null when unknown.
     * @returns {string}
     */
    function formatUptime(startedAt) {
        if (startedAt === null) return '--';
        const totalSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        const pad = (n) => String(n).padStart(2, '0');
        if (hours > 0) return `${hours}h ${pad(minutes)}m`;
        if (minutes > 0) return `${minutes}m ${pad(seconds)}s`;
        return `${seconds}s`;
    }

    /**
     * @param {string} connection 'connecting' | 'connected' | 'resyncing' | 'disconnected'
     * @param {boolean} paused
     * @param {boolean} resyncing True from the `resync` event until the store
     *   reports a settled connection again.
     * @returns {{state: string, label: string}}
     */
    function describe(connection, paused, resyncing) {
        if (connection === 'disconnected') return { state: 'disconnected', label: 'Disconnected' };
        if (connection === 'connecting') return { state: 'connecting', label: 'Connecting…' };
        if (resyncing || connection === 'resyncing') return { state: 'resyncing', label: RESYNC_LABEL };
        if (paused) return { state: 'paused', label: 'Paused' };
        return { state: 'connected', label: 'Connected' };
    }

    /**
     * Render the footer into `el`.
     *
     * @param {HTMLElement} el
     * @param {object} deps
     * @param {object} deps.store
     * @param {object} deps.bus
     * @returns {() => void} disposer — drains subscriptions and the interval.
     */
    function mount(el, deps) {
        const { store, bus } = deps;
        const disposers = [];

        // Owned here rather than in the store: it is server data this one
        // component reads, and nothing else needs it.
        let startedAt = null;
        // Set by `resync`, cleared when the shell reports the refetches done.
        let resyncing = false;

        clear(el);

        const dot = h('span', { class: 'footer-status-dot', 'data-state': 'connecting', 'aria-hidden': 'true' });
        const statusLabel = h('span', { class: 'footer-status-label' });
        const agentCount = h('span', { class: 'footer-agent-count' });
        const uptime = h('span', { class: 'footer-uptime' });

        el.append(
            h('span', { class: 'footer-copyright' }, 'copyright(c) 2026 JTechMinds LLC'),
            h('div', { class: 'footer-status-group' },
                h('span', { class: 'footer-runtime-status' }, dot, statusLabel),
                agentCount,
                uptime));

        function applyStatus() {
            const state = store.getState();
            const shown = describe(state.connection, state.runtimePaused === true, resyncing);
            dot.setAttribute('data-state', shown.state);
            clear(statusLabel);
            statusLabel.append(shown.label);
        }

        function applyAgentCount(roster) {
            const count = roster.length;
            clear(agentCount);
            agentCount.append(`${count} agent${count === 1 ? '' : 's'}`);
        }

        function applyUptime() {
            clear(uptime);
            uptime.append(formatUptime(startedAt));
        }

        disposers.push(bus.subscribe('resync', () => {
            resyncing = true;
            applyStatus();
        }));
        disposers.push(store.subscribe((s) => s.connection, (value) => {
            // The shell flips connection to 'resyncing' while it refetches and
            // back to 'connected' when they settle; that is what ends the notice.
            if (value === 'connected') resyncing = false;
            applyStatus();
        }));
        disposers.push(store.subscribe((s) => s.runtimePaused, applyStatus));
        disposers.push(store.subscribe((s) => s.roster, applyAgentCount));
        disposers.push(bus.subscribe('runtime_state', (payload) => {
            const iso = payload.started_at || (payload.worker && payload.worker.started_at) || null;
            startedAt = iso === null ? null : new Date(iso).getTime();
            applyUptime();
        }));

        const interval = setInterval(applyUptime, 1000);
        disposers.push(() => clearInterval(interval));

        applyStatus();
        applyAgentCount(store.getState().roster);
        applyUptime();

        return () => { disposers.splice(0).forEach((off) => off()); };
    }

    return { mount };
})();
