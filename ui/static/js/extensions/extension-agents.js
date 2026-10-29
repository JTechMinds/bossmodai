/**
 * BossMod AI — the Extensions detail's Agents section: one extension, every agent.
 *
 * Mounted in the Agents section of an enabled extension with per-agent
 * settings (manifest `agent_config`), so the operator sets it up for every
 * agent in one place; the desk's Extensions row stays the per-agent way in.
 * It is a component, not a layer: the controller (extensions-dialog.js)
 * creates it once per opened extension and hands its `element` to the section
 * on every render, so the table, a switch mid-request and focus survive the
 * detail's rebuilds and tab switches. The section panel scrolls it. Built from
 * one read (BossModExtensionsApi.extensionAgents), so it knows nothing about
 * any one extension:
 *   - the extension's help text first, as text, one paragraph per blank line;
 *   - a table: Agent (face and name) | Status ("On", "On · <summary>", "Off")
 *     | On (a switch) | Settings (a button, only while on).
 *
 * The switch, driven by the field schema the server summarises:
 *   on, quick-enable (no field is required, so `{}` stores the defaults)
 *     → saved at once;
 *   on, otherwise → the settings dialog (BossModAgentConfigDialog) opens as a
 *     layer, and its Save is what turns it on;
 *   off → a confirm layer, then the settings are removed.
 * An extension that needs an image-capable model cannot be turned on for an
 * agent whose model can't see images: that off row's switch is disabled, with
 * a note saying why. A row already on stays switchable, so it can be turned
 * off.
 *
 * States: loading, failed (with Try again), no agents, the table. A row's
 * failed request is shown under that row and its switch goes back: nothing
 * the server refused is shown as done. The table is re-read after every
 * change and whenever the settings dialog closes; there is no polling. Focus
 * returns to the row's switch (or, from Settings, its Settings button).
 */
