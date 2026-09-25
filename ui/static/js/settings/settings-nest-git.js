/**
 * BossMod AI — Settings → Nest git (self-host remotes).
 *
 * Named credentials (label + remote match). Host Enable only flips On after
 * a Shell probe. Token/SSH use the same bm1 wrap as API keys. Approving a
 * command once does not skip auth.
 */

const NestGitSection = (() => {
    async function render(el, options) {
        let status;
        try {
            const res = await apiFetch('/api/nest-git/status');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            status = await res.json();
        } catch {
            el.innerHTML = '<p class="text-red-500 text-sm">Couldn’t load Nest git settings.</p>';
            return;
        }

        const on = !!status.host_enabled;
        const how = status.how_to || '';
        const matchHow = status.match_how_to || '';
        const creds = Array.isArray(status.credentials) ? status.credentials : [];
        el.innerHTML = `
            <div class="mb-6">
                <h2 class="text-lg font-semibold">Nest git</h2>
                <p class="text-sm text-bm-muted mt-0.5">
                    Your computer’s GitHub login isn’t shared with agents.
                    Paste a GitHub access token (a special password from GitHub → Settings → Developer settings),
                    or an SSH key if you use those. Saved once here.
                    Approving a command once doesn’t skip this.
                </p>
            </div>
            <div class="max-w-lg space-y-5">
                <div class="border border-bm-border rounded-lg p-4 bg-white">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-semibold">Use this computer’s Git login</h3>
                            <p class="text-xs text-bm-muted mt-0.5">
                                Turns on only if Git on this computer already has a login the agent can use.
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
                            ? `Git on this computer is ready (${BossModFormat.escapeHtml(status.probe_via || 'host')}).`
                            : BossModFormat.escapeHtml(status.probe_why || 'Host git is not visible to Shell')}
                    </p>
                </div>
                <div>
                    <h3 class="text-sm font-semibold mb-1">Credentials</h3>
                    <p class="text-xs text-bm-muted mb-3">
                        Name each login and match it to a remote — <span class="font-mono">github.com/Org/*</span>
                        or <span class="font-mono">github.com/Org/repo</span>.
                        A fine-grained token’s Resource owner is the org that owns the repo (Contents Read and write).
                        Need one key for everything? Use a classic repo token. If the org uses SAML, open Configure SSO.
                    </p>
                    <div id="nest-git-cred-list" class="space-y-3">${creds.map(credentialCard).join('') || emptyList()}</div>
                </div>
                <div class="rounded-lg border border-bm-border bg-slate-50/70 p-4" data-credential-form="new">
                    <h3 class="text-sm font-semibold mb-2">Add a credential</h3>
                    <label class="block text-sm font-medium mb-1">Name</label>
                    <input type="text" id="nest-git-label" value=""
                           placeholder="Name"
                           class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white mb-3">
                    <label class="block text-sm font-medium mb-1">Remote match</label>
                    <input type="text" id="nest-git-match" value=""
                           placeholder="github.com/Org/* or github.com/Org/repo"
                           class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono mb-3">
                    <label class="flex items-center gap-2 text-xs text-bm-muted mb-3">
                        <input type="checkbox" id="nest-git-default" data-nest-default ${creds.some((item) => item.is_default) ? '' : 'checked'}>
                        Use for remotes that don’t match another credential
                    </label>
                    <label class="block text-sm font-medium mb-1">GitHub access token</label>
                    <p class="text-xs text-bm-muted mb-1.5">
                        A special password from GitHub → Settings → Developer settings.
                    </p>
                    <div class="flex gap-2">
                        <input type="text" id="nest-git-pat" data-focus="pat" data-credential-field="pat" data-secret-field="pat"
                               autocomplete="off" spellcheck="false" autocapitalize="off"
                               placeholder="GitHub access token"
                               class="setting-input flex-1 px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono bm-secret-masked">
                        <button type="button" id="nest-git-pat-toggle" data-secret-toggle="#nest-git-pat"
                                class="hpc-action text-sm" aria-pressed="false">Show</button>
                    </div>
                    <label class="block text-sm font-medium mb-1">SSH key (optional)</label>
                    <textarea id="nest-git-ssh" data-credential-field="ssh" rows="4"
                              class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono"
                              placeholder="SSH key (optional)"></textarea>
                    <div class="flex gap-2 mt-4">
                        <button type="button" id="nest-git-save" class="btn btn-primary btn-sm">Save</button>
                    </div>
                </div>
                <p class="text-xs text-bm-muted">${BossModFormat.escapeHtml(how)}</p>
                <p class="text-xs text-bm-muted">${BossModFormat.escapeHtml(matchHow)}</p>
                <div id="nest-git-status" class="hidden p-3 rounded-lg text-sm"></div>
            </div>`;
        BossModIcons.paint(el, 'settings-nest-git');
        bind(el, status);
        const focus = options && options.focus;
        if (focus) el.querySelector(`[data-focus="${focus}"]`)?.focus();
    }

    function emptyList() {
        return '<p class="text-xs text-bm-muted">No credentials saved yet.</p>';
    }

    function credentialCard(item) {
        const label = BossModFormat.escapeHtml(item.label || item.id || '');
        const match = BossModFormat.escapeHtml(item.match || 'unmatched remotes (default)');
        const badge = item.is_default ? '<span class="text-xs text-emerald-700 ml-2">default</span>' : '';
        const pat = item.has_pat
            ? `Token saved (last 4: ${BossModFormat.escapeHtml(item.pat_last4 || '')}).`
            : 'No token.';
        const ssh = item.has_ssh
            ? `SSH saved (last 4: ${BossModFormat.escapeHtml(item.ssh_last4 || '')}).`
            : 'No SSH key.';
        return `
            <div class="border border-bm-border rounded-lg p-3 bg-white" data-cred-id="${BossModFormat.escapeAttribute(item.id || '')}">
                <div class="flex items-start justify-between gap-2">
                    <div>
                        <p class="text-sm font-medium">${label}${badge}</p>
                        <p class="text-xs font-mono text-bm-muted mt-0.5">${match}</p>
                        <p class="text-xs text-bm-muted mt-1">${pat} ${ssh}</p>
                    </div>
                    <div class="flex flex-col gap-1">
                        <button type="button" class="hpc-action text-xs nest-git-edit" data-id="${BossModFormat.escapeAttribute(item.id || '')}">Edit</button>
                        <button type="button" class="hpc-action text-xs nest-git-remove" data-id="${BossModFormat.escapeAttribute(item.id || '')}">Remove</button>
                    </div>
                </div>
                <div class="hidden nest-git-edit-form mt-3 space-y-2">
                    <input type="text" class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg nest-edit-label"
                           value="${BossModFormat.escapeAttribute(item.label || '')}" placeholder="Name">
                    <input type="text" class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg font-mono nest-edit-match"
                           value="${BossModFormat.escapeAttribute(item.match || '')}" placeholder="github.com/Org/* or github.com/Org/repo">
                    <div class="flex gap-2">
                        <input type="text" data-credential-field="pat" data-secret-field="pat"
                               autocomplete="off" spellcheck="false" autocapitalize="off"
                               class="setting-input flex-1 px-3 py-2 text-sm border border-bm-border rounded-lg font-mono nest-edit-pat bm-secret-masked"
                               placeholder="${item.has_pat ? '••••' + BossModFormat.escapeAttribute(item.pat_last4 || '') : 'GitHub access token'}">
                        <button type="button" data-secret-toggle=".nest-edit-pat" class="hpc-action text-xs" aria-pressed="false">Show</button>
                    </div>
                    <textarea rows="3" data-credential-field="ssh"
                              class="setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg font-mono nest-edit-ssh"
                              placeholder="SSH key (optional)"></textarea>
                    <label class="flex items-center gap-2 text-xs text-bm-muted">
                        <input type="checkbox" class="nest-edit-default" data-nest-default ${item.is_default ? 'checked' : ''}>
                        Use for remotes that don’t match another credential
                    </label>
                    <button type="button" class="btn btn-primary btn-sm nest-git-save-edit" data-id="${BossModFormat.escapeAttribute(item.id || '')}">Save</button>
                </div>
            </div>`;
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
                showStatus(el, 'Couldn’t update this computer’s Git login.', 'error');
            }
        });
        const addSave = el.querySelector('#nest-git-save');
        if (addSave) addSave.addEventListener('click', () => saveNew(el));
        el.querySelectorAll('[data-secret-field]').forEach((input) => BossModSecretField.bind(input));
        el.querySelectorAll('[data-secret-toggle]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const selector = btn.getAttribute('data-secret-toggle');
                const scope = selector.charAt(0) === '#' ? el : (btn.closest('[data-cred-id]') || el);
                BossModSecretField.toggle(scope.querySelector(selector), btn);
            });
        });
        bindDefaultToggles(el);
        el.querySelectorAll('.nest-git-remove').forEach((btn) => {
            btn.addEventListener('click', () => removeItem(el, btn.getAttribute('data-id')));
        });
        el.querySelectorAll('.nest-git-edit').forEach((btn) => {
            btn.addEventListener('click', () => {
                const card = btn.closest('[data-cred-id]');
                const form = card && card.querySelector('.nest-git-edit-form');
                if (form) form.classList.toggle('hidden');
            });
        });
        el.querySelectorAll('.nest-git-save-edit').forEach((btn) => {
            btn.addEventListener('click', () => saveEdit(el, btn));
        });
        void status;
    }

    function bindDefaultToggles(root) {
        root.querySelectorAll('[data-nest-default]').forEach((box) => {
            box.addEventListener('change', () => {
                if (!box.checked) return;
                root.querySelectorAll('[data-nest-default]').forEach((other) => {
                    if (other !== box) other.checked = false;
                });
            });
        });
    }

    /**
     * One unmatched-remotes default. A second check clears the others in the
     * form. Saving a card that lost its check to that sibling does not clear
     * the stored default — the checked card’s Save is what claims it.
     */
    function editDefaultFlag(box, root) {
        if (!box) return undefined;
        if (box.checked) return true;
        const others = root.querySelectorAll('[data-nest-default]');
        for (let index = 0; index < others.length; index += 1) {
            if (others[index] !== box && others[index].checked) return undefined;
        }
        return false;
    }

    async function saveNew(el) {
        const form = el.querySelector('[data-credential-form="new"]') || el;
        hideStatus(el);
        const secrets = BossModNestGitCredentialForm.payloadForSave(form, { requireSecret: true });
        if (secrets.blocked) return;
        const { ssh: sshDraft } = BossModNestGitCredentialForm.read(form);
        const sshLeftInvalid = Boolean(sshDraft) && !secrets.ssh_key;
        const label = (form.querySelector('#nest-git-label') && form.querySelector('#nest-git-label').value) || '';
        const match = (form.querySelector('#nest-git-match') && form.querySelector('#nest-git-match').value) || '';
        const defaultBox = form.querySelector('#nest-git-default');
        const asDefault = Boolean(defaultBox && defaultBox.checked);
        const named = Boolean(label.trim() || match.trim());
        const secretBody = { pat: secrets.pat, ssh_key: secrets.ssh_key };
        const body = named
            ? Object.assign(
                { label: label.trim() || 'GitHub', match: match.trim(), is_default: asDefault },
                secretBody,
            )
            : secretBody;
        try {
            const res = await apiFetch(named ? '/api/nest-git/items' : '/api/nest-git/credentials', {
                method: named ? 'POST' : 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showStatus(el, err.detail || 'Couldn’t save.', 'error');
                return;
            }
            const patInput = form.querySelector('[data-credential-field="pat"]');
            const sshInput = form.querySelector('[data-credential-field="ssh"]');
            if (secrets.pat) BossModSecretField.clear(patInput);
            if (secrets.ssh_key && sshInput) sshInput.value = '';
            if (!sshLeftInvalid) render(el);
        } catch {
            showStatus(el, 'Couldn’t save.', 'error');
        }
    }

    async function saveEdit(el, btn) {
        const card = btn.closest('[data-cred-id]');
        if (!card) return;
        hideStatus(el);
        const id = btn.getAttribute('data-id');
        const label = (card.querySelector('.nest-edit-label') || {}).value || '';
        const match = (card.querySelector('.nest-edit-match') || {}).value || '';
        const secrets = BossModNestGitCredentialForm.payloadForSave(card, { requireSecret: false });
        if (secrets.blocked) return;
        const { ssh: sshDraft } = BossModNestGitCredentialForm.read(card);
        const sshLeftInvalid = Boolean(sshDraft) && !secrets.ssh_key;
        const isDefault = editDefaultFlag(card.querySelector('[data-nest-default]'), el);
        try {
            const res = await apiFetch(`/api/nest-git/items/${encodeURIComponent(id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    label,
                    match,
                    pat: secrets.pat || undefined,
                    ssh_key: secrets.ssh_key || undefined,
                    is_default: isDefault,
                }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showStatus(el, err.detail || 'Couldn’t save.', 'error');
                return;
            }
            const patInput = card.querySelector('[data-credential-field="pat"]');
            const sshInput = card.querySelector('[data-credential-field="ssh"]');
            if (secrets.pat) BossModSecretField.clear(patInput);
            if (secrets.ssh_key && sshInput) sshInput.value = '';
            if (!sshLeftInvalid) render(el);
        } catch {
            showStatus(el, 'Couldn’t save.', 'error');
        }
    }

    async function removeItem(el, id) {
        if (!id) return;
        try {
            const res = await apiFetch(`/api/nest-git/items/${encodeURIComponent(id)}`, { method: 'DELETE' });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                showStatus(el, err.detail || 'Couldn’t remove.', 'error');
                return;
            }
            render(el);
        } catch {
            showStatus(el, 'Couldn’t remove.', 'error');
        }
    }

    function hideStatus(root) {
        const el = root.querySelector('#nest-git-status');
        if (!el) return;
        el.classList.add('hidden');
        el.textContent = '';
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
