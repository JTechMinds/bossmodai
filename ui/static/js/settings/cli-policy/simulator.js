/**
 * BossMod AI — CLI Policy simulator tab.
 *
 * A full interactive terminal, not a policy checker: it echoes commands at a
 * prompt, keeps a command history the arrow keys walk, shows a loading line
 * while a command is in flight, and runs as whichever agent is selected.
 * Enter is dry-run (parse + policy). Execute for real sends execute=true.
 *
 * The simulator is three modules, one per job: this one owns the terminal —
 * the DOM, the state and the bindings; `simulator-run.js` owns running a
 * command; `simulator-output.js` owns painting the result. Each is handed
 * what it needs as an argument, so none of them shares a closure with another.
 */
const CliPolicySimulator = (() => {
    const esc = BossModFormat.escapeHtml;
    const { icons } = BossModCliPolicyShared;
    const output = BossModSimulatorOutput;

    let rulesCache = [];
    let agentsCache = [];

    // ── Simulator state ──
    let simCommandHistory = [];
    let simHistoryIdx = -1;
    let simShellEnabled = false;
    let simDefaultPolicy = 'deny';

    /**
     * @returns {Element|null} The output pane. Resolved at every write, not
     *   held: a command can still be in flight when the terminal re-renders.
     */
    function getOutputEl() {
        return document.getElementById('cli-sim-output');
    }

    /** @returns {string} The selected agent's display name, or 'agent'. */
    function selectedAgentName() {
        const select = document.getElementById('cli-sim-agent');
        return select?.selectedOptions[0]?.text || 'agent';
    }

    /**
     * Read the two policy facts the terminal banner reports.
     *
     * @returns {Promise<boolean>} false when the read failed — a banner
     *   reading "SHELL OFF / deny" because we could not ask is a claim, not a
     *   default, so the caller says so instead of printing it.
     */
    async function _fetchSimStatus() {
        try {
            const res = await apiFetch('/api/settings?category=cli_policy');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const settings = await res.json();
            for (const s of settings) {
                if (s.key === 'cli_shell_enabled') simShellEnabled = s.value === 'true';
                if (s.key === 'cli_default_policy') simDefaultPolicy = s.value || 'deny';
            }
            return true;
        } catch (err) {
            console.error('[cli-policy] could not read the simulator policy status', err);
            return false;
        }
    }

    /**
     * One centred notice in place of the terminal.
     *
     * @param {Element} el
     * @param {string} icon    Lucide icon name.
     * @param {string} title
     * @param {string} detail
     * @returns {void}
     */
    function _renderNotice(el, icon, title, detail) {
        el.innerHTML = `
            <div class="text-center py-16 text-bm-muted">
                <i data-lucide="${esc(icon)}" class="w-12 h-12 mx-auto mb-4 opacity-30"></i>
                <p class="text-sm font-medium mb-1">${esc(title)}</p>
                <p class="text-xs">${esc(detail)}</p>
            </div>`;
        icons(el);
    }

    /**
     * Build the terminal and bind it.
     *
     * @param {Element} el  The tab content element.
     * @returns {Promise<void>} Resolves once the terminal is on screen. With no
     *   agents it renders the empty state and binds nothing — the simulator
     *   runs commands *as* an agent, so there is nothing to run.
     */
    async function render(el) {
        let failed = !(await _fetchSimStatus());

        if (agentsCache.length === 0) {
            try {
                const res = await apiFetch('/api/agents');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                agentsCache = await res.json();
            } catch (err) {
                console.error('[cli-policy] could not read the agent roster', err);
                agentsCache = [];
                failed = true;
            }
        }

        // Non-critical: without rules the terminal runs, it just cannot name
        // the matched rule.
        if (rulesCache.length === 0) {
            try {
                const res = await apiFetch('/api/cli-policy/rules');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                rulesCache = await res.json();
            } catch (err) {
                console.warn('[cli-policy] matched-rule hints are unavailable', err);
            }
        }

        // A failed read is not an empty roster: "create an agent first" when
        // they have ten hides the outage that caused it.
        if (failed) {
            _renderNotice(el, 'alert-triangle', 'The simulator could not load',
                'The agent roster or the CLI policy status could not be read. '
                + 'Check the backend and reopen this tab.');
            return;
        }

        if (agentsCache.length === 0) {
            _renderNotice(el, 'bot', 'No agents found',
                'Create an agent first — the simulator runs commands as a '
                + 'specific agent to test their permissions.');
            return;
        }

        const agentOptions = agentsCache.map(a =>
            `<option value="${esc(a.id)}">${esc(a.name)}</option>`
        ).join('');

        el.innerHTML = BossModSimulatorShell.terminalMarkup({
            agentOptions,
            shellEnabled: simShellEnabled,
            defaultPolicy: simDefaultPolicy,
        });

        icons(el);
        simCommandHistory = [];
        simHistoryIdx = -1;
        _updateSimPrompt();
        output.printWelcomeBanner(getOutputEl(), {
            shellEnabled: simShellEnabled,
            defaultPolicy: simDefaultPolicy,
        });

        const input = document.getElementById('cli-sim-input');

        input.focus();

        // Focus input when clicking terminal
        document.getElementById('cli-sim-terminal').addEventListener('click', (e) => {
            if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT') input.focus();
        });

        // Agent change updates prompt
        document.getElementById('cli-sim-agent').addEventListener('change', () => {
            _updateSimPrompt();
            output.line(getOutputEl(), 'dim', `Switched to ${selectedAgentName()}.`);
            output.blank(getOutputEl());
        });

        document.getElementById('btn-sim-execute-real').addEventListener('click', async () => {
            const cmd = (input.value.trim() || simCommandHistory[simCommandHistory.length - 1] || '').trim();
            if (!cmd) {
                output.line(getOutputEl(), 'amber', 'Type a command first, then click Execute for real.');
                output.blank(getOutputEl());
                return;
            }
            input.value = '';
            if (simCommandHistory[simCommandHistory.length - 1] !== cmd) {
                simCommandHistory.push(cmd);
            }
            simHistoryIdx = simCommandHistory.length;
            await _executeSimCommand(cmd, { execute: true });
        });

        // Clear
        document.getElementById('btn-sim-clear').addEventListener('click', () => {
            const out = getOutputEl();
            if (out) out.innerHTML = '';
            output.line(getOutputEl(), 'dim', 'Terminal cleared. Type <span class="text-cyan-400">help</span> for commands.');
            output.blank(getOutputEl());
        });

        // Handle input
        input.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const cmd = input.value.trim();
                input.value = '';
                if (!cmd) return;

                simCommandHistory.push(cmd);
                simHistoryIdx = simCommandHistory.length;

                await _executeSimCommand(cmd, { execute: false });
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (simHistoryIdx > 0) {
                    simHistoryIdx--;
                    input.value = simCommandHistory[simHistoryIdx] || '';
                }
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (simHistoryIdx < simCommandHistory.length - 1) {
                    simHistoryIdx++;
                    input.value = simCommandHistory[simHistoryIdx] || '';
                } else {
                    simHistoryIdx = simCommandHistory.length;
                    input.value = '';
                }
            }
        });
    }

    function _updateSimPrompt() {
        const select = document.getElementById('cli-sim-agent');
        const prompt = document.getElementById('cli-sim-prompt');
        const title = document.getElementById('cli-sim-title');
        if (!select || !prompt) return;
        const name = selectedAgentName();
        prompt.textContent = `${name} $`;
        if (title) title.textContent = `BossMod CLI — ${name}`;
    }

    /**
     * Run one command as the selected agent.
     *
     * @param {string} cmd
     * @param {object} [options]
     * @param {boolean} [options.execute]  true runs for real; false dry-runs.
     * @returns {Promise<void>}
     */
    async function _executeSimCommand(cmd, { execute = false } = {}) {
        await BossModSimulatorRun.runCommand(cmd, {
            execute,
            getOutputEl,
            agentId: document.getElementById('cli-sim-agent')?.value,
            agentName: selectedAgentName(),
            rules: rulesCache,
        });
    }

    return { render };
})();
