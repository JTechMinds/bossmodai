/**
 * BossMod AI — the CLI simulator's terminal chrome.
 *
 * The controls bar (run-as, the two policy badges, Execute-for-real, Clear)
 * and the terminal frame it sits above. Pure markup: it binds nothing and
 * knows nothing about running a command, which is what lets simulator.js be
 * read as orchestration rather than as a wall of Tailwind.
 *
 * Split out in Phase 4 for the same reason Phase 3C split the output renderer
 * and the run path out of the same file: ~85 lines of chrome is what pushed
 * simulator.js over the cap once its load path grew a real error state.
 *
 * MARKUP EXEMPTION: settings/ builds DOM with template strings (spec 6.7).
 */
const BossModSimulatorShell = (() => {

    /**
     * Build the terminal shell.
     *
     * @param {object} view
     * @param {string} view.agentOptions      Pre-escaped <option> markup.
     * @param {boolean} view.shellEnabled
     * @param {string} view.defaultPolicy
     * @returns {string}
     */
    function terminalMarkup(view) {
        const { agentOptions, shellEnabled, defaultPolicy } = view;
        const shellBadge = shellEnabled
            ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">SHELL ON</span>'
            : '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-500/15 text-red-400 border border-red-500/20">SHELL OFF</span>';

        const policyBadge = defaultPolicy === 'approval_required'
            ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/15 text-amber-400 border border-amber-500/20">DEFAULT: APPROVAL</span>'
            : '<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-500/15 text-slate-400 border border-slate-500/20">DEFAULT: DENY</span>';

        return `
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
    }

    return { terminalMarkup };
})();
