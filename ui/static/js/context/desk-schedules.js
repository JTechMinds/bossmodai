/**
 * BossMod AI — the Schedules section of an agent's desk.
 *
 * One row per schedule: its title, its summary ("Every weekday at 06:00,
 * 12:00"), when it runs next ("Next: Tue 06:00", or "Off"), and a tone when
 * the last run went wrong ("Missed 06:00, computer was asleep" / "Last run
 * failed" / "Paused: last run still open"). A row opens the schedule as a layer over the desk
 * (context/schedule-layer.js); the section header's "New" opens a new one
 * (`openNew`, called by desk-panel.js, which owns the header).
 *
 * It repaints itself when the runtime worker ran one of this agent's
 * schedules (`schedule_ran`) or the app changed one (`schedule_changed`),
 * both activity events carrying `agent_id`; bursts are debounced. Content
 * only, like the other desk sections: skeleton, error with a retry, empty.
 */
const BossModDeskSchedules = (() => {
    const { h, clear } = BossModDom;

    const EVENTS = Object.freeze(['schedule_ran', 'schedule_changed']);
    /** Several runs or edits in a burst repaint once (the Tasks place's delay). */
    const REFRESH_DELAY_MS = 500;
    const WEEKDAYS = Object.freeze(['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']);

    /**
     * `Tue 06:00`, or `06:00` alone: an ISO instant on the operator's clock.
     * @throws {Error} When the server sent an unreadable time.
     */
    function localTime(iso, withDay) {
        const at = new Date(iso);
        if (Number.isNaN(at.getTime())) throw new Error(`[desk-schedules] unreadable time ${iso}`);
        const clock = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
        return withDay ? `${WEEKDAYS[at.getDay()]} ${clock}` : clock;
    }

    /** The tone line for a last run that went wrong, or null. */
    function toneLine(schedule) {
        if (schedule.last_outcome === 'missed' && schedule.last_occurrence_at) {
            return `Missed ${localTime(schedule.last_occurrence_at, false)}, computer was asleep`;
        }
        if (schedule.last_outcome === 'failed') return 'Last run failed';
        // An open run makes every later run skip; a schedule doing nothing must not look healthy.
        if (schedule.last_outcome === 'skipped_open') return 'Paused: last run still open';
        return null;
    }

    /**
     * Build the section.
     *
     * @param {object} deps
     * @param {Function} deps.api  Authenticated fetch helper.
     * @param {object} deps.bus  Topic bus; `activity` repaints the rows.
     * @param {string} deps.agentId
     * @param {() => string} deps.agentName  The agent's current name.
     * @param {(taskId: string) => void} deps.onOpenTask  Opens a run's task
     *   (the desk's task opener, a layer over the desk).
     * @returns {{ element: HTMLElement, refresh: () => Promise<void>,
     *   openNew: () => void, destroy: () => void }} `destroy` unsubscribes,
     *   drops an in-flight read and closes an open schedule layer.
     * @throws {Error} When any dependency is missing.
     */
    function createDeskSchedules(deps) {
        const { api, bus, agentId, agentName, onOpenTask } = deps || {};
        if (typeof api !== 'function') throw new Error('[desk-schedules] deps.api is required');
        if (!bus) throw new Error('[desk-schedules] deps.bus is required');
        if (!agentId) throw new Error('[desk-schedules] deps.agentId is required');
        if (typeof agentName !== 'function') throw new Error('[desk-schedules] deps.agentName is required');
        if (typeof onOpenTask !== 'function') throw new Error('[desk-schedules] deps.onOpenTask is required');

        const load = BossModGates.createLoadGeneration();
        const element = h('div', { class: 'desk-schedules' });
        let destroyed = false;
        let refreshTimer = null;
        /** The open schedule layer, or null. One at a time: it covers the desk. */
        let layer = null;

        function openLayer(schedule) {
            if (layer) return;
            layer = BossModScheduleLayer.open({
                api, agentId, agentName, schedule, onOpenTask,
                onChanged: () => { void refresh(); },
                onClose: () => { layer = null; },
            });
        }

        function row(schedule) {
            const tone = toneLine(schedule);
            return h('button', {
                class: 'desk-schedule', type: 'button', 'data-schedule-id': schedule.id,
                onclick: () => openLayer(schedule),
            },
                h('i', { 'data-lucide': 'calendar-clock', 'aria-hidden': 'true' }),
                h('span', { class: 'desk-schedule-main' },
                    h('span', { class: 'desk-schedule-title' }, String(schedule.title)),
                    h('span', { class: 'desk-schedule-meta' }, String(schedule.summary)),
                    tone ? h('span', { class: 'desk-schedule-tone', 'data-tone': 'alert' }, tone) : null),
                h('span', { class: 'desk-schedule-next' },
                    schedule.enabled && schedule.next_run_at ? `Next: ${localTime(schedule.next_run_at, true)}` : 'Off'));
        }

        /**
         * Re-read this agent's schedules and paint them.
         * @returns {Promise<void>} Never rejects; a failure is the error state.
         */
        async function refresh() {
            const loadId = load.next();
            if (!element.children.length) element.append(h('p', { class: 'context-skeleton' }, 'Loading schedules…'));
            let schedules;
            try {
                schedules = await BossModScheduleApi.list(api, agentId);
            } catch (err) {
                if (destroyed || !load.isCurrent(loadId)) return;
                console.error('[desk-schedules] could not load the schedules', err);
                clear(element);
                element.append(
                    h('p', { class: 'context-error', role: 'alert' }, 'Could not load schedules.'),
                    h('button', {
                        class: 'btn btn-sm', type: 'button', id: 'desk-schedules-retry',
                        onclick: () => { void refresh(); },
                    }, 'Try again'));
                return;
            }
            if (destroyed || !load.isCurrent(loadId)) return;
            clear(element);
            if (!schedules.length) {
                element.append(h('p', { class: 'context-empty empty-slot' }, 'No schedules yet.'));
                return;
            }
            element.append(...schedules.map(row));
            // Rebuilt per read, so this section paints its own glyphs.
            BossModIcons.paint(element, 'desk-schedules');
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
            /** Open a new schedule for this agent, straight in edit mode. */
            openNew() {
                openLayer(null);
            },
            destroy() {
                destroyed = true;
                load.next();
                clearTimeout(refreshTimer);
                unsubscribe();
                if (layer) layer.close();
                layer = null;
            },
        };
    }

    return { createDeskSchedules };
})();