const BossModExtensionAgents = (() => {
    const { h, clear } = BossModDom;
    const API = BossModExtensionsApi;

    const COPY = Object.freeze({
        loading: 'Loading agents…',
        retry: 'Try again',
        empty: 'No agents yet.',
        caption: (extension) => `Agents and whether ${extension} is on for each`,
        agent: 'Agent',
        status: 'Status',
        on: 'On',
        settings: 'Settings',
        stateOn: 'On',
        stateOnWith: (summary) => `On · ${summary}`,
        stateOff: 'Off',
        switchLabel: (extension, agent) => `${extension} for ${agent}`,
        noImages: 'Its model can’t see images',
        offTitle: (extension, agent) => `Turn off ${extension} for ${agent}?`,
        offBody: 'Their settings for it are removed.',
        turnOff: 'Turn off',
        cancel: 'Cancel',
    });

    const message = (err) => String((err && err.message) || err);

    /**
     * Build the Agents section for one extension and start its first read.
     *
     * @param {object} options
     * @param {string} options.extensionId  An enabled extension with `agent_config`.
     * @param {string} options.extensionName  Named in the table's caption, each
     *   switch's label and the turn-off question.
     * @returns {{element: HTMLElement, refresh: () => Promise<void>,
     *   dispose: () => void}} `element` is the `.ext-agents` host the section
     *   mounts; `refresh` re-reads the table in place (never rejects: every
     *   outcome is a state); `dispose` stops it for good — a read or a
     *   settings dialog still in flight then lands nowhere.
     * @throws {Error} When an option is missing.
     */
    function create(options) {
        const { extensionId, extensionName } = options || {};
        if (!extensionId) throw new Error('[extension-agents] extensionId is required');
        if (!extensionName) throw new Error('[extension-agents] extensionName is required');

        const host = h('div', { class: 'ext-agents' });
        const state = {
            // loading | failed | empty | ready
            status: 'loading',
            error: '',
            // The last `GET …/agents` answer.
            data: null,
            // Agent id → the on/off its switch is moving to, while a request runs.
            pending: new Map(),
            // Agent id → the last refusal, shown under that agent's row.
            errors: {},
            // `{agentId, control: 'switch'|'settings'}` to focus after the next render.
            focus: null,
        };
        /** Agent id → its row's controls, from the last render. */
        let controls = new Map();
        const load = BossModGates.createLoadGeneration();
        let disposed = false;

        /**
         * Re-read the table. A response overtaken by a later read is dropped.
         *
         * @param {boolean} showLoading  Replace the table with the loading line.
         * @returns {Promise<void>} Never rejects; every outcome is a state.
         */
        async function refresh(showLoading) {
            const loadId = load.next();
            if (showLoading) {
                state.status = 'loading';
                render();
            }
            try {
                const data = await API.extensionAgents(extensionId);
                if (disposed || !load.isCurrent(loadId)) return;
                state.data = data;
                state.status = data.agents.length ? 'ready' : 'empty';
                state.error = '';
            } catch (err) {
                if (disposed || !load.isCurrent(loadId)) return;
                state.status = 'failed';
                state.error = message(err);
            }
            render();
        }

        /**
         * Run one row's request with its switch held at `next` and disabled.
         * Success re-reads the table; a refusal is shown under the row and
         * the switch goes back to what the server still has.
         */
        async function change(agent, next, request) {
            state.pending.set(agent.id, next);
            state.errors[agent.id] = '';
            state.focus = { agentId: agent.id, control: 'switch' };
            render();
            let ok = true;
            try {
                await request();
            } catch (err) {
                ok = false;
                state.errors[agent.id] = message(err);
            }
            state.pending.delete(agent.id);
            if (disposed) return;
            state.focus = { agentId: agent.id, control: 'switch' };
            if (ok) await refresh(false);
            else render();
        }

        function openSettings(agent, control) {
            BossModAgentConfigDialog.open({
                extensionId,
                agentId: agent.id,
                agentName: agent.name,
                onSaved: () => { void refresh(false); },
                onClosed: () => {
                    if (disposed) return;
                    state.focus = { agentId: agent.id, control };
                    void refresh(false);
                },
            });
        }

        function confirmOff(agent) {
            BossModOverlays.createModal({
                title: COPY.offTitle(extensionName, agent.name),
                body: COPY.offBody,
                // Cancel last: a confirm opens on its last action, the safe one.
                actions: [
                    {
                        label: COPY.turnOff, tone: 'danger', id: 'ext-agents-off-confirm',
                        onSelect: () => { void change(agent, false, () => API.deleteAgentConfig(extensionId, agent.id)); },
                    },
                    { label: COPY.cancel, tone: 'quiet' },
                ],
            });
        }

        function onSwitch(agent, pressed, toggle) {
            if (!pressed) {
                // Still on until the operator confirms.
                toggle.set(true);
                confirmOff(agent);
            } else if (state.data.quick_enable) {
                void change(agent, true, () => API.saveAgentConfig(extensionId, agent.id, {}));
            } else {
                // Nothing is stored until the dialog's Save, so it stays off.
                toggle.set(false);
                openSettings(agent, 'switch');
            }
        }

        function statusCell(agent, noteId, blocked) {
            let text = COPY.stateOff;
            if (agent.configured) text = agent.summary ? COPY.stateOnWith(agent.summary) : COPY.stateOn;
            return h('td', {}, text, blocked ? [
                h('span', { 'aria-hidden': 'true' }, ' · '),
                h('span', { class: 'ext-agents-note', id: noteId }, COPY.noImages),
            ] : null);
        }

        /** One agent's row, and the error row under it when its last request was refused. */
        function agentRows(agent) {
            const nameId = `ext-agents-name-${agent.id}`;
            const noteId = `ext-agents-note-${agent.id}`;
            const pending = state.pending.has(agent.id);
            const blocked = state.data.requires_image_model && !agent.image_capable && !agent.configured;
            const toggle = BossModSwitch.create({
                label: COPY.switchLabel(extensionName, agent.name),
                pressed: pending ? state.pending.get(agent.id) : agent.configured,
                onChange: (pressed) => onSwitch(agent, pressed, toggle),
            });
            // The column head says "On" and the row names the agent, so the
            // label is the switch's accessible name only, not repeated on screen.
            toggle.element.querySelector('.switch-label').classList.add('visually-hidden');
            if (pending || blocked) toggle.element.setAttribute('disabled', '');
            if (blocked) toggle.element.setAttribute('aria-describedby', noteId);
            // The row header doubles as the focus target while the row's
            // controls are disabled, so the keyboard stays on this agent.
            const header = h('th', { scope: 'row', tabindex: '-1' },
                h('span', { class: 'ext-agents-who' },
                    BossModAvatar.create({ name: agent.name, color: agent.color, size: 'sm' }),
                    h('span', { id: nameId }, agent.name)));
            const settings = agent.configured
                ? h('button', {
                    class: 'btn btn-sm', type: 'button', 'aria-describedby': nameId, disabled: pending,
                    onclick: () => openSettings(agent, 'settings'),
                }, COPY.settings)
                : null;
            controls.set(agent.id, { toggle: toggle.element, settings, header });
            const row = h('tr', {},
                header,
                statusCell(agent, noteId, blocked),
                h('td', { class: 'ext-agents-control' }, toggle.element),
                h('td', { class: 'ext-agents-control' }, settings));
            const error = state.errors[agent.id];
            if (!error) return [row];
            return [row, h('tr', { class: 'ext-agents-error-row' },
                h('td', { colspan: '4' }, h('p', { class: 'market-failed', role: 'alert' }, error)))];
        }

        function table() {
            return h('table', { class: 'ext-agents-table' },
                h('caption', { class: 'visually-hidden' }, COPY.caption(extensionName)),
                h('thead', {}, h('tr', {},
                    h('th', { scope: 'col' }, COPY.agent),
                    h('th', { scope: 'col' }, COPY.status),
                    h('th', { scope: 'col', class: 'ext-agents-control' }, COPY.on),
                    h('th', { scope: 'col', class: 'ext-agents-control' },
                        h('span', { class: 'visually-hidden' }, COPY.settings)))),
                h('tbody', {}, state.data.agents.flatMap(agentRows)));
        }

        function content() {
            if (state.status === 'loading') {
                return h('p', { class: 'market-status', role: 'status' }, COPY.loading);
            }
            if (state.status === 'failed') {
                return [
                    h('p', { class: 'market-failed', role: 'alert' }, state.error),
                    h('button', {
                        class: 'btn btn-sm', id: 'ext-agents-retry', type: 'button',
                        onclick: () => { void refresh(true); },
                    }, COPY.retry),
                ];
            }
            const help = state.data.help.split(/\n\s*\n/).map((para) => para.trim()).filter(Boolean);
            return [
                h('div', { class: 'ext-config-help' }, help.map((para) => h('p', { class: 'field-hint' }, para))),
                state.status === 'empty'
                    ? h('p', { class: 'market-status', role: 'status' }, COPY.empty)
                    : table(),
            ];
        }

        function render() {
            const wanted = state.focus;
            state.focus = null;
            controls = new Map();
            clear(host);
            [].concat(content()).forEach((node) => host.append(node));
            const row = wanted ? controls.get(wanted.agentId) : null;
            if (!row) return;
            let target = wanted.control === 'settings' && row.settings ? row.settings : row.toggle;
            // A disabled control cannot take focus; its row header can.
            if (target.hasAttribute('disabled')) target = row.header;
            target.focus();
        }

        void refresh(true);
        return {
            element: host,
            refresh: () => refresh(false),
            dispose: () => {
                disposed = true;
                load.next();
            },
        };
    }

    return { create };
})();
