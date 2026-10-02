/**
 * BossMod AI — Settings → AI Connections, the System AI row.
 *
 * Owns the whole System AI setting: the connection it runs on and the
 * thinking level it sends, drawn as one row of two toolbar dropdowns
 * (core/menu-select.js, the default 'button' look, as the Tasks and Log
 * toolbars use) under one hint line. settings-connections.js renders the list
 * of connections and places this row in its header; it no longer knows how
 * the setting is saved.
 *
 * One ordering rule spans the two controls: a connection that does not offer
 * the stored level gets `default` saved first, so the server never holds a
 * level its System AI connection cannot apply (api/routes/settings.py refuses
 * that pairing with a 409).
 *
 * The level labels are BossModAgentFields.THINKING_CHOICES, the list the agent
 * form's connection section uses, so a level reads the same in both places.
 */
const BossModSystemAi = (() => {
    const CONNECTION_LABEL = 'System AI connection';
    const THINKING_LABEL = 'System AI thinking';
    const HINT = 'Used for compaction, channel routing and auto-approve reviews. Off or Low thinking is usually enough.';
    const LINE_CLASS = 'text-xs text-bm-muted';

    /**
     * The connection System AI uses: the saved id when it is listed,
     * otherwise the first connection. Mirrors resolve_system_connection in
     * core/llm/system_completion.py.
     *
     * @param {string} savedId
     * @param {object[]} connections
     * @returns {object|null}
     */
    function effectiveConnection(savedId, connections) {
        return connections.find(conn => conn.id === savedId) || connections[0] || null;
    }

    /**
     * @param {object|null} connection
     * @param {string} choice
     * @returns {boolean} Whether `choice` can be sent on `connection`.
     */
    function offers(connection, choice) {
        if (choice === 'default') return true;
        const levels = (connection && connection.thinking_levels) || {};
        return Object.hasOwn(levels, choice);
    }

    /**
     * @param {object[]} connections
     * @returns {Array<{value: string, label: string}>} One option per
     *   connection, labelled `name (model)`.
     */
    function connectionOptions(connections) {
        return connections.map(conn => ({
            value: conn.id,
            label: conn.model ? `${conn.name} (${conn.model})` : conn.name,
        }));
    }

    /**
     * Server default plus each level the connection offers, in vocabulary
     * order, each reading "Thinking: <level>" — the 'button' trigger shows
     * the option label, and that prefix is what lets the control explain
     * itself without a label line of its own. A stored choice the connection
     * does not offer (its saved connection was deleted and the fallback lacks
     * the level) is listed too, marked, so the control shows what is stored
     * rather than a choice the server does not hold.
     *
     * @param {object|null} connection
     * @param {string} stored
     * @returns {Array<{value: string, label: string}>}
     */
    function thinkingOptions(connection, stored) {
        const options = BossModAgentFields.THINKING_CHOICES
            .filter(choice => offers(connection, choice.value))
            .map(({ value, label }) => ({ value, label: `Thinking: ${label}` }));
        if (!options.some(option => option.value === stored)) {
            const known = BossModAgentFields.THINKING_CHOICES.find(choice => choice.value === stored);
            options.push({ value: stored, label: `Thinking: ${known ? known.label : stored} (not offered)` });
        }
        return options;
    }

    /**
     * The one line that replaces the row when it cannot be drawn, or null
     * when the row is drawn. markup() and bind() both read it, so they cannot
     * disagree about whether the controls exist.
     *
     * @param {{connections: object[], connectionsFailed: boolean, settingsFailed: boolean}} state
     * @returns {string|null}
     */
    function blockedMessage(state) {
        if (state.settingsFailed) return 'System AI could not be loaded.';
        if (state.connectionsFailed) return 'AI connections could not be loaded.';
        if (!state.connections.length) return 'No AI connections yet.';
        return null;
    }

    /**
     * The System AI block: the row (label, connection mount, thinking mount)
     * and its hint, or one line saying why there is no row.
     *
     * @param {{connections: object[], connectionsFailed: boolean,
     *   settingsFailed: boolean, thinking: (string|null)}} state
     *   `thinking` is the stored `system_ai_thinking`, or null when the row
     *   is missing — a broken install, said as such rather than drawn as
     *   Server default.
     * @returns {string}
     */
    function markup(state) {
        const blocked = blockedMessage(state);
        if (blocked !== null) {
            return `<div class="mt-3" data-system-ai><p class="${LINE_CLASS}">${BossModFormat.escapeHtml(blocked)}</p></div>`;
        }
        const thinking = state.thinking === null
            ? `<span class="${LINE_CLASS}">${BossModFormat.escapeHtml('System AI thinking could not be loaded.')}</span>`
            : '<span data-system-ai-thinking-mount></span>';
        return `
                <div class="mt-3" data-system-ai>
                    <div class="flex flex-wrap items-center gap-x-3 gap-y-2" data-system-ai-row>
                        <span class="text-sm font-medium">System AI</span>
                        <div class="flex flex-wrap items-center gap-2">
                            <span data-system-ai-connection-mount></span>
                            ${thinking}
                        </div>
                    </div>
                    <p class="${LINE_CLASS} mt-1.5">${BossModFormat.escapeHtml(HINT)}</p>
                </div>`;
    }

    /**
     * @param {string} key  `system_ai_connection` or `system_ai_thinking`.
     * @param {string} value
     * @returns {Promise<Response>}
     * @throws {Error} With the server's detail when the save is refused.
     */
    function save(key, value) {
        return apiFetchOk(`/api/settings/${encodeURIComponent(key)}?value=${encodeURIComponent(value)}&category=llm`, {
            method: 'PUT',
        });
    }

    /**
     * @param {HTMLElement} root
     * @param {string} selector
     * @returns {HTMLElement}
     * @throws {Error} When markup() drew the row without that mount.
     */
    function mountPoint(root, selector) {
        const point = root.querySelector(selector);
        if (!point) throw new Error(`[system-ai] the System AI row has no ${selector}`);
        return point;
    }

    /**
     * Mount the two dropdowns into the row markup() drew, and save each
     * change. Painting writes nothing: an unset or stale saved id shows the
     * first connection without storing it. Does nothing when markup() drew
     * a message instead of the row.
     *
     * @param {HTMLElement} root  The Connections section; refusals are shown
     *   in it with showRowError.
     * @param {{savedId: string, thinking: (string|null), connections: object[],
     *   connectionsFailed: boolean, settingsFailed: boolean}} state  The same
     *   state markup() was given.
     * @returns {void}
     * @throws {Error} When the row was drawn without its mount points.
     */
    function bind(root, state) {
        if (blockedMessage(state) !== null) return;
        const options = connectionOptions(state.connections);
        let connection = effectiveConnection(state.savedId, state.connections);
        let stored = state.thinking;
        let thinking = null;

        const reoption = () => thinking.setOptions(thinkingOptions(connection, stored), stored);

        async function chooseConnection(id) {
            const next = effectiveConnection(id, state.connections);
            if (thinking && !offers(next, stored)) {
                try {
                    await save('system_ai_thinking', 'default');
                } catch (err) {
                    // Nothing was saved, so the control goes back to what is.
                    connectionSelect.setOptions(options, connection.id);
                    showRowError(root, `System AI thinking could not be reset: ${err.message}`);
                    return;
                }
                stored = 'default';
                reoption();
            }
            try {
                await save('system_ai_connection', id);
            } catch (err) {
                connectionSelect.setOptions(options, connection.id);
                showRowError(root, `System AI could not be saved: ${err.message}`);
                return;
            }
            connection = next;
            if (thinking) reoption();
            BossModOperatorInvalidate.notifyLocal(['connections']);
        }

        async function chooseThinking(value) {
            try {
                await save('system_ai_thinking', value);
            } catch (err) {
                reoption();
                showRowError(root, `System AI thinking could not be saved: ${err.message}`);
                return;
            }
            stored = value;
            reoption();
            BossModOperatorInvalidate.notifyLocal(['connections']);
        }

        const connectionSelect = BossModMenuSelect.create({
            label: CONNECTION_LABEL,
            options,
            value: connection.id,
            onChange: id => chooseConnection(id),
        });
        mountPoint(root, '[data-system-ai-connection-mount]').append(connectionSelect.element);

        if (stored !== null) {
            thinking = BossModMenuSelect.create({
                label: THINKING_LABEL,
                options: thinkingOptions(connection, stored),
                value: stored,
                onChange: value => chooseThinking(value),
            });
            mountPoint(root, '[data-system-ai-thinking-mount]').append(thinking.element);
        }
        BossModIcons.paint(root.querySelector('[data-system-ai]'), 'settings-connections.system-ai');
    }

    return { markup, bind };
})();
