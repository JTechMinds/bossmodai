/**
 * BossMod AI — CLI Policy Virtual Commands tab.
 *
 * Read-only reference for the built-in commands every agent has without a
 * policy rule or shell access, grouped by the categories the backend returns.
 * Ported from cli-policy-section.js unchanged.
 */
const BossModCliPolicyVirtual = (() => {
    const esc = BossModFormat.escapeHtml;
    const { icons } = BossModCliPolicyShared;

    /**
     * Render the Virtual Commands reference.
     *
     * @param {Element} el  The tab content element.
     * @returns {Promise<void>} A failed load leaves an error line; there is
     *   nothing to bind, the tab is read-only.
     */
    async function renderVirtualCommandsTab(el) {
        let data = { commands: [], categories: [] };
        try {
            const res = await apiFetch('/api/cli-policy/virtual-commands');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            data = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load virtual commands.</p>';
            return;
        }

        const byCategory = {};
        for (const cmd of data.commands) {
            if (!byCategory[cmd.category]) byCategory[cmd.category] = [];
            byCategory[cmd.category].push(cmd);
        }

        const catOrder = data.categories.map(c => c.name);
        const catDescriptions = {};
        for (const c of data.categories) catDescriptions[c.name] = c.description;

        let html = `
            <div class="mb-5">
                <div class="flex items-start gap-3 p-4 bg-blue-500/5 border border-blue-500/20 rounded-xl">
                    <i data-lucide="info" class="w-5 h-5 text-blue-400 shrink-0 mt-0.5"></i>
                    <div class="text-sm">
                        <p class="font-medium mb-1">Built-in virtual commands</p>
                        <p class="text-xs text-bm-muted">Always available to all agents. These run inside the BossMod virtual environment — no shell access or policy rules required.</p>
                    </div>
                </div>
            </div>`;

        for (const cat of catOrder) {
            const cmds = byCategory[cat];
            if (!cmds || cmds.length === 0) continue;
            const desc = catDescriptions[cat] || '';

            html += `
                <div class="mb-5">
                    <h3 class="text-sm font-semibold capitalize mb-2 flex items-center gap-2">
                        ${esc(cat)}
                        <span class="text-xs text-bm-muted font-normal">${esc(desc)}</span>
                    </h3>
                    <div class="space-y-1.5">`;

            for (const cmd of cmds) {
                html += `
                        <details class="group border border-bm-border rounded-lg bg-white overflow-hidden">
                            <summary class="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-slate-50 transition-colors">
                                <code class="text-sm font-mono font-semibold text-bm-accent">${esc(cmd.name)}</code>
                                <span class="text-xs text-bm-muted flex-1">${esc(cmd.description)}</span>
                                <code class="text-xs text-bm-muted font-mono hidden sm:inline">${esc(cmd.usage_syntax)}</code>
                                <i data-lucide="chevron-down" class="w-4 h-4 text-bm-muted transition-transform group-open:rotate-180"></i>
                            </summary>
                            <div class="px-4 py-3 border-t border-bm-border bg-bm-bg/50">
                                <div class="text-xs mb-2">
                                    <span class="font-semibold">Usage:</span>
                                    <code class="ml-1 font-mono">${esc(cmd.usage_syntax)}</code>
                                </div>
                                <pre class="text-xs text-bm-muted whitespace-pre-wrap font-mono leading-relaxed">${esc(cmd.help_text)}</pre>
                            </div>
                        </details>`;
            }

            html += '</div></div>';
        }

        el.innerHTML = html;
        icons(el);
    }

    return { renderVirtualCommandsTab };
})();
