/**
 * BossMod AI — CLI Policy simulator tab (HA-STRUCT-P1-04).
 *
 * Enter is dry-run (parse + policy). Execute for real sends execute=true.
 */
const CliPolicySimulator = (() => {
    const esc = BossModUtils.escapeHtml;

    function icons(root) {
        if (window.lucide) lucide.createIcons({ nodes: [root] });
    }

    let rulesCache = [];
    let agentsCache = [];

    // ── Simulator state ──
    let simHistory = [];
    let simRunning = false;
    let simCommandHistory = [];
    let simHistoryIdx = -1;
    let simShellEnabled = false;
    let simDefaultPolicy = 'deny';

    async function _fetchSimStatus() {
        try {
            const res = await apiFetch('/api/settings?category=cli_policy');
            const settings = await res.json();
            for (const s of settings) {
                if (s.key === 'cli_shell_enabled') simShellEnabled = s.value === 'true';
                if (s.key === 'cli_default_policy') simDefaultPolicy = s.value || 'deny';
            }
        } catch { /* use defaults */ }
    }

    async function render(el) {
        await _fetchSimStatus();

        if (agentsCache.length === 0) {
            try {
                const res = await apiFetch('/api/agents');
                agentsCache = await res.json();
            } catch {
                agentsCache = [];
            }
        }

        // Pre-fetch rules for matched rule hints
        if (rulesCache.length === 0) {
            try {
                const res = await apiFetch('/api/cli-policy/rules');
                rulesCache = await res.json();
            } catch { /* non-critical */ }
        }

        if (agentsCache.length === 0) {
            el.innerHTML = `
                <div class="text-center py-16 text-bm-muted">
                    <i data-lucide="bot" class="w-12 h-12 mx-auto mb-4 opacity-30"></i>
                    <p class="text-sm font-medium mb-1">No agents found</p>
                    <p class="text-xs">Create an agent first — the simulator runs commands as a specific agent to test their permissions.</p>
                </div>`;
            icons(el);
            return;
        }

        const agentOptions = agentsCache.map(a =>
            `<option value="${esc(a.id)}">${esc(a.name)}</option>`
        ).join('');

        const shellBadge = simShellEnabled
            ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">SHELL ON</span>'
            : '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-500/15 text-red-400 border border-red-500/20">SHELL OFF</span>';

        const policyBadge = simDefaultPolicy === 'approval_required'
            ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/15 text-amber-400 border border-amber-500/20">DEFAULT: APPROVAL</span>'
            : '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-500/15 text-slate-400 border border-slate-500/20">DEFAULT: DENY</span>';

        el.innerHTML = `
            <div class="flex flex-col h-full max-h-[calc(100vh-12rem)]">
                <!-- Controls bar -->
                <div class="flex items-center gap-3 mb-3 shrink-0 flex-wrap">
                    <div class="flex items-center gap-2">
                        <i data-lucide="user" class="w-4 h-4 text-bm-muted"></i>
                        <label class="text-xs font-semibold whitespace-nowrap">Run as</label>
                        <select id="cli-sim-agent"
                                class="px-3 py-1.5 bg-bm-bg border border-bm-border rounded-lg text-sm text-bm-text font-medium min-w-[200px]">
                            ${agentOptions}
                        </select>
                    </div>
                    <div class="flex items-center gap-2">
                        ${shellBadge}
                        ${policyBadge}
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-sky-500/15 text-sky-400 border border-sky-500/20">DRY-RUN DEFAULT</span>
                    </div>
                    <button id="btn-sim-execute-real"
                            class="text-xs font-semibold text-amber-300 hover:text-amber-200 border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 rounded-lg flex items-center gap-1">
                        <i data-lucide="play" class="w-3 h-3"></i> Execute for real
                    </button>
                    <button id="btn-sim-clear"
                            class="ml-auto text-xs text-bm-muted hover:text-bm-text flex items-center gap-1">
                        <i data-lucide="trash-2" class="w-3 h-3"></i> Clear
                    </button>
                </div>

                <!-- Terminal -->
                <div id="cli-sim-terminal"
                     class="flex-1 bg-gray-950 rounded-xl border border-gray-800 overflow-hidden flex flex-col font-mono text-[15px] leading-relaxed min-h-[400px] shadow-lg">
                    <!-- Title bar -->
                    <div class="flex items-center gap-2 px-4 py-2 bg-gray-900/80 border-b border-gray-800 shrink-0">
                        <div class="w-3 h-3 rounded-full bg-red-500/80"></div>
                        <div class="w-3 h-3 rounded-full bg-yellow-500/80"></div>
                        <div class="w-3 h-3 rounded-full bg-green-500/80"></div>
                        <span class="ml-2 text-gray-500 text-xs" id="cli-sim-title">BossMod CLI Simulator</span>
                    </div>
                    <!-- Output -->
                    <div id="cli-sim-output"
                         class="flex-1 overflow-y-auto p-4 text-gray-300 space-y-0.5 min-h-0">
                    </div>
                    <!-- Input bar -->
                    <div class="flex items-center gap-0 px-4 py-2.5 bg-gray-900/80 border-t border-gray-800 shrink-0">
                        <span class="text-emerald-400 mr-2 select-none font-semibold" id="cli-sim-prompt">$</span>
                        <input type="text" id="cli-sim-input"
                               class="flex-1 bg-transparent text-gray-200 outline-none text-[15px] font-mono"
                               placeholder="Type a command..."
                               autocomplete="off" spellcheck="false">
                        <span class="text-gray-600 text-xs ml-2 select-none hidden sm:inline" id="cli-sim-hint">
                            Enter = dry-run &middot; &uarr;&darr; history
                        </span>
                    </div>
                </div>
            </div>`;

        icons(el);
        simHistory = [];
        simCommandHistory = [];
        simHistoryIdx = -1;
        _updateSimPrompt();
        _printWelcomeBanner();

        const input = document.getElementById('cli-sim-input');

        input.focus();

        // Focus input when clicking terminal
        document.getElementById('cli-sim-terminal').addEventListener('click', (e) => {
            if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT') input.focus();
        });

        // Agent change updates prompt
        document.getElementById('cli-sim-agent').addEventListener('change', () => {
            _updateSimPrompt();
            const select = document.getElementById('cli-sim-agent');
            const name = select?.selectedOptions[0]?.text || 'agent';
            _simLine('dim', `Switched to ${name}.`);
            _simBlank();
        });

        document.getElementById('btn-sim-execute-real').addEventListener('click', async () => {
            const cmd = (input.value.trim() || simCommandHistory[simCommandHistory.length - 1] || '').trim();
            if (!cmd) {
                _simLine('amber', 'Type a command first, then click Execute for real.');
                _simBlank();
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
            const out = document.getElementById('cli-sim-output');
            if (out) out.innerHTML = '';
            _simLine('dim', 'Terminal cleared. Type <span class="text-cyan-400">help</span> for commands.');
            _simBlank();
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

    // ── Simulator output helpers ──

    function _simLine(type, html) {
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
        _appendOutput(cls, html);
    }

    function _simBlank() {
        _appendOutput('text-gray-500', '&nbsp;');
    }

    function _printWelcomeBanner() {
        const shellStatus = simShellEnabled
            ? '<span class="text-emerald-400">enabled</span> — commands go through policy check, then execute on the host'
            : '<span class="text-red-400">disabled</span> — only built-in virtual commands are available';

        _simLine('cyan',   '┌───────────────────────────────────────────────┐');
        _simLine('cyan',   '│             BossMod CLI Simulator             │');
        _simLine('cyan',   '└───────────────────────────────────────────────┘');
        _simBlank();
        _simLine('dim',    `Shell executor: ${shellStatus}`);
        _simLine('dim',    `Default policy: <span class="text-gray-300">${esc(simDefaultPolicy)}</span> (when no rule matches)`);
        _simBlank();
        _simLine('dim',    'Enter is dry-run: parse + policy only. No files or shell.');
        _simLine('dim',    'Use <span class="text-amber-400">Execute for real</span> to run writes/shell through the full pipeline.');
        _simLine('dim',    '  policy check → (dry-run stops here) → execute → result');
        _simBlank();
        _simLine('dim',    'Try these:');
        _simLine('text',   '  <span class="text-cyan-400">help</span>             — discover available commands');
        _simLine('text',   '  <span class="text-cyan-400">categories</span>       — browse commands by category');
        _simLine('text',   '  <span class="text-cyan-400">fsearch network</span>  — search for commands by keyword');
        _simLine('text',   '  <span class="text-cyan-400">learn cat</span>        — detailed usage for a command');
        _simLine('text',   '  <span class="text-cyan-400">pwd</span>             — built-in command, always works');
        _simLine('text',   '  <span class="text-cyan-400">echo hello</span>      — requires shell enabled');
        _simBlank();
    }

    function _updateSimPrompt() {
        const select = document.getElementById('cli-sim-agent');
        const prompt = document.getElementById('cli-sim-prompt');
        const title = document.getElementById('cli-sim-title');
        if (!select || !prompt) return;
        const name = select.selectedOptions[0]?.text || 'agent';
        prompt.textContent = `${name} $`;
        if (title) title.textContent = `BossMod CLI — ${name}`;
    }

    function _appendOutput(cls, html) {
        const out = document.getElementById('cli-sim-output');
        if (!out) return;
        const div = document.createElement('div');
        div.className = cls;
        div.innerHTML = html;
        out.appendChild(div);
        out.scrollTop = out.scrollHeight;
    }

    function _appendCommandEcho(cmd) {
        const select = document.getElementById('cli-sim-agent');
        const name = select?.selectedOptions[0]?.text || 'agent';
        _appendOutput('text-gray-200', `<span class="text-emerald-400 font-semibold">${esc(name)} $</span> ${esc(cmd)}`);
    }

    async function _executeSimCommand(cmd, { execute = false } = {}) {
        // Unwrap bm_cli("...") / bm_cli('...') wrapper if the user types it
        const wrapMatch = cmd.match(/^bm_cli\s*\(\s*["'](.+?)["']\s*\)$/);
        if (wrapMatch) cmd = wrapMatch[1];

        const agentId = document.getElementById('cli-sim-agent')?.value;
        _appendCommandEcho(cmd);

        if (!agentId) {
            _simLine('red', 'No agent selected. Choose an agent from the dropdown above.');
            _simBlank();
            return;
        }

        simRunning = true;
        const loadingEl = document.createElement('div');
        loadingEl.className = 'text-gray-600 animate-pulse';
        loadingEl.textContent = execute ? 'executing...' : 'dry-run...';
        const out = document.getElementById('cli-sim-output');
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
                _simLine('red', `Error: ${esc(data.detail || 'Request failed')}`);
                _simBlank();
                return;
            }

            // Render result based on kind
            _renderExecutionResult(data);
        } catch (err) {
            if (loadingEl.parentNode) loadingEl.remove();
            _simLine('red', `Network error: ${esc(err.message)}`);
        }
        simRunning = false;
        _simBlank();
    }

    function _renderExecutionResult(data) {
        // ── Status banner ──
        if (data.dry_run && data.ok) {
            _appendOutput(
                'bg-sky-500/10 text-sky-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-sky-500/20',
                `&#9711; DRY RUN — ${esc(data.kind)} (${esc(data.executor)}) — no files or shell`
            );
        } else if (data.ok) {
            _appendOutput(
                'bg-emerald-500/10 text-emerald-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-emerald-500/20',
                `&#10003; OK — ${esc(data.kind)} (${esc(data.executor)})`
            );
        } else if (data.approval_required) {
            _appendOutput(
                'bg-amber-500/10 text-amber-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-amber-500/20',
                `&#9888; APPROVAL REQUIRED — this command is gated behind human approval`
            );
        } else {
            _appendOutput(
                'bg-red-500/10 text-red-400 px-3 py-1.5 rounded-md text-xs font-medium mt-1 border border-red-500/20',
                `&#10007; BLOCKED — exit ${data.exit_code} (${esc(data.kind)})`
            );
        }

        // ── Approval detail ──
        if (data.approval_required) {
            _simLine('amber', `In a real agent turn, the turn would pause here until you approve or reject.`);
            if (data.approval_request_id) {
                _simLine('dim', `Approval request: ${esc(data.approval_request_id)}`);
            }
            _simLine('dim', `Go to the Approvals tab to manage pending requests.`);
            return;
        }

        // ── Detail message (for errors/blocks) ──
        if (!data.ok && data.detail) {
            _simLine('red', esc(data.detail));
        }

        // ── Output content ──
        const output = data.output || '';
        if (output.trim()) {
            const lines = output.split('\n');
            for (const line of lines) {
                // Skip BossMod wrapper header lines
                if (line.startsWith('BOSSMOD CLI RESULT') || line.startsWith('command:')) continue;
                if (!line.trim()) continue;

                if (line.match(/^[A-Z][A-Z ]+:$/)) {
                    // Section header (STDOUT:, STDERR:, ERROR:, etc.)
                    const label = line.replace(/:$/, '');
                    const labelColor = label === 'ERROR' || label === 'STDERR'
                        ? 'text-red-400' : 'text-gray-400';
                    _appendOutput(`${labelColor} text-xs uppercase tracking-wider font-semibold mt-2`, label);
                } else {
                    _simLine('text', esc(line));
                }
            }
        }

        // ── Matched rule hint ──
        if (data.matched_rule_id) {
            const rule = rulesCache.find(r => r.id === data.matched_rule_id);
            if (rule) {
                _simLine('dim', `matched rule: "${esc(rule.pattern)}" (${esc(rule.match_mode)}) — ${esc(rule.description || rule.tier)}`);
            }
        }

        // ── CWD ──
        if (data.cwd) {
            _simLine('dim', `cwd: ${esc(data.cwd)}`);
        }
    }

    return { render };
})();
