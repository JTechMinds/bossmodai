/**
 * Nest git in-thread card — GitHub token / SSH key, or use this computer’s Git login.
 * Writes the one Settings store. Loaded before consent-card.js.
 */
const BossModNestGitCard = (() => {
    function isNestGitCard(card) {
        return Boolean(card && (card.kind || card.card_kind) === 'nest_git');
    }

    function title(card) {
        if (card && card.title) return card.title;
        const name = (card && (card.agent_name || card.agentName)) || '';
        return name
            ? `${name} needs permission to push to GitHub`
            : 'needs permission to push to GitHub';
    }

    function body(card) {
        return (card && (card.body || card.reason))
            || 'Your computer’s GitHub login isn’t shared with agents. '
            + 'Paste a GitHub access token (a special password from GitHub → Settings → Developer settings), '
            + 'or an SSH key if you use those. Saved once under Settings → Nest git. '
            + 'Approving a command once doesn’t skip this.';
    }

    function hint(card) {
        return (card && card.enable_hint)
            || 'Saved once under Settings → Nest git. Approving a command once doesn’t skip this.';
    }

    function errorText(card) {
        return (card && card.error) || '';
    }

    function actions(card) {
        return [
            {
                label: (card && card.enable_label) || 'Use this computer’s Git login',
                path: 'enable',
                primary: true,
            },
            {
                label: (card && card.add_label) || 'Add a GitHub access token or SSH key',
                path: 'credentials',
            },
        ];
    }

    function statusLabel(card) {
        return (card && card.decision_note) || 'GitHub permission saved under Settings → Nest git.';
    }

    function showCredentialsForm(container, card, actionsEl, api, afterSave) {
        if (typeof api !== 'function') throw new Error('[consent-card] api is required');
        actionsEl.replaceChildren();
        const pat = document.createElement('input');
        pat.type = 'password';
        pat.placeholder = 'GitHub access token';
        pat.setAttribute('aria-label', 'GitHub access token');
        pat.className = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono mb-2';
        const ssh = document.createElement('textarea');
        ssh.rows = 3;
        ssh.placeholder = 'SSH key (optional)';
        ssh.setAttribute('aria-label', 'SSH key (optional)');
        ssh.className = 'setting-input w-full px-3 py-2 text-sm border border-bm-border rounded-lg bg-white font-mono mb-2';
        const row = document.createElement('div');
        row.className = 'host-path-consent-actions';
        const save = document.createElement('button');
        save.type = 'button';
        save.className = 'hpc-action hpc-action-primary';
        save.textContent = 'Save';
        const settings = document.createElement('button');
        settings.type = 'button';
        settings.className = 'hpc-action';
        settings.textContent = 'Open Nest git settings';
        settings.addEventListener('click', () => {
            if (typeof SettingsView !== 'undefined' && SettingsView.open) {
                SettingsView.open('nest-git', { focus: 'pat' });
            }
        });
        save.addEventListener('click', async () => {
            save.disabled = true;
            settings.disabled = true;
            try {
                const res = await api(`/api/nest-git/${card.id}/credentials`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pat: pat.value, ssh_key: ssh.value }),
                });
                if (!res.ok) throw new Error((await res.text()) || 'Consent update failed.');
                const updated = await res.json();
                pat.value = '';
                ssh.value = '';
                afterSave(updated);
            } catch (err) {
                save.disabled = false;
                settings.disabled = false;
                const note = document.createElement('div');
                note.className = 'hpc-status';
                note.textContent = err?.message || 'Consent update failed.';
                container.appendChild(note);
            }
        });
        row.appendChild(save);
        row.appendChild(settings);
        actionsEl.appendChild(pat);
        actionsEl.appendChild(ssh);
        actionsEl.appendChild(row);
    }

    return { isNestGitCard, title, body, hint, errorText, actions, statusLabel, showCredentialsForm };
})();
