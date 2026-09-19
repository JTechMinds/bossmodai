/**
 * BossMod AI — Settings → Nest git (self-host remotes).
 *
 * One Settings store. Host Enable only flips On after a Shell probe.
 * PAT/SSH use the same bm1 wrap as API keys. Always-allow does not skip auth.
 */

const NestGitSection = (() => {
    async function render(el, options) {
        let status;
        try {
            const res = await apiFetch('/api/nest-git/status');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            status = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Failed to load Nest git settings.</p>';
            return;
        }

        const on = !!status.host_enabled;
        const how = status.how_to || '';
        el.innerHTML = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">Nest git</h2>
                <p class="text-sm text-bm-muted mt-0.5">
                    Credentials the Shell can see for nest remotes (typically push).
                    Always-allow on a command does not skip auth.
                    Browser or desktop GitHub login is not the agent's.
                </p>
            </div>
            <div class="max-w-lg space-y-5">
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-semibold">Enable host git</h3>
                            <p class="text-xs text-bm-muted mt-0.5">
                                On only after a credential helper or SSH agent is visible to Shell.
                            </p>
                        </div>
                        <button id="btn-toggle-nest-git" type="button"
                                class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors
                                       ${on ? 'bg-bm-accent' : 'bg-slate-300'}"
                                role="switch" aria-checked="${on ? 'true' : 'false'}">
                            <span class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform
                                         ${on ? 'translate-x-6' : 'translate-x-1'}"></span>
                        </button>
                    </div>
                    <p id="nest-git-probe" class="text-xs mt-3 ${status.probe_ok ? 'text-emerald-700' : 'text-bm-muted'}">
                        ${status.probe_ok
                            ? `Probe: visible via ${BossModFormat.escapeHtml(status.probe_via || 'host')}.`
                            : BossModFormat.escapeHtml(status.probe_why || 'Host git is not visible to Shell')}
                    </p>
                </div>
                <div class="rounded-lg border border-bm-border bg-slate-50/70 p-4">
                    <label class="block text-sm font-medium mb-1">PAT</label>
                    <p class="text-xs text-bm-muted mb-1.5">
                        Stored with the same wrap as API keys. Prefer bot identity for attribution.
                        ${status.has_pat ? `Saved (last 4: ${BossModFormat.escapeHtml(status.pat_last4 || '')}). Leave blank to keep.` : 'Not saved.'}
                    </p>
                    <input type="password" id="nest-git-pat" data-focus="pat" value=""
                           placeholder="${status.has_pat ? '••••' + BossModFormat.escapeAttribute(status.pat_last4 || '') : 'ghp_…'}"
                           class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono">
                    <div class="flex gap-2 mt-2">
                        <button type="button" id="nest-git-pat-save" class="hpc-action hpc-action-primary text-sm">Save PAT</button>
                        <button type="button" id="nest-git-pat-clear" class="hpc-action text-sm" ${status.has_pat ? '' : 'disabled'}>Clear</button>
                    </div>
                </div>
                <div class="rounded-lg border border-bm-border bg-slate-50/70 p-4">
                    <label class="block text-sm font-medium mb-1">SSH private key</label>
                    <p class="text-xs text-bm-muted mb-1.5">
                        Written 0600 for Shell. ${status.has_ssh ? `Saved (last 4: ${BossModFormat.escapeHtml(status.ssh_last4 || '')}).` : 'Not saved.'}
                    </p>
                    <textarea id="nest-git-ssh" rows="4"
                              class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono"
                              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea>
                    <div class="flex gap-2 mt-2">
                        <button type="button" id="nest-git-ssh-save" class="hpc-action hpc-action-primary text-sm">Save SSH</button>
                        <button type="button" id="nest-git-ssh-clear" class="hpc-action text-sm" ${status.has_ssh ? '' : 'disabled'}>Clear</button>
                    </div>
                </div>
                <p class="text-xs text-bm-muted">${BossModFormat.escapeHtml(how)}</p>
                <div id="nest-git-status" class="hidden p-3 rounded-lg text-sm"></div>
            </div>`;
        BossModIcons.paint(el, 'settings-nest-git');
        bind(el, status);
        const focus = options && options.focus;
        if (focus) el.querySelector(`[data-focus="${focus}"]`)?.focus();
    }

    function bind(el, status) {
        el.querySelector('#btn-toggle-nest-git').addEventListener('click', async () => {
            const btn = el.querySelector('#btn-toggle-nest-git');
            const was = btn.getAttribute('aria-checked') === 'true';
            const next = was ? 'false' : 'true';
            try {
                const res = await apiFetch(
                    `/api/settings/nest_git_host_enabled?value=${encodeURIComponent(next)}&category=nest_git`,
                    { method: 'PUT' },
                );
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    showStatus(el, err.detail || 'Host git is not visible to Shell.', 'error');
                    return;
                }
                render(el);
            } catch {
                showStatus(el, 'Failed to update host Enable.', 'error');
            }
        });
        el.querySelector('#nest-git-pat-save').addEventListener('click', () => saveSecret(el, 'pat'));
        el.querySelector('#nest-git-ssh-save').addEventListener('click', () => saveSecret(el, 'ssh_key'));
        el.querySelector('#nest-git-pat-clear').addEventListener('click', () => clearSecret(el, 'clear_pat'));
        el.querySelector('#nest-git-ssh-clear').addEventListener('click', () => clearSecret(el, 'clear_ssh'));
        void status;
    }

    async function saveSecret(el, field) {
        const input = el.querySelector(field === 'pat' ? '#nest-git-pat' : '#nest-git-ssh');
        const value = (input && input.value) || '';
        if (!value.trim()) {
            showStatus(el, 'Add a PAT or an SSH key. Empty does not enable nest git.', 'error');
            return;
        }
        try {
            const res = await apiFetch('/api/nest-git/credentials', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ [field]: value }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showStatus(el, err.detail || 'Failed to save.', 'error');
                return;
            }
            if (input) input.value = '';
            render(el);
        } catch {
            showStatus(el, 'Failed to save.', 'error');
        }
    }

    async function clearSecret(el, flag) {
        try {
            const res = await apiFetch('/api/nest-git/credentials', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ [flag]: true }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showStatus(el, err.detail || 'Failed to clear.', 'error');
                return;
            }
            render(el);
        } catch {
            showStatus(el, 'Failed to clear.', 'error');
        }
    }

    function showStatus(root, message, type) {
        const el = root.querySelector('#nest-git-status');
        if (!el) return;
        el.classList.remove('hidden');
        const colors = {
            success: 'bg-emerald-50 border border-emerald-200 text-emerald-700',
            error: 'bg-red-50 border border-red-200 text-red-700',
            info: 'bg-blue-50 border border-blue-200 text-blue-700',
        };
        el.className = 'p-3 rounded-lg text-sm ' + (colors[type] || colors.info);
        el.textContent = message;
    }

    return { render };
})();
