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
    const MINUTE_MS = 60 * 1000;

    /**
     * Render the uptime at minute resolution.
     *
     * Minutes, not seconds, because a seconds counter changed the DOM every
     * second for the life of the app — a constant wake-up and style recalc on
     * a low-end PC for a number nobody reads to the second.
     *
     * @param {number|null} startedAt Epoch ms, or null when unknown.
     * @param {number} now Epoch ms.
     * @returns {string}
     */
    function formatUptime(startedAt, now) {
        if (startedAt === null) return '--';
        const totalMinutes = Math.max(0, Math.floor((now - startedAt) / MINUTE_MS));
        const hours = Math.floor(totalMinutes / 60);
        const minutes = totalMinutes % 60;
        if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`;
        if (totalMinutes > 0) return `${minutes}m`;
        return '<1m';
    }

    /**
     * Milliseconds until the displayed uptime next changes: the next whole
     * minute since `startedAt`, so the tick lands on the boundary rather than
     * up to a minute late.
     *
     * @param {number} startedAt Epoch ms.
     * @param {number} now Epoch ms.
     * @returns {number}
     */
    function msToNextMinute(startedAt, now) {
        const elapsed = Math.max(0, now - startedAt);
        return MINUTE_MS - (elapsed % MINUTE_MS);
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
     * @returns {() => void} disposer — drains subscriptions, the visibility
     *   listener and the pending uptime tick.
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

        // In place, and only when the text changes: no childList mutation, and
        // no write at all when a tick lands on an unchanged value.
        function applyUptime() {
            const next = formatUptime(startedAt, Date.now());
            if (uptime.textContent !== next) uptime.textContent = next;
        }

        // One pending timer at most. It runs only while the start time is known
        // and the window is visible: a hidden webview has no one to show it to.
        let tickTimer = null;
        function stopTicking() {
            if (tickTimer !== null) clearTimeout(tickTimer);
            tickTimer = null;
        }
        function scheduleTick() {
            stopTicking();
            if (startedAt === null || document.hidden) return;
            tickTimer = setTimeout(() => {
                tickTimer = null;
                applyUptime();
                scheduleTick();
            }, msToNextMinute(startedAt, Date.now()));
        }
        function onVisibilityChange() {
            if (document.hidden) {
                stopTicking();
                return;
            }
            // Catch up on the minutes missed while hidden, then realign.
            applyUptime();
            scheduleTick();
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
            const parsed = iso === null ? null : new Date(iso).getTime();
            if (Number.isNaN(parsed)) {
                // Said, not ticked: a NaN start would schedule a 0ms tick forever.
                console.error('[footer] runtime_state carried an unparseable started_at', iso);
                startedAt = null;
            } else {
                startedAt = parsed;
            }
            applyUptime();
            // A (re)started runtime moves the minute boundary; realign to it.
            scheduleTick();
        }));

        document.addEventListener('visibilitychange', onVisibilityChange);
        disposers.push(() => document.removeEventListener('visibilitychange', onVisibilityChange));
        disposers.push(stopTicking);

        applyStatus();
        applyAgentCount(store.getState().roster);
        applyUptime();

        return () => { disposers.splice(0).forEach((off) => off()); };
    }

    return { mount };
})();
