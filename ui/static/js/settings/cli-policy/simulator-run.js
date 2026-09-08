/**
 * BossMod AI — CLI simulator command execution.
 *
 * One command, end to end: unwrap the `bm_cli("...")` form an agent would
 * write, echo it at the prompt, show the loading line, POST it, and hand the
 * result to `simulator-output.js`.
 *
 * This is the middle of the simulator's three jobs — the shell
 * (`simulator.js`) owns the DOM and the state, this owns the request, and the
 * output module owns the painting. It holds no state of its own and reads
 * nothing from the page: the caller resolves the agent and the rule list and
 * passes them in.
 */
const BossModSimulatorRun = (() => {
    const esc = BossModFormat.escapeHtml;
    const output = BossModSimulatorOutput;

    /**
     * Run one command through the simulator endpoint.
     *
     * @param {string} cmd  The raw input, possibly wrapped as `bm_cli("...")`.
     * @param {object} options
     * @param {boolean} [options.execute]  false is a dry run — parse and policy
     *   only, no files and no shell. true runs the full pipeline.
     * @param {() => Element|null} options.getOutputEl  Resolves the output pane
     *   at each write rather than once up front, because a command can still be
     *   in flight when the terminal is re-rendered.
     * @param {string|undefined} options.agentId  The agent to run as; a missing
     *   one is reported in the terminal rather than sent.
     * @param {string} options.agentName  Display name, for the prompt echo.
     * @param {object[]} options.rules  Rule list, for the matched-rule hint.
     * @returns {Promise<void>} Never rejects; every failure is printed as a
     *   terminal line, which is what a terminal does with one.
     */
    async function runCommand(cmd, { execute = false, getOutputEl, agentId, agentName, rules }) {
        // Unwrap bm_cli("...") / bm_cli('...') wrapper if the user types it
        const wrapMatch = cmd.match(/^bm_cli\s*\(\s*["'](.+?)["']\s*\)$/);
        if (wrapMatch) cmd = wrapMatch[1];

        output.appendCommandEcho(getOutputEl(), agentName, cmd);

        if (!agentId) {
            output.line(getOutputEl(), 'red', 'No agent selected. Choose an agent from the dropdown above.');
            output.blank(getOutputEl());
            return;
        }

        const loadingEl = document.createElement('div');
        loadingEl.className = 'text-gray-600 animate-pulse';
        loadingEl.textContent = execute ? 'executing...' : 'dry-run...';
        const out = getOutputEl();
        if (out) { out.appendChild(loadingEl); out.scrollTop = out.scrollHeight; }

        try {
            const body = { command: cmd, agent_id: agentId };
            if (execute) {
                body.execute = true;
                body.dry_run = false;
            } else {
                body.dry_run = true;
            }
            const res = await apiFetch('/api/cli-policy/simulator/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json();

            // Remove the loading indicator
            if (loadingEl.parentNode) loadingEl.remove();

            if (!res.ok) {
                output.line(getOutputEl(), 'red', `Error: ${esc(data.detail || 'Request failed')}`);
                output.blank(getOutputEl());
                return;
            }

            // Render result based on kind
            output.renderResult(getOutputEl(), data, { rules });
        } catch (err) {
            if (loadingEl.parentNode) loadingEl.remove();
            output.line(getOutputEl(), 'red', `Network error: ${esc(err.message)}`);
        }
        output.blank(getOutputEl());
    }

    return { runCommand };
})();
