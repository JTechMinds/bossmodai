/**
 * BossMod AI — CLI simulator output rendering.
 *
 * Everything that writes into the terminal's output pane, and nothing that
 * owns it. Extracted from the single ~380-line `render` in
 * cli-policy-simulator.js, whose lower half turned a command result into
 * output while its upper half built the shell and bound its events.
 *
 * Every function takes the output element and everything else it needs as an
 * argument — there is no shared closure state with `simulator.js`, so the
 * shell owns the DOM and the state and this owns only the painting. That is
 * what makes the seam real rather than a line cut: nothing here reads the
 * agent selector, the settings, or the rule cache on its own.
 */
const BossModSimulatorOutput = (() => {
    const esc = BossModFormat.escapeHtml;

    /**
     * Append one line to the output pane and keep it scrolled to the bottom.
     *
     * @param {Element|null} outputEl  The terminal's output pane, or null when
     *   the tab has been navigated away from mid-request. The shell resolves
     *   it; this module never looks it up, which is why it can be handed a
     *   different pane without changing.
     * @param {string} cls  Class list for the line.
     * @param {string} html  Line content. Callers escape their own
     *   interpolations; the fixed markup here is the terminal's own styling.
     * @returns {void}
     */
    function appendOutput(outputEl, cls, html) {
        if (!outputEl) return;
        const div = document.createElement('div');
        div.className = cls;
        div.innerHTML = html;
        outputEl.appendChild(div);
        outputEl.scrollTop = outputEl.scrollHeight;
    }

    /**
     * Append one line in one of the terminal's named tones.
     *
     * @param {Element|null} outputEl
     * @param {string} type  dim | text | bright | cyan | green | red | amber | header
     * @param {string} html
     * @returns {void}
     */
    function line(outputEl, type, html) {
        const cls = {
            dim:     'text-gray-400',
            text:    'text-gray-200',
            bright:  'text-gray-100',
            cyan:    'text-cyan-400',
            green:   'text-emerald-400',
            red:     'text-red-400',
            amber:   'text-amber-400',
            header:  'text-gray-400 text-xs uppercase tracking-wider font-semibold mt-2',
        }[type] || 'text-gray-200';
        appendOutput(outputEl, cls, html);
    }

    /**
     * Append a blank spacer line.
     *
     * @param {Element|null} outputEl
     * @returns {void}
     */
    function blank(outputEl) {
        appendOutput(outputEl, 'text-gray-500', '&nbsp;');
    }

    /**
     * Print the banner the terminal opens with.
     *
     * @param {Element|null} outputEl
     * @param {object} status  The two policy facts the banner states, read by
     *   the shell rather than by this module.
     * @param {boolean} status.shellEnabled
     * @param {string} status.defaultPolicy
     * @returns {void}
     */
    function printWelcomeBanner(outputEl, { shellEnabled, defaultPolicy }) {
        const shellStatus = shellEnabled
            ? '<span class="text-emerald-400">enabled</span> — commands go through policy check, then execute on the host'
            : '<span class="text-red-400">disabled</span> — only built-in virtual commands are available';

        line(outputEl, 'cyan',   '┌───────────────────────────────────────────────┐');
        line(outputEl, 'cyan',   '│             BossMod CLI Simulator             │');
        line(outputEl, 'cyan',   '└───────────────────────────────────────────────┘');
        blank(outputEl);
        line(outputEl, 'dim',    `Shell executor: ${shellStatus}`);
        line(outputEl, 'dim',    `Default policy: <span class="text-gray-300">${esc(defaultPolicy)}</span> (when no rule matches)`);
        blank(outputEl);
        line(outputEl, 'dim',    'Enter is dry-run: parse + policy only. No files or shell.');
        line(outputEl, 'dim',    'Use <span class="text-amber-400">Execute for real</span> to run writes/shell through the full pipeline.');
        line(outputEl, 'dim',    '  policy check → (dry-run stops here) → execute → result');
        blank(outputEl);
        line(outputEl, 'dim',    'Try these:');
        line(outputEl, 'text',   '  <span class="text-cyan-400">help</span>             — discover available commands');
        line(outputEl, 'text',   '  <span class="text-cyan-400">categories</span>       — browse commands by category');
        line(outputEl, 'text',   '  <span class="text-cyan-400">fsearch network</span>  — search for commands by keyword');
        line(outputEl, 'text',   '  <span class="text-cyan-400">learn cat</span>        — detailed usage for a command');
        line(outputEl, 'text',   '  <span class="text-cyan-400">pwd</span>             — built-in command, always works');
        line(outputEl, 'text',   '  <span class="text-cyan-400">echo hello</span>      — requires shell enabled');
        blank(outputEl);
    }

    /**
     * Echo a command back at the prompt, the way a real terminal does.
     *
     * @param {Element|null} outputEl
     * @param {string} agentName  The selected agent's display name; the shell
     *   owns the selector, so it is passed in rather than read here.
     * @param {string} cmd
     * @returns {void}
     */
    function appendCommandEcho(outputEl, agentName, cmd) {
        appendOutput(outputEl, 'text-gray-200', `<span class="text-emerald-400 font-semibold">${esc(agentName)} $</span> ${esc(cmd)}`);
    }

    /**
     * Turn one simulator result into terminal output.
     *
     * @param {Element|null} outputEl
     * @param {object} data  The simulator's execute response; the module
     *   that requests it is `simulator-run.js`.
     * @param {object} context
     * @param {object[]} context.rules  The rule list, for the matched-rule
     *   hint. An empty list drops the hint rather than failing the render.
     * @returns {void}
     */
    function renderResult(outputEl, data, { rules }) {
        // ── Status banner ──
        if (data.dry_run && data.ok) {
            appendOutput(outputEl,
                'bg-sky-500/10 text-sky-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-sky-500/20',
                `&#9711; DRY RUN — ${esc(data.kind)} (${esc(data.executor)}) — no files or shell`
            );
        } else if (data.ok) {
            appendOutput(outputEl,
                'bg-emerald-500/10 text-emerald-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-emerald-500/20',
                `&#10003; OK — ${esc(data.kind)} (${esc(data.executor)})`
            );
        } else if (data.approval_required) {
            appendOutput(outputEl,
                'bg-amber-500/10 text-amber-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-amber-500/20',
                `&#9888; APPROVAL REQUIRED — this command is gated behind human approval`
            );
        } else {
            appendOutput(outputEl,
                'bg-red-500/10 text-red-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-red-500/20',
                `&#10007; BLOCKED — exit ${data.exit_code} (${esc(data.kind)})`
            );
        }

        // ── Approval detail ──
        if (data.approval_required) {
            line(outputEl, 'amber', `In a real agent turn, the turn would pause here until you approve or reject.`);
            if (data.approval_request_id) {
                line(outputEl, 'dim', `Approval request: ${esc(data.approval_request_id)}`);
            }
            line(outputEl, 'dim', `Go to the Approvals tab to manage pending requests.`);
            return;
        }

        // ── Detail message (for errors/blocks) ──
        if (!data.ok && data.detail) {
            line(outputEl, 'red', esc(data.detail));
        }

        // ── Output content ──
        const output = data.output || '';
        if (output.trim()) {
            const lines = output.split('\n');
            for (const row of lines) {
                // Skip BossMod wrapper header lines
                if (row.startsWith('BOSSMOD CLI RESULT') || row.startsWith('command:')) continue;
                if (!row.trim()) continue;

                if (row.match(/^[A-Z][A-Z ]+:$/)) {
                    // Section header (STDOUT:, STDERR:, ERROR:, etc.)
                    const label = row.replace(/:$/, '');
                    const labelColor = label === 'ERROR' || label === 'STDERR'
                        ? 'text-red-400' : 'text-gray-400';
                    appendOutput(outputEl, `${labelColor} text-xs uppercase tracking-wider font-semibold mt-2`, label);
                } else {
                    line(outputEl, 'text', esc(row));
                }
            }
        }

        // ── Matched rule hint ──
        if (data.matched_rule_id) {
            const rule = rules.find(r => r.id === data.matched_rule_id);
            if (rule) {
                line(outputEl, 'dim', `matched rule: "${esc(rule.pattern)}" (${esc(rule.match_mode)}) — ${esc(rule.description || rule.tier)}`);
            }
        }

        // ── CWD ──
        if (data.cwd) {
            line(outputEl, 'dim', `cwd: ${esc(data.cwd)}`);
        }
    }

    return { appendOutput, line, blank, printWelcomeBanner, appendCommandEcho, renderResult };
})();
